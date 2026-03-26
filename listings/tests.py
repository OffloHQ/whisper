from datetime import date, timedelta
import re
from urllib.error import URLError
from unittest.mock import MagicMock, patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core import mail
from django.core.management import call_command
from io import StringIO
from django.db import IntegrityError
from django.test import RequestFactory
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from django.test.utils import override_settings
from django.core.exceptions import ValidationError

from services.email import get_email_provider, send_email

from .admin import AccessRequestAdmin
from .checkins import (
    build_signed_listing_token,
    deactivate_stale_listings,
    get_freshness_state_label,
    get_listings_requiring_checkin,
    get_reminder_due_at,
    get_stale_at,
    group_listings_by_agent_email,
    should_send_checkin_for_listing,
    send_grouped_listing_checkins,
)
from .email_flows import build_agent_email_verification_token
from .intake import WAITLIST_TOAST_MESSAGE, approve_access_request, reject_access_request
from .forms import FeedFilterForm, ListingForm
from .models import AccessRequest, AgentEmail, AgentPhone, AgentUser, AuthAccessToken, Collection, CollectionFilter, CollectionItem, EmailNotificationLog, InAppNotification, Listing, SavedListing
from .retention import get_cleanup_querysets
from .utils import format_listing_price, get_town_area_choices
from .verification.schemas import VerificationResult, VerificationStatus
from .verification.utils import normalize_state_code
from .views import CURRENT_AGENT_LOCKED_OUT_KEY, CURRENT_AGENT_SESSION_KEY


def create_agent(*, phone_number="914-555-0101", **kwargs):
    kwargs.setdefault("terms_accepted", True)
    kwargs.setdefault("privacy_accepted", True)
    kwargs.setdefault("terms_accepted_at", timezone.now())
    kwargs.setdefault("privacy_accepted_at", timezone.now())
    kwargs.setdefault("terms_version", "test-terms-v1")
    kwargs.setdefault("privacy_version", "test-privacy-v1")
    agent = AgentUser.objects.create(**kwargs)
    if phone_number:
        AgentPhone.objects.create(agent=agent, phone_number=phone_number)
    return agent


def login_agent(client, agent):
    session = client.session
    session[CURRENT_AGENT_SESSION_KEY] = agent.id
    session[CURRENT_AGENT_LOCKED_OUT_KEY] = False
    session.save()


def build_verification_result(
    *,
    success=True,
    status=VerificationStatus.VERIFIED,
    state="NY",
    submitted_full_name="Beth Acocella",
    submitted_license_number="30AC0961210",
    provider="ny_soda3",
    reason="Verified against the NY licensing dataset.",
    requires_manual_review=False,
    raw_payload=None,
):
    return VerificationResult(
        success=success,
        status=status,
        state=state,
        submitted_full_name=submitted_full_name,
        submitted_license_number=submitted_license_number,
        normalized_submitted_name="beth acocella",
        matched_name="ACOCELLA BETH A" if success else "",
        matched_license_number=submitted_license_number if success else "",
        matched_license_type="ASSOCIATE BROKER" if success else "",
        matched_business_name="COLDWELL BANKER REALTY" if success else "",
        matched_business_city="WHITE PLAINS" if success else "",
        matched_business_state=state if success else "",
        matched_expiration_date=date(2028, 2, 24) if success else None,
        provider=provider,
        raw_payload=raw_payload if raw_payload is not None else {},
        reason=reason,
        requires_manual_review=requires_manual_review,
    )


def build_admin_request(path="/admin/listings/accessrequest/"):
    request = RequestFactory().post(path)
    request.user = get_user_model().objects.create_superuser(
        username=f"admin-{timezone.now().timestamp()}",
        email=f"admin-{timezone.now().timestamp()}@example.com",
        password="password123",
    )
    request.build_absolute_uri = lambda url: f"https://admin.example.com{url}"
    return request


def create_reviewer_user(*, username, permissions):
    user = get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="password123",
    )
    for codename in permissions:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return user


class PostListingViewTests(TestCase):
    def get_valid_payload(self):
        return {
            "city": "Scarsdale",
            "property_type": "Townhouse",
            "beds": 3,
            "baths": "2.5",
            "price_min": 900000,
            "price_max": 950000,
            "stage": Listing.Stage.PREMARKET,
            "description": "Quiet block with finished basement.",
            "seller_direction_certified": "on",
            "agent_compliance_acknowledged": "on",
            "information_accuracy_certified": "on",
        }

    def test_post_listing_assigns_first_agent_and_redirects_to_feed(self):
        first_agent = create_agent(
            name="First Agent",
            email="first@example.com",
            license_number="LIC-001",
        )
        login_agent(self.client, first_agent)
        create_agent(
            name="Second Agent",
            email="second@example.com",
            license_number="LIC-002",
        )

        response = self.client.post(reverse("post_listing"), data=self.get_valid_payload())

        self.assertRedirects(response, reverse("feed"))
        listing = Listing.objects.get()
        self.assertEqual(listing.agent, first_agent)
        self.assertTrue(listing.is_active)
        self.assertEqual(listing.title, "3 Bed / 2.5 Bath in Scarsdale")

    def test_post_listing_without_agents_shows_friendly_message(self):
        response = self.client.post(reverse("post_listing"), data=self.get_valid_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no agent account is available")
        self.assertEqual(Listing.objects.count(), 0)

    def test_post_listing_page_hides_internal_fields_and_reuses_partial(self):
        response = self.client.get(reverse("post_listing"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "name=\"agent\"")
        self.assertNotContains(response, "name=\"is_active\"")
        self.assertNotContains(response, "name=\"title\"")
        self.assertContains(response, "name=\"city\"")
        self.assertContains(response, "name=\"stage\"")
        self.assertContains(response, "Town / Area")
        self.assertContains(response, "Share Opportunity")

    def test_post_listing_page_uses_controlled_town_area_choices(self):
        response = self.client.get(reverse("post_listing"))

        self.assertContains(response, "<option value=\"Scarsdale\">Scarsdale</option>", html=False)
        self.assertNotContains(response, "type=\"text\" name=\"city\"", html=False)

    def test_post_listing_requires_agent_phone_with_friendly_message(self):
        agent = create_agent(
            name="Phone Missing Agent",
            email="phone-missing@example.com",
            license_number="LIC-NOPHONE",
            phone_number=None,
        )
        login_agent(self.client, agent)

        response = self.client.post(reverse("post_listing"), data=self.get_valid_payload(), follow=True)

        self.assertRedirects(response, reverse("account") + "#contact-settings", fetch_redirect_response=False)
        self.assertEqual(Listing.objects.count(), 0)

    def test_premarket_listing_fails_without_seller_direction_certification(self):
        agent = create_agent(
            name="Premarket Seller Cert Agent",
            email="premarket-seller-cert@example.com",
            license_number="LIC-PRE-SELLER",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload.pop("seller_direction_certified")

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You must certify seller direction before posting.")
        self.assertEqual(Listing.objects.count(), 0)

    def test_premarket_listing_fails_without_agent_acknowledgment(self):
        agent = create_agent(
            name="Premarket Compliance Agent",
            email="premarket-compliance@example.com",
            license_number="LIC-PRE-COMPLIANCE",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload.pop("agent_compliance_acknowledged")

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You must acknowledge compliance responsibility before posting.")
        self.assertEqual(Listing.objects.count(), 0)

    def test_premarket_listing_fails_without_accuracy_certification(self):
        agent = create_agent(
            name="Premarket Accuracy Agent",
            email="premarket-accuracy@example.com",
            license_number="LIC-PRE-ACCURACY",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload.pop("information_accuracy_certified")

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "You must certify that the submitted information is accurate before sharing this opportunity.",
        )
        self.assertEqual(Listing.objects.count(), 0)

    def test_private_listing_fails_without_private_certification(self):
        agent = create_agent(
            name="Private Cert Agent",
            email="private-cert@example.com",
            license_number="LIC-PRIVATE-CERT",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload["stage"] = Listing.Stage.PRIVATE

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You must certify seller direction for a private listing before posting.")
        self.assertEqual(Listing.objects.count(), 0)

    def test_private_listing_succeeds_with_all_required_certifications(self):
        agent = create_agent(
            name="Private Success Agent",
            email="private-success@example.com",
            license_number="LIC-PRIVATE-SUCCESS",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload["stage"] = Listing.Stage.PRIVATE
        payload["private_marketing_certified"] = "on"

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertRedirects(response, reverse("feed"))
        listing = Listing.objects.get(agent=agent)
        self.assertTrue(listing.seller_direction_certified)
        self.assertTrue(listing.agent_compliance_acknowledged)
        self.assertTrue(listing.information_accuracy_certified)
        self.assertTrue(listing.private_marketing_certified)

    def test_post_listing_rejects_description_with_exact_address(self):
        agent = create_agent(
            name="Address Block Agent",
            email="address-block@example.com",
            license_number="LIC-ADDRESS-BLOCK",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload["description"] = "Seller wants a quiet process at 123 Main St."

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Please remove exact property identifiers, direct contact details, and links. Share only high-level opportunity information in Whisper.",
        )
        self.assertEqual(Listing.objects.count(), 0)

    def test_post_listing_rejects_description_with_phone_number(self):
        agent = create_agent(
            name="Phone Block Agent",
            email="phone-block@example.com",
            license_number="LIC-PHONE-BLOCK",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload["description"] = "Text me at 914-555-1212 for the full story."

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please remove exact property identifiers, direct contact details, and links.")
        self.assertEqual(Listing.objects.count(), 0)

    def test_post_listing_rejects_description_with_email_address(self):
        agent = create_agent(
            name="Email Block Agent",
            email="email-block@example.com",
            license_number="LIC-EMAIL-BLOCK",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload["description"] = "Reach me at broker@example.com for details."

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please remove exact property identifiers, direct contact details, and links.")
        self.assertEqual(Listing.objects.count(), 0)

    def test_post_listing_rejects_description_with_url(self):
        agent = create_agent(
            name="URL Block Agent",
            email="url-block@example.com",
            license_number="LIC-URL-BLOCK",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload["description"] = "Full packet here: https://example.com/offer"

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please remove exact property identifiers, direct contact details, and links.")
        self.assertEqual(Listing.objects.count(), 0)

    def test_post_listing_rejects_description_with_mls_reference(self):
        agent = create_agent(
            name="MLS Block Agent",
            email="mls-block@example.com",
            license_number="LIC-MLS-BLOCK",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload["description"] = "Cross-ref MLS# 1234567 before calling."

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please remove exact property identifiers, direct contact details, and links.")
        self.assertEqual(Listing.objects.count(), 0)

    def test_post_listing_sets_certification_timestamps(self):
        agent = create_agent(
            name="Timestamp Agent",
            email="timestamp-agent@example.com",
            license_number="LIC-TIMESTAMP",
        )
        login_agent(self.client, agent)

        response = self.client.post(reverse("post_listing"), data=self.get_valid_payload())

        self.assertRedirects(response, reverse("feed"))
        listing = Listing.objects.get(agent=agent)
        self.assertIsNotNone(listing.seller_direction_certified_at)
        self.assertIsNotNone(listing.agent_compliance_acknowledged_at)
        self.assertIsNotNone(listing.information_accuracy_certified_at)
        self.assertIsNone(listing.private_marketing_certified_at)

    def test_private_listing_sets_private_certification_timestamp(self):
        agent = create_agent(
            name="Private Timestamp Agent",
            email="private-timestamp@example.com",
            license_number="LIC-PRIVATE-TIMESTAMP",
        )
        login_agent(self.client, agent)
        payload = self.get_valid_payload()
        payload["stage"] = Listing.Stage.PRIVATE
        payload["private_marketing_certified"] = "on"

        response = self.client.post(reverse("post_listing"), data=payload)

        self.assertRedirects(response, reverse("feed"))
        listing = Listing.objects.get(agent=agent)
        self.assertIsNotNone(listing.private_marketing_certified_at)


class ListingPhoneValidationTests(TestCase):
    def test_listing_creation_fails_when_agent_has_no_phone(self):
        agent = create_agent(
            name="No Phone Agent",
            email="no-phone@example.com",
            license_number="LIC-NO-PHONE",
            phone_number=None,
        )

        with self.assertRaises(ValidationError) as exc:
            Listing.objects.create(
                agent=agent,
                title="3 Bed / 2.0 Bath in Scarsdale",
                city="Scarsdale",
                property_type="House",
                beds=3,
                baths="2.0",
                price_min=1000000,
                price_max=1200000,
                stage=Listing.Stage.PREMARKET,
                description="Should fail without agent phone.",
            )

        self.assertIn("Agents must have a phone number to post listings.", str(exc.exception))

    def test_listing_creation_succeeds_when_agent_has_phone(self):
        agent = create_agent(
            name="Phone Agent",
            email="has-phone@example.com",
            license_number="LIC-HAS-PHONE",
        )

        listing = Listing.objects.create(
            agent=agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Should save with agent phone.",
        )

        self.assertEqual(listing.agent, agent)


class AccessFlowTests(TestCase):
    @patch("listings.views.send_access_request_signup_email")
    def test_request_access_stores_request_and_sends_signed_signup_email(self, mock_send_signup_email):
        response = self.client.post(reverse("request_access"), {"email": "newagent@example.com"}, follow=True)

        self.assertRedirects(response, reverse("request_access"))
        access_request = AccessRequest.objects.get(email="newagent@example.com")
        self.assertEqual(access_request.status, AccessRequest.Status.LINK_SENT)
        self.assertIsNotNone(access_request.signup_sent_at)
        self.assertTrue(mock_send_signup_email.called)

    @override_settings(DEV_EXPOSE_SIGNUP_LINKS=True)
    @patch("listings.views.send_access_request_signup_email")
    def test_request_access_exposes_dev_signup_link_only_in_dev(self, mock_send_signup_email):
        response = self.client.post(reverse("request_access"), {"email": "devagent@example.com"}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dev Signup Link")
        self.assertContains(response, "/signup/")

    @override_settings(DEV_EXPOSE_SIGNUP_LINKS=False)
    @patch("listings.views.send_access_request_signup_email")
    def test_request_access_does_not_expose_dev_signup_link_when_disabled(self, mock_send_signup_email):
        response = self.client.post(reverse("request_access"), {"email": "prodlike@example.com"}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Dev Signup Link")

    @override_settings(DEBUG=True, DEV_EXPOSE_SIGNUP_LINKS=True)
    @patch("listings.views.send_access_request_signup_email", side_effect=RuntimeError("Emailit API request failed with status 422"))
    def test_request_access_in_debug_logs_warning_and_continues_when_email_send_fails(self, mock_send_signup_email):
        with self.assertLogs("listings.views", level="WARNING") as logs:
            response = self.client.post(reverse("request_access"), {"email": "debugfail@example.com"}, follow=True)

        self.assertRedirects(response, reverse("request_access"))
        access_request = AccessRequest.objects.get(email="debugfail@example.com")
        self.assertEqual(access_request.status, AccessRequest.Status.LINK_SENT)
        self.assertContains(response, "Dev Signup Link")
        self.assertContains(response, "Local email delivery failed. Use the dev signup link below to continue testing.")
        self.assertTrue(any("Request access email delivery failed in DEBUG mode" in entry for entry in logs.output))

    @override_settings(DEBUG=False, DEV_EXPOSE_SIGNUP_LINKS=False)
    @patch("listings.views.send_access_request_signup_email", side_effect=RuntimeError("Emailit API request failed with status 422"))
    def test_request_access_in_production_still_raises_when_email_send_fails(self, mock_send_signup_email):
        with self.assertRaises(RuntimeError):
            self.client.post(reverse("request_access"), {"email": "prodfail@example.com"})

    @patch("listings.views.VerificationService.verify_license")
    def test_signup_identity_failure_flags_manual_review(self, mock_verify_license):
        access_request = AccessRequest.objects.create(email="review@example.com")
        from .email_flows import build_access_request_signup_token

        mock_verify_license.return_value = build_verification_result(
            success=False,
            status=VerificationStatus.PROVIDER_ERROR,
            reason="Timeout while reaching provider.",
            requires_manual_review=True,
        )
        response = self.client.post(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            {
                "full_name": "Review Agent",
                "state": "NY",
                "license_number": "bad",
            },
        )

        self.assertEqual(response.status_code, 200)
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.MANUAL_REVIEW)
        self.assertEqual(access_request.queue_type, AccessRequest.QueueType.MANUAL_REVIEW)
        self.assertEqual(access_request.reason_code, AccessRequest.Reason.PROVIDER_ERROR)
        self.assertEqual(access_request.verification_status, AccessRequest.VerificationStatus.PROVIDER_ERROR)
        self.assertTrue(access_request.requires_manual_review)
        agent = AgentUser.objects.get(email="review@example.com")
        self.assertEqual(agent.signup_status, AgentUser.SignupStatus.MANUAL_REVIEW)
        self.assertFalse(agent.is_active)
        self.assertEqual(agent.state, "NY")
        self.assertContains(
            response,
            "We’re having trouble verifying your license right now. A teammate will be in touch shortly.",
        )

    @patch("listings.views.VerificationService.verify_license")
    def test_signup_identity_duplicate_license_uses_normalized_comparison(self, mock_verify_license):
        access_request = AccessRequest.objects.create(email="duplicate-license@example.com")
        from .email_flows import build_access_request_signup_token

        create_agent(
            name="Existing License Holder",
            email="existing-license@example.com",
            license_number="30AC0961210",
            is_verified=True,
        )
        mock_verify_license.return_value = build_verification_result(
            success=True,
            submitted_license_number="30AC0961210",
        )

        response = self.client.post(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            {
                "full_name": "Duplicate License",
                "state": "NY",
                "license_number": "30ac-0961210",
            },
        )

        self.assertEqual(response.status_code, 200)
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.MANUAL_REVIEW)
        self.assertEqual(access_request.reason_code, AccessRequest.Reason.DUPLICATE_LICENSE)
        self.assertContains(
            response,
            "We’re having trouble verifying your license right now. A teammate will be in touch shortly.",
        )

    @patch("listings.views.waitlist_access_request")
    @patch("listings.views.VerificationService.verify_license")
    def test_signup_identity_unsupported_state_routes_to_waitlist(self, mock_verify_license, mock_waitlist_access_request):
        access_request = AccessRequest.objects.create(email="waitlist@example.com")
        from .email_flows import build_access_request_signup_token

        mock_verify_license.return_value = build_verification_result(
            success=False,
            status=VerificationStatus.UNSUPPORTED_STATE,
            state="CA",
            reason="Automated verification is not available for CA.",
            requires_manual_review=True,
        )

        response = self.client.post(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            {
                "full_name": "Wait List Agent",
                "state": "CA",
                "license_number": "CA-12345",
            },
        )

        self.assertEqual(response.status_code, 200)
        access_request.refresh_from_db()
        self.assertEqual(access_request.status, AccessRequest.Status.WAITLIST)
        self.assertEqual(access_request.queue_type, AccessRequest.QueueType.WAITLIST)
        self.assertEqual(access_request.reason_code, AccessRequest.Reason.UNSUPPORTED_STATE)
        self.assertFalse(access_request.requires_manual_review)
        self.assertTrue(mock_waitlist_access_request.called)
        self.assertContains(response, WAITLIST_TOAST_MESSAGE)

    @patch("listings.views.VerificationService.verify_license")
    def test_signup_identity_new_york_input_stores_canonical_state_and_skips_waitlist(self, mock_verify_license):
        access_request = AccessRequest.objects.create(email="newyork-signup@example.com")
        from .email_flows import build_access_request_signup_token

        mock_verify_license.return_value = build_verification_result(
            state="NY",
            raw_payload={"license_number": "LIC-NY-12345"},
        )

        response = self.client.post(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            {
                "full_name": "New York Agent",
                "state": "New York",
                "license_number": "LIC-NY-12345",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("signup_contact"))
        agent = AgentUser.objects.get(email="newyork-signup@example.com")
        access_request.refresh_from_db()
        self.assertEqual(agent.state, "NY")
        self.assertEqual(access_request.state, "NY")
        self.assertEqual(access_request.queue_type, AccessRequest.QueueType.NONE)
        self.assertEqual(access_request.verification_status, AccessRequest.VerificationStatus.VERIFIED)

    @patch("listings.verification.providers.ny_soda3.request.urlopen", side_effect=URLError("timed out"))
    @override_settings(
        NY_OPEN_DATA_BASE_URL="https://data.ny.gov/resource",
        NY_REAL_ESTATE_DATASET_ID="abc-1234",
        NY_LICENSE_API_APP_TOKEN="token-123",
        NY_LICENSE_API_TIMEOUT=5,
    )
    def test_signup_identity_new_york_provider_failure_routes_to_manual_review(self, mock_urlopen):
        access_request = AccessRequest.objects.create(email="newyork-timeout@example.com")
        from .email_flows import build_access_request_signup_token

        response = self.client.post(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            {
                "full_name": "Timeout Agent",
                "state": "New York",
                "license_number": "30AC0961210",
            },
        )

        self.assertEqual(response.status_code, 200)
        access_request.refresh_from_db()
        self.assertEqual(access_request.state, "NY")
        self.assertEqual(access_request.status, AccessRequest.Status.MANUAL_REVIEW)
        self.assertEqual(access_request.queue_type, AccessRequest.QueueType.MANUAL_REVIEW)
        self.assertEqual(access_request.reason_code, AccessRequest.Reason.PROVIDER_ERROR)
        self.assertContains(
            response,
            "We’re having trouble verifying your license right now. A teammate will be in touch shortly.",
        )

    @patch("listings.views.waitlist_access_request")
    def test_signup_identity_unsupported_full_state_name_still_routes_to_waitlist(self, mock_waitlist_access_request):
        access_request = AccessRequest.objects.create(email="california@example.com")
        from .email_flows import build_access_request_signup_token

        response = self.client.post(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            {
                "full_name": "California Agent",
                "state": "California",
                "license_number": "CA-12345",
            },
        )

        self.assertEqual(response.status_code, 200)
        access_request.refresh_from_db()
        self.assertEqual(access_request.state, "CA")
        self.assertEqual(access_request.status, AccessRequest.Status.WAITLIST)
        self.assertEqual(access_request.queue_type, AccessRequest.QueueType.WAITLIST)
        self.assertEqual(access_request.reason_code, AccessRequest.Reason.UNSUPPORTED_STATE)
        self.assertTrue(mock_waitlist_access_request.called)
        self.assertContains(response, WAITLIST_TOAST_MESSAGE)

    def test_signup_identity_redirects_active_registered_email_to_sign_in(self):
        access_request = AccessRequest.objects.create(email="existing@example.com")
        from .email_flows import build_access_request_signup_token

        create_agent(
            name="Existing Agent",
            email="existing@example.com",
            license_number="LIC-EXISTING",
            is_verified=True,
            signup_status=AgentUser.SignupStatus.ACTIVE,
            is_active=True,
        )

        response = self.client.get(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            follow=True,
        )

        self.assertRedirects(response, reverse("landing"))
        self.assertContains(
            response,
            "This email is already registered. Enter it on the sign-in page to get a magic link.",
        )

    def test_signup_contact_redirects_active_registered_email_to_sign_in(self):
        agent = create_agent(
            name="Existing Agent",
            email="existing-contact@example.com",
            license_number="LIC-EXISTING-CONTACT",
            is_verified=True,
            signup_status=AgentUser.SignupStatus.ACTIVE,
            is_active=True,
        )
        access_request = AccessRequest.objects.create(email=agent.email)
        session = self.client.session
        session["pending_signup_agent_id"] = agent.id
        session["pending_access_request_id"] = access_request.id
        session.save()

        response = self.client.get(reverse("signup_contact"), follow=True)

        self.assertRedirects(response, reverse("landing"))
        self.assertContains(
            response,
            "This email is already registered. Enter it on the sign-in page to get a magic link.",
        )

    @patch("listings.views.VerificationService.verify_license")
    def test_signup_contact_completion_activates_agent_and_redirects_to_board(self, mock_verify_license):
        access_request = AccessRequest.objects.create(email="signup@example.com")
        from .email_flows import build_access_request_signup_token

        mock_verify_license.return_value = build_verification_result(raw_payload={"license_number": "LIC-12345"})
        identity_response = self.client.post(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            {
                "full_name": "Signup Agent",
                "state": "NY",
                "license_number": "LIC-12345",
            },
            follow=True,
        )

        self.assertRedirects(identity_response, reverse("signup_contact"))
        self.assertContains(identity_response, "License verified. Continue with contact details.")

        contact_response = self.client.post(
            reverse("signup_contact"),
            {
                "phone_number": "914-555-2222",
                "brokerage": "Whisper Realty",
                "city": "Scarsdale",
            },
            follow=True,
        )

        self.assertRedirects(contact_response, reverse("legal_acceptance"))
        self.assertContains(contact_response, "Signup complete. Review the Terms of Use and Privacy Policy to continue.")
        agent = AgentUser.objects.get(email="signup@example.com")
        access_request.refresh_from_db()
        self.assertTrue(agent.is_active)
        self.assertEqual(agent.signup_status, AgentUser.SignupStatus.ACTIVE)
        self.assertEqual(agent.state, "NY")
        self.assertEqual(agent.brokerage, "Whisper Realty")
        self.assertEqual(agent.city, "Scarsdale")
        self.assertEqual(agent.primary_phone.phone_number, "914-555-2222")
        self.assertEqual(access_request.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(access_request.verification_status, AccessRequest.VerificationStatus.VERIFIED)
        self.assertEqual(access_request.verification_provider, "ny_soda3")
        self.assertIsNotNone(access_request.verified_at)

    @patch("listings.views.VerificationService.verify_license")
    def test_signup_contact_allows_blank_brokerage_and_city(self, mock_verify_license):
        access_request = AccessRequest.objects.create(email="minimal@example.com")
        from .email_flows import build_access_request_signup_token

        mock_verify_license.return_value = build_verification_result(raw_payload={"license_number": "LIC-67890"})
        self.client.post(
            reverse("signup_identity", args=[build_access_request_signup_token(access_request)]),
            {
                "full_name": "Minimal Agent",
                "state": "NY",
                "license_number": "LIC-67890",
            },
        )

        response = self.client.post(
            reverse("signup_contact"),
            {
                "phone_number": "914-555-9090",
                "brokerage": "",
                "city": "",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("legal_acceptance"))
        self.assertContains(response, "Signup complete. Review the Terms of Use and Privacy Policy to continue.")
        agent = AgentUser.objects.get(email="minimal@example.com")
        self.assertEqual(agent.brokerage, "")
        self.assertEqual(agent.city, "")
        self.assertEqual(agent.primary_phone.phone_number, "914-555-9090")

    @override_settings(WHISPER_TERMS_VERSION="2026-03-v1", WHISPER_PRIVACY_VERSION="2026-03-v1")
    def test_legal_acceptance_requires_checkbox_and_stores_acceptance(self):
        agent = create_agent(
            name="Legal Pending Agent",
            email="legal-pending@example.com",
            license_number="LIC-LEGAL-PENDING",
            is_verified=True,
            signup_status=AgentUser.SignupStatus.ACTIVE,
            terms_accepted=False,
            privacy_accepted=False,
            terms_accepted_at=None,
            privacy_accepted_at=None,
            terms_version="",
            privacy_version="",
        )
        login_agent(self.client, agent)

        invalid_response = self.client.post(reverse("legal_acceptance"), {}, follow=True)

        self.assertEqual(invalid_response.status_code, 200)
        self.assertContains(invalid_response, "You must agree to the Terms of Use and Privacy Policy to continue.")

        valid_response = self.client.post(
            reverse("legal_acceptance"),
            {"accept_legal": "on"},
            follow=True,
            HTTP_USER_AGENT="WhisperBrowser/1.0",
            REMOTE_ADDR="203.0.113.10",
        )

        self.assertRedirects(valid_response, reverse("feed"))
        agent.refresh_from_db()
        self.assertTrue(agent.terms_accepted)
        self.assertTrue(agent.privacy_accepted)
        self.assertIsNotNone(agent.terms_accepted_at)
        self.assertIsNotNone(agent.privacy_accepted_at)
        self.assertEqual(agent.terms_version, "2026-03-v1")
        self.assertEqual(agent.privacy_version, "2026-03-v1")
        self.assertEqual(agent.legal_acceptance_ip, "203.0.113.10")
        self.assertEqual(agent.legal_acceptance_user_agent, "WhisperBrowser/1.0")

    def test_legal_acceptance_page_contains_terms_and_privacy_links(self):
        agent = create_agent(
            name="Legal Links Agent",
            email="legal-links@example.com",
            license_number="LIC-LEGAL-LINKS",
            is_verified=True,
            signup_status=AgentUser.SignupStatus.ACTIVE,
            terms_accepted=False,
            privacy_accepted=False,
            terms_accepted_at=None,
            privacy_accepted_at=None,
            terms_version="",
            privacy_version="",
        )
        login_agent(self.client, agent)

        response = self.client.get(reverse("legal_acceptance"))

        self.assertContains(response, reverse("terms_of_use"))
        self.assertContains(response, reverse("privacy_policy"))
        self.assertContains(response, "Whisper is not an MLS")

    def test_terms_and_privacy_pages_resolve(self):
        terms_response = self.client.get(reverse("terms_of_use"))
        privacy_response = self.client.get(reverse("privacy_policy"))

        self.assertContains(terms_response, "Whisper Terms of Use")
        self.assertContains(terms_response, "Effective Date:")
        self.assertContains(terms_response, "not a Multiple Listing Service")
        self.assertContains(terms_response, "Information Accuracy Disclaimer")
        self.assertContains(privacy_response, "Whisper Privacy Policy")
        self.assertContains(privacy_response, "Effective Date:")
        self.assertContains(privacy_response, "authentication events")
        self.assertContains(privacy_response, "legal acceptance")


class FrontDoorMagicLinkTests(TestCase):
    neutral_toast = "If this email is registered, a sign-in link has been sent. Please check your inbox and spam folder."

    @patch("listings.views.send_magic_sign_in_link")
    def test_landing_sends_magic_link_for_active_agent_email(self, mock_send_magic_sign_in_link):
        agent = create_agent(
            name="Active Agent",
            email="active@example.com",
            license_number="LIC-ACTIVE",
            is_verified=True,
        )
        mock_send_magic_sign_in_link.return_value = (MagicMock(), "https://example.com/sign-in/dev")

        response = self.client.post(reverse("landing"), {"email": agent.email}, follow=True)

        self.assertRedirects(response, reverse("landing"))
        self.assertTrue(mock_send_magic_sign_in_link.called)
        self.assertContains(response, self.neutral_toast)

    def test_landing_shows_under_review_message_for_pending_request(self):
        AccessRequest.objects.create(
            email="pending@example.com",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )

        response = self.client.post(reverse("landing"), {"email": "pending@example.com"}, follow=True)

        self.assertRedirects(response, reverse("landing") + "?email=pending%40example.com")
        self.assertContains(response, self.neutral_toast)
        self.assertContains(response, reverse("request_access") + "?email=pending%40example.com", html=False)
        self.assertNotContains(response, "That email is still under review.")

    def test_landing_shows_unknown_email_message_and_prefilled_request_access_link(self):
        response = self.client.post(reverse("landing"), {"email": "unknown@example.com"}, follow=True)

        self.assertRedirects(response, reverse("landing") + "?email=unknown%40example.com")
        self.assertContains(response, self.neutral_toast)
        self.assertContains(response, reverse("request_access") + "?email=unknown%40example.com", html=False)
        self.assertNotContains(response, "We don’t recognize that email yet.")

    def test_magic_link_sign_in_is_single_use_and_redirects_to_board(self):
        agent = create_agent(
            name="Magic Link Agent",
            email="magic@example.com",
            license_number="LIC-MAGIC",
            is_verified=True,
        )
        token = AuthAccessToken.objects.create(
            agent=agent,
            email=agent.email,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        first_response = self.client.get(reverse("consume_auth_access_token", args=[token.token]), follow=True)

        self.assertRedirects(first_response, reverse("feed"))
        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)

        second_response = self.client.get(reverse("consume_auth_access_token", args=[token.token]), follow=True)

        self.assertRedirects(second_response, reverse("landing"))
        self.assertContains(second_response, "This sign-in link is invalid or has expired.")

    def test_request_access_prefills_email_from_query_string(self):
        response = self.client.get(reverse("request_access"), {"email": "prefill@example.com"})

        self.assertContains(response, 'value="prefill@example.com"', html=False)

    def test_landing_can_render_qr_panel_for_active_agent(self):
        agent = create_agent(
            name="QR Agent",
            email="qr@example.com",
            license_number="LIC-QR",
            is_verified=True,
        )

        response = self.client.post(
            reverse("landing"),
            {"email": agent.email, "sign_in_method": "qr"},
        )

        self.assertEqual(response.status_code, 200)
        token = AuthAccessToken.objects.get(email=agent.email, delivery_method=AuthAccessToken.DeliveryMethod.QR)
        self.assertContains(response, "Scan with your phone to sign in.")
        self.assertContains(response, reverse("qr_sign_in_status", args=[token.token]))

    def test_qr_sign_in_marks_token_completed_and_signs_in_phone(self):
        agent = create_agent(
            name="Phone QR Agent",
            email="phone-qr@example.com",
            license_number="LIC-PHONE-QR",
            is_verified=True,
        )
        token = AuthAccessToken.objects.create(
            agent=agent,
            email=agent.email,
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        response = self.client.get(reverse("consume_auth_access_token", args=[token.token]), follow=True)

        self.assertRedirects(response, reverse("feed"))
        token.refresh_from_db()
        self.assertIsNotNone(token.completed_at)
        self.assertIsNotNone(token.used_at)

    def test_qr_status_authenticates_desktop_after_phone_completion(self):
        agent = create_agent(
            name="Desktop QR Agent",
            email="desktop-qr@example.com",
            license_number="LIC-DESKTOP-QR",
            is_verified=True,
        )
        token = AuthAccessToken.objects.create(
            agent=agent,
            email=agent.email,
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            expires_at=timezone.now() + timedelta(minutes=10),
            completed_at=timezone.now(),
            used_at=timezone.now(),
        )

        response = self.client.get(reverse("qr_sign_in_status", args=[token.token]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "authenticated")
        token.refresh_from_db()
        self.assertIsNotNone(token.desktop_authenticated_at)
        session = self.client.session
        self.assertEqual(session[CURRENT_AGENT_SESSION_KEY], agent.id)

    def test_qr_status_redirects_to_legal_step_when_acceptance_incomplete(self):
        agent = create_agent(
            name="Desktop QR Legal Agent",
            email="desktop-qr-legal@example.com",
            license_number="LIC-DESKTOP-QR-LEGAL",
            terms_accepted=False,
            privacy_accepted=False,
            terms_accepted_at=None,
            privacy_accepted_at=None,
            terms_version="",
            privacy_version="",
        )
        token = AuthAccessToken.objects.create(
            agent=agent,
            email=agent.email,
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            expires_at=timezone.now() + timedelta(minutes=10),
            completed_at=timezone.now(),
            used_at=timezone.now(),
        )

        response = self.client.get(reverse("qr_sign_in_status", args=[token.token]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["redirect_url"], reverse("feed"))


@override_settings(
    AUTH_TOKEN_RETENTION_DAYS=14,
    QR_AUTH_TOKEN_EXPIRED_RETENTION_DAYS=1,
    QR_AUTH_TOKEN_USED_RETENTION_DAYS=7,
    ACCESS_REQUEST_RETENTION_DAYS=90,
    REJECTED_ACCESS_REQUEST_RETENTION_DAYS=90,
)
class RetentionCleanupTests(TestCase):
    def setUp(self):
        self.agent = create_agent(
            name="Retention Agent",
            email="retention@example.com",
            license_number="LIC-RETENTION",
            is_verified=True,
        )

    def test_expired_qr_tokens_are_selected_for_cleanup(self):
        stale_qr = AuthAccessToken.objects.create(
            agent=self.agent,
            email=self.agent.email,
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            expires_at=timezone.now() - timedelta(days=2),
        )
        fresh_qr = AuthAccessToken.objects.create(
            agent=self.agent,
            email="fresh-qr@example.com",
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            expires_at=timezone.now() - timedelta(hours=12),
        )

        cleanup_targets = get_cleanup_querysets()

        self.assertIn(stale_qr, cleanup_targets["auth_tokens.qr_expired"])
        self.assertNotIn(fresh_qr, cleanup_targets["auth_tokens.qr_expired"])

    def test_cleanup_keys_match_approved_v1_categories(self):
        cleanup_targets = get_cleanup_querysets()

        self.assertEqual(
            tuple(cleanup_targets.keys()),
            (
                "auth_tokens.qr_expired",
                "auth_tokens.qr_used",
                "auth_tokens.non_qr",
                "access_requests.pending_or_waitlist",
                "access_requests.rejected",
            ),
        )

    def test_stale_access_requests_are_selected_conservatively(self):
        stale_pending = AccessRequest.objects.create(
            email="stale-pending@example.com",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )
        AccessRequest.objects.filter(pk=stale_pending.pk).update(updated_at=timezone.now() - timedelta(days=91))

        stale_rejected = AccessRequest.objects.create(
            email="stale-rejected@example.com",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
        )
        AccessRequest.objects.filter(pk=stale_rejected.pk).update(updated_at=timezone.now() - timedelta(days=91))

        protected = AccessRequest.objects.create(
            email=self.agent.email,
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )
        AccessRequest.objects.filter(pk=protected.pk).update(updated_at=timezone.now() - timedelta(days=120))

        reviewed_rejected = AccessRequest.objects.create(
            email="reviewed-rejected@example.com",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
            reviewed_at=timezone.now() - timedelta(days=120),
        )

        completed = AccessRequest.objects.create(
            email="completed@example.com",
            status=AccessRequest.Status.COMPLETED,
            decision_status=AccessRequest.DecisionStatus.COMPLETED,
            completed_at=timezone.now() - timedelta(days=120),
        )
        AccessRequest.objects.filter(pk=completed.pk).update(updated_at=timezone.now() - timedelta(days=120))

        cleanup_targets = get_cleanup_querysets()

        self.assertIn(stale_pending, cleanup_targets["access_requests.pending_or_waitlist"])
        self.assertIn(stale_rejected, cleanup_targets["access_requests.rejected"])
        self.assertNotIn(protected, cleanup_targets["access_requests.pending_or_waitlist"])
        self.assertNotIn(reviewed_rejected, cleanup_targets["access_requests.rejected"])
        self.assertNotIn(completed, cleanup_targets["access_requests.pending_or_waitlist"])

    def test_cleanup_retention_dry_run_does_not_delete(self):
        AuthAccessToken.objects.create(
            agent=self.agent,
            email=self.agent.email,
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            expires_at=timezone.now() - timedelta(days=2),
        )
        stale_request = AccessRequest.objects.create(
            email="dry-run@example.com",
            status=AccessRequest.Status.REQUESTED,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )
        AccessRequest.objects.filter(pk=stale_request.pk).update(updated_at=timezone.now() - timedelta(days=91))

        output = StringIO()
        call_command("cleanup_retention", "--dry-run", stdout=output)

        self.assertIn("auth_tokens.qr_expired: 1 would delete", output.getvalue())
        self.assertIn("Processing Whisper cleanup categories:", output.getvalue())
        self.assertIn("sessions: cleanup enabled via clearsessions (dry-run cannot preview count)", output.getvalue())
        self.assertEqual(AuthAccessToken.objects.count(), 1)
        self.assertEqual(AccessRequest.objects.count(), 1)

    def test_cleanup_retention_delete_mode_deletes_selected_records(self):
        stale_token = AuthAccessToken.objects.create(
            agent=self.agent,
            email=self.agent.email,
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            expires_at=timezone.now() - timedelta(days=2),
        )
        stale_request = AccessRequest.objects.create(
            email="delete-me@example.com",
            status=AccessRequest.Status.REQUESTED,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )
        AccessRequest.objects.filter(pk=stale_request.pk).update(updated_at=timezone.now() - timedelta(days=91))

        output = StringIO()
        call_command("cleanup_retention", stdout=output)

        self.assertIn("Cleanup complete. Deleted 2 records.", output.getvalue())
        self.assertIn("sessions: cleanup executed via clearsessions", output.getvalue())
        self.assertFalse(AuthAccessToken.objects.filter(pk=stale_token.pk).exists())
        self.assertFalse(AccessRequest.objects.filter(pk=stale_request.pk).exists())

    def test_cleanup_querysets_do_not_include_agentuser_or_listing_models(self):
        cleanup_targets = get_cleanup_querysets()

        self.assertTrue(all(queryset.model is not AgentUser for queryset in cleanup_targets.values()))
        self.assertTrue(all(queryset.model is not Listing for queryset in cleanup_targets.values()))


@override_settings(
    NY_OPEN_DATA_BASE_URL="https://data.ny.gov/resource",
    NY_REAL_ESTATE_DATASET_ID="abc-1234",
    NY_LICENSE_API_APP_TOKEN="token-123",
    NY_LICENSE_API_TIMEOUT=5,
)
class LicenseVerificationTests(TestCase):
    def build_response(self, payload):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = payload.encode("utf-8")
        return response

    def test_normalize_state_code_canonicalizes_multiple_states(self):
        self.assertEqual(normalize_state_code("New York"), "NY")
        self.assertEqual(normalize_state_code("california"), "CA")
        self.assertEqual(normalize_state_code("Texas"), "TX")
        self.assertEqual(normalize_state_code(" new   jersey "), "NJ")
        self.assertEqual(normalize_state_code("District of Columbia"), "DC")
        self.assertEqual(normalize_state_code("Unknownland"), "UNKNOWNLAND")

    @patch("listings.verification.providers.ny_soda3.request.urlopen")
    def test_ny_provider_success(self, mock_urlopen):
        from .verification.service import VerificationService

        mock_urlopen.return_value = self.build_response(
            '[{"business_name":"COLDWELL BANKER REALTY","business_city":"WHITE PLAINS","license_holder_name":"ACOCELLA BETH A","license_expiration_date":"2028-02-24T00:00:00.000","license_number":"30AC0961210","license_type":"ASSOCIATE BROKER"}]'
        )

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="NY",
            license_number="30AC0961210",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertEqual(result.provider, "ny_soda3")
        self.assertEqual(result.matched_business_city, "WHITE PLAINS")
        request_arg = mock_urlopen.call_args[0][0]
        self.assertEqual(
            request_arg.full_url,
            "https://data.ny.gov/resource/abc-1234.json?license_number=30AC0961210&%24limit=5",
        )
        self.assertEqual(request_arg.headers["X-app-token"], "token-123")

    @patch("listings.verification.providers.ny_soda3.request.urlopen")
    def test_reversed_name_success(self, mock_urlopen):
        from .verification.service import VerificationService

        mock_urlopen.return_value = self.build_response(
            '[{"business_name":"COLDWELL BANKER REALTY","business_city":"WHITE PLAINS","license_holder_name":"ACOCELLA BETH A","license_expiration_date":"2028-02-24T00:00:00.000","license_number":"30AC0961210","license_type":"ASSOCIATE BROKER"}]'
        )

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="NY",
            license_number="30AC0961210",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, VerificationStatus.VERIFIED)

    @patch("listings.verification.providers.ny_soda3.request.urlopen")
    def test_new_york_state_name_resolves_to_ny_provider(self, mock_urlopen):
        from .verification.service import VerificationService

        mock_urlopen.return_value = self.build_response(
            '[{"business_name":"COLDWELL BANKER REALTY","business_city":"WHITE PLAINS","license_holder_name":"ACOCELLA BETH A","license_expiration_date":"2028-02-24T00:00:00.000","license_number":"30AC0961210","license_type":"ASSOCIATE BROKER"}]'
        )

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="New York",
            license_number="30AC0961210",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertEqual(result.state, "NY")
        self.assertEqual(result.provider, "ny_soda3")

    @patch("listings.verification.providers.ny_soda3.request.urlopen")
    def test_lowercase_ny_resolves_to_ny_provider(self, mock_urlopen):
        from .verification.service import VerificationService

        mock_urlopen.return_value = self.build_response(
            '[{"business_name":"COLDWELL BANKER REALTY","business_city":"WHITE PLAINS","license_holder_name":"ACOCELLA BETH A","license_expiration_date":"2028-02-24T00:00:00.000","license_number":"30AC0961210","license_type":"ASSOCIATE BROKER"}]'
        )

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="ny",
            license_number="30AC0961210",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        self.assertEqual(result.state, "NY")
        self.assertEqual(result.provider, "ny_soda3")

    @patch("listings.verification.providers.ny_soda3.request.urlopen")
    def test_license_number_is_normalized_before_query_and_match(self, mock_urlopen):
        from .verification.service import VerificationService

        mock_urlopen.return_value = self.build_response(
            '[{"business_name":"COLDWELL BANKER REALTY","business_city":"WHITE PLAINS","license_holder_name":"ACOCELLA BETH A","license_expiration_date":"2028-02-24T00:00:00.000","license_number":"30AC0961210","license_type":"ASSOCIATE BROKER"}]'
        )

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="NY",
            license_number="30ac-0961210",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, VerificationStatus.VERIFIED)
        request_arg = mock_urlopen.call_args[0][0]
        self.assertEqual(
            request_arg.full_url,
            "https://data.ny.gov/resource/abc-1234.json?license_number=30AC0961210&%24limit=5",
        )

    @patch("listings.verification.providers.ny_soda3.request.urlopen")
    def test_expired_license_returns_manual_review(self, mock_urlopen):
        from .verification.service import VerificationService

        mock_urlopen.return_value = self.build_response(
            '[{"business_name":"COLDWELL BANKER REALTY","business_city":"WHITE PLAINS","license_holder_name":"ACOCELLA BETH A","license_expiration_date":"2020-02-24T00:00:00.000","license_number":"30AC0961210","license_type":"ASSOCIATE BROKER"}]'
        )

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="NY",
            license_number="30AC0961210",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, VerificationStatus.EXPIRED)

    @patch("listings.verification.providers.ny_soda3.request.urlopen")
    def test_no_match_returns_manual_review(self, mock_urlopen):
        from .verification.service import VerificationService

        mock_urlopen.return_value = self.build_response("[]")

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="NY",
            license_number="30AC0961210",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, VerificationStatus.NO_MATCH)

    def test_unsupported_state_uses_manual_provider(self):
        from .verification.service import VerificationService

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="CA",
            license_number="30AC0961210",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, VerificationStatus.UNSUPPORTED_STATE)
        self.assertEqual(result.provider, "manual")

    @patch("listings.verification.providers.ny_soda3.request.urlopen", side_effect=URLError("timed out"))
    def test_provider_timeout_routes_to_manual_review(self, mock_urlopen):
        from .verification.service import VerificationService

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="NY",
            license_number="30AC0961210",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, VerificationStatus.PROVIDER_ERROR)
        self.assertIn("timed out", result.reason)

    @override_settings(NY_LICENSE_API_APP_TOKEN="")
    def test_missing_app_token_returns_provider_error_before_request(self):
        from .verification.service import VerificationService

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="NY",
            license_number="30AC0961210",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, VerificationStatus.PROVIDER_ERROR)
        self.assertIn("NY verification provider is not configured.", result.reason)
        self.assertIn("NY_LICENSE_API_APP_TOKEN", result.reason)

    @patch("listings.verification.providers.ny_soda3.request.urlopen")
    def test_name_mismatch_routes_to_manual_review(self, mock_urlopen):
        from .verification.service import VerificationService

        mock_urlopen.return_value = self.build_response(
            '[{"business_name":"COLDWELL BANKER REALTY","business_city":"WHITE PLAINS","license_holder_name":"SMITH JOHN A","license_expiration_date":"2028-02-24T00:00:00.000","license_number":"30AC0961210","license_type":"ASSOCIATE BROKER"}]'
        )

        result = VerificationService().verify_license(
            full_name="Beth Acocella",
            state="NY",
            license_number="30AC0961210",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, VerificationStatus.NAME_MISMATCH)


@override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class IntakePortalTests(TestCase):
    def setUp(self):
        self.site = AdminSite()
        self.model_admin = AccessRequestAdmin(AccessRequest, self.site)

    def test_manual_review_records_are_distinguishable_in_portal_filters(self):
        self.assertIn("queue_type", self.model_admin.list_filter)
        self.assertIn("decision_status", self.model_admin.list_filter)
        self.assertIn("reason_code", self.model_admin.list_filter)

    def test_waitlist_records_are_distinguishable_in_portal_filters(self):
        request = AccessRequest.objects.create(
            email="waitlist-portal@example.com",
            full_name="Portal Waitlist",
            state="CA",
            queue_type=AccessRequest.QueueType.WAITLIST,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )

        self.assertEqual(request.queue_type, AccessRequest.QueueType.WAITLIST)

    def test_waitlist_email_is_sent_and_metadata_is_stored(self):
        from .intake import waitlist_access_request

        access_request = AccessRequest.objects.create(
            email="waitlist@example.com",
            full_name="Wait List Agent",
            state="CA",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )

        waitlist_access_request(access_request)

        access_request.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "You’re on the Whisper waitlist")
        self.assertIsNotNone(access_request.waitlist_email_sent_at)
        self.assertEqual(access_request.last_notification_type, AccessRequest.NotificationType.WAITLIST)

    def test_admin_manual_verify_action_activates_agent_and_sends_sign_in_email(self):
        access_request = AccessRequest.objects.create(
            email="review-approve@example.com",
            full_name="Review Approve",
            state="NY",
            license_number="LIC-REVIEW-1",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.NAME_MISMATCH,
            verification_status=AccessRequest.VerificationStatus.NAME_MISMATCH,
        )
        request = build_admin_request()
        self.model_admin.message_user = lambda *args, **kwargs: None

        self.model_admin.approve_manual_review_requests(request, AccessRequest.objects.filter(pk=access_request.pk))

        access_request.refresh_from_db()
        agent = AgentUser.objects.get(email="review-approve@example.com")
        self.assertEqual(access_request.queue_type, AccessRequest.QueueType.NONE)
        self.assertEqual(access_request.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(access_request.decision_status, AccessRequest.DecisionStatus.COMPLETED)
        self.assertEqual(access_request.reviewed_by, request.user)
        self.assertIsNotNone(access_request.reviewed_at)
        self.assertIsNotNone(access_request.completed_at)
        self.assertIsNotNone(access_request.approval_email_sent_at)
        self.assertEqual(access_request.last_notification_type, AccessRequest.NotificationType.APPROVAL)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Whisper is live for you now")
        self.assertIn("/sign-in/", mail.outbox[0].body)
        self.assertEqual(agent.signup_status, AgentUser.SignupStatus.ACTIVE)
        self.assertTrue(agent.is_active)

    def test_admin_waitlist_activate_action_activates_agent_and_sends_sign_in_email(self):
        access_request = AccessRequest.objects.create(
            email="waitlist-live@example.com",
            full_name="Waitlist Live",
            state="CA",
            license_number="LIC-WAITLIST-1",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )
        request = build_admin_request()
        self.model_admin.message_user = lambda *args, **kwargs: None

        self.model_admin.activate_waitlisted_requests(request, AccessRequest.objects.filter(pk=access_request.pk))

        access_request.refresh_from_db()
        agent = AgentUser.objects.get(email="waitlist-live@example.com")
        self.assertEqual(access_request.queue_type, AccessRequest.QueueType.NONE)
        self.assertEqual(access_request.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(access_request.decision_status, AccessRequest.DecisionStatus.COMPLETED)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Whisper is live for you now")
        self.assertIn("/sign-in/", mail.outbox[0].body)
        self.assertEqual(agent.signup_status, AgentUser.SignupStatus.ACTIVE)
        self.assertTrue(agent.is_active)

    def test_admin_bulk_waitlist_activate_action_reports_activated_skipped_and_failed_counts(self):
        waitlist_one = AccessRequest.objects.create(
            email="waitlist-bulk-1@example.com",
            full_name="Waitlist Bulk One",
            state="CA",
            license_number="LIC-WAITLIST-BULK-1",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )
        waitlist_two = AccessRequest.objects.create(
            email="waitlist-bulk-2@example.com",
            full_name="Waitlist Bulk Two",
            state="CA",
            license_number="LIC-WAITLIST-BULK-2",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )
        manual_review = AccessRequest.objects.create(
            email="manual-skip@example.com",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )
        completed = AccessRequest.objects.create(
            email="completed-skip@example.com",
            status=AccessRequest.Status.COMPLETED,
            queue_type=AccessRequest.QueueType.NONE,
            decision_status=AccessRequest.DecisionStatus.COMPLETED,
            completed_at=timezone.now(),
        )
        request = build_admin_request()
        messages = []
        self.model_admin.message_user = lambda _request, message, **kwargs: messages.append(message)

        self.model_admin.activate_waitlisted_requests(
            request,
            AccessRequest.objects.filter(pk__in=[waitlist_one.pk, waitlist_two.pk, manual_review.pk, completed.pk]),
        )

        waitlist_one.refresh_from_db()
        waitlist_two.refresh_from_db()
        manual_review.refresh_from_db()
        completed.refresh_from_db()
        self.assertTrue(AgentUser.objects.get(email=waitlist_one.email).is_active)
        self.assertTrue(AgentUser.objects.get(email=waitlist_two.email).is_active)
        self.assertEqual(waitlist_one.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(waitlist_two.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(manual_review.status, AccessRequest.Status.MANUAL_REVIEW)
        self.assertEqual(completed.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(
            messages[-1],
            "Activated 2 waitlist request(s). Skipped 2. Failed 0.",
        )

    def test_admin_reject_action_updates_state_and_sends_rejection_email(self):
        access_request = AccessRequest.objects.create(
            email="review-reject@example.com",
            full_name="Review Reject",
            state="NY",
            license_number="LIC-REVIEW-3",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.EXPIRED,
            verification_status=AccessRequest.VerificationStatus.EXPIRED,
        )
        request = build_admin_request()
        self.model_admin.message_user = lambda *args, **kwargs: None

        self.model_admin.reject_manual_review_requests(request, AccessRequest.objects.filter(pk=access_request.pk))

        access_request.refresh_from_db()
        self.assertEqual(access_request.queue_type, AccessRequest.QueueType.MANUAL_REVIEW)
        self.assertEqual(access_request.decision_status, AccessRequest.DecisionStatus.REJECTED)
        self.assertEqual(access_request.reviewed_by, request.user)
        self.assertIsNotNone(access_request.rejection_email_sent_at)
        self.assertEqual(access_request.last_notification_type, AccessRequest.NotificationType.REJECTION)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Update on your Whisper access request")

    def test_rejected_user_cannot_continue(self):
        access_request = AccessRequest.objects.create(
            email="rejected@example.com",
            full_name="Rejected Agent",
            state="NY",
            license_number="LIC-REVIEW-4",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
            reason_code=AccessRequest.Reason.NAME_MISMATCH,
        )
        from .email_flows import build_access_request_continuation_token

        response = self.client.get(
            reverse("signup_contact_continue", args=[build_access_request_continuation_token(access_request)]),
            follow=True,
        )

        self.assertRedirects(response, reverse("request_access"))
        self.assertContains(response, "This continuation link is no longer available.")

    def test_waitlisted_user_cannot_continue(self):
        access_request = AccessRequest.objects.create(
            email="waitlisted@example.com",
            full_name="Waitlisted Agent",
            state="CA",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )
        from .email_flows import build_access_request_continuation_token

        response = self.client.get(
            reverse("signup_contact_continue", args=[build_access_request_continuation_token(access_request)]),
            follow=True,
        )

        self.assertRedirects(response, reverse("request_access"))
        self.assertContains(response, "This continuation link is no longer available.")

    def test_approved_and_rejected_records_remain_in_manual_review_queue(self):
        approved = AccessRequest.objects.create(
            email="approved@example.com",
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.APPROVED,
        )
        rejected = AccessRequest.objects.create(
            email="rejected-metadata@example.com",
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
        )

        self.assertEqual(approved.queue_type, AccessRequest.QueueType.MANUAL_REVIEW)
        self.assertEqual(rejected.queue_type, AccessRequest.QueueType.MANUAL_REVIEW)


@override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class InternalIntakePortalTests(TestCase):
    def setUp(self):
        self.reviewer = create_reviewer_user(
            username="reviewer",
            permissions=[
                "can_access_intake_portal",
                "can_review_manual_requests",
                "can_manage_waitlist",
            ],
        )
        self.manual_request = AccessRequest.objects.create(
            email="manual-portal@example.com",
            full_name="Manual Portal",
            state="NY",
            license_number="LIC-MANUAL-PORTAL",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.NAME_MISMATCH,
            verification_status=AccessRequest.VerificationStatus.NAME_MISMATCH,
        )
        self.waitlist_request = AccessRequest.objects.create(
            email="waitlist-portal@example.com",
            full_name="Waitlist Portal",
            state="CA",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )

    def test_anonymous_user_cannot_access_internal_portal(self):
        response = self.client.get(reverse("intake_home"), follow=True)

        self.assertRedirects(response, reverse("intake_login") + "?next=" + reverse("intake_home"))

    def test_normal_non_reviewer_user_cannot_access_internal_portal(self):
        user = get_user_model().objects.create_user(
            username="plainuser",
            email="plain@example.com",
            password="password123",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("intake_home"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Admin Portal", status_code=403)
        self.assertNotContains(response, "intake portal", status_code=403)

    def test_login_page_uses_admin_portal_wording(self):
        response = self.client.get(reverse("intake_login"))

        self.assertContains(response, "Admin Portal Login")
        self.assertNotContains(response, "Intake Portal")

    def test_authorized_reviewer_can_access_portal(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("intake_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Admin Portal")

    def test_manual_review_list_only_shows_manual_review_records(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("intake_manual_review"))

        self.assertContains(response, "<table", html=False)
        self.assertContains(response, "Last Email")
        self.assertContains(response, "No email sent")
        self.assertContains(response, "Approve Selected")
        self.assertContains(response, "Reject Selected")
        self.assertContains(response, "Approve / Activate")
        self.assertContains(response, "Reject")
        self.assertContains(response, "New York")
        self.assertContains(response, "manual-portal@example.com")
        self.assertNotContains(response, "waitlist-portal@example.com")

    def test_wait_list_list_only_shows_waitlist_records(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("intake_waitlist"))

        self.assertContains(response, "waitlist-portal@example.com")
        self.assertNotContains(response, "manual-portal@example.com")
        self.assertContains(response, "<table", html=False)
        self.assertContains(response, "Search")
        self.assertContains(response, "Bulk Action")
        self.assertContains(response, "Sort")
        self.assertContains(response, "Last Email")
        self.assertContains(response, "Activate Selected")
        self.assertContains(response, "Resend Access Email")
        self.assertContains(response, "California")
        self.assertContains(response, "Showing 1-1 of 1")
        self.assertContains(response, "Page 1 of 1")

    def test_waitlist_bulk_action_preserves_filter_context_in_redirect(self):
        self.client.force_login(self.reviewer)
        next_url = (
            reverse("intake_waitlist")
            + "?q=waitlist&state=CA&status=waitlist&sort=date_desc&page=2"
        )

        response = self.client.post(
            reverse("intake_activate_waitlist_requests"),
            {
                "request_ids": [str(self.waitlist_request.id)],
                "decision_reason": "Launch complete.",
                "next": next_url,
            },
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)

    def test_single_row_action_preserves_filter_context_in_redirect(self):
        self.client.force_login(self.reviewer)
        next_url = (
            reverse("intake_manual_review")
            + "?q=manual&state=NY&status=manual_review&sort=name&page=2"
        )

        response = self.client.post(
            reverse("intake_verify_request", args=[self.manual_request.id]),
            {
                "decision_reason": "Reviewed manually.",
                "next": next_url,
            },
        )

        self.assertRedirects(response, next_url, fetch_redirect_response=False)

    def test_manual_review_dashboard_can_filter_by_search_state_and_status(self):
        self.client.force_login(self.reviewer)
        matching_request = AccessRequest.objects.create(
            email="manual-filter@example.com",
            full_name="Manual Filter Match",
            state="NY",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )
        wrong_queue_request = AccessRequest.objects.create(
            email="waitlist-filter-leak@example.com",
            full_name="Manual Filter Match",
            state="NY",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )

        response = self.client.get(
            reverse("intake_manual_review"),
            {"q": "manual filter", "state": "NY", "status": AccessRequest.Status.MANUAL_REVIEW},
        )

        self.assertContains(response, matching_request.email)
        self.assertNotContains(response, wrong_queue_request.email)
        self.assertNotContains(response, self.manual_request.email)
        self.assertContains(response, "New York")
        self.assertContains(response, "Clear Filters")

    def test_waitlist_dashboard_can_filter_by_search_state_and_status(self):
        self.client.force_login(self.reviewer)
        matching_request = AccessRequest.objects.create(
            email="waitlist-filter@example.com",
            full_name="Waitlist Filter Match",
            state="NY",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )
        wrong_queue_request = AccessRequest.objects.create(
            email="manual-filter-leak@example.com",
            full_name="Waitlist Filter Match",
            state="NY",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )

        response = self.client.get(
            reverse("intake_waitlist"),
            {"q": "waitlist filter", "state": "NY", "status": AccessRequest.Status.WAITLIST},
        )

        self.assertContains(response, matching_request.email)
        self.assertNotContains(response, wrong_queue_request.email)
        self.assertNotContains(response, self.waitlist_request.email)
        self.assertContains(response, "New York")
        self.assertContains(response, "Clear Filters")

    def test_waitlist_dashboard_supports_sort_query_param(self):
        self.client.force_login(self.reviewer)
        AccessRequest.objects.create(
            email="alaska@example.com",
            full_name="Alaska Agent",
            state="AK",
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )

        response = self.client.get(reverse("intake_waitlist"), {"sort": "state"})

        self.assertContains(response, 'option value="state" selected')
        self.assertContains(response, "?sort=state")

    def test_waitlist_dashboard_paginates_results(self):
        self.client.force_login(self.reviewer)
        for index in range(30):
            AccessRequest.objects.create(
                email=f"waitlist-page-{index}@example.com",
                full_name=f"Waitlist Page {index}",
                state="CA",
                queue_type=AccessRequest.QueueType.WAITLIST,
                decision_status=AccessRequest.DecisionStatus.PENDING,
                reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
            )

        response = self.client.get(reverse("intake_waitlist"))

        self.assertContains(response, "Showing 1-25 of 31")
        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, "Next")
        self.assertNotContains(response, "waitlist-page-0@example.com")

        page_two = self.client.get(reverse("intake_waitlist"), {"page": 2})
        self.assertContains(page_two, "Showing 26-31 of 31")
        self.assertContains(page_two, "Page 2 of 2")
        self.assertContains(page_two, "waitlist-page-0@example.com")

    def test_waitlist_row_resend_visibility_matches_eligibility(self):
        self.client.force_login(self.reviewer)
        eligible_waitlist = AccessRequest.objects.create(
            email="eligible-resend@example.com",
            full_name="Eligible Resend",
            state="CA",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        )
        create_agent(
            name="Eligible Resend",
            email=eligible_waitlist.email,
            license_number="LIC-ELIGIBLE-RESEND",
            state="CA",
            signup_status=AgentUser.SignupStatus.ACTIVE,
            is_active=True,
            is_verified=True,
        )

        response = self.client.get(reverse("intake_waitlist"))

        self.assertContains(response, "Resend Access Email", count=2)

    def test_manual_review_dashboard_empty_state_is_queue_specific(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("intake_manual_review"), {"q": "missing-record"})

        self.assertContains(response, "No manual review requests matched the current filters.")
        self.assertContains(response, "Clear filters")

    def test_waitlist_row_resend_is_hidden_for_ineligible_record(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("intake_waitlist"))

        self.assertContains(response, "Resend Access Email", count=1)

    def test_reviewer_can_verify_manual_review_record(self):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            reverse("intake_verify_request", args=[self.manual_request.id]),
            {"decision_reason": "Reviewed manually."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[self.manual_request.id]))
        self.manual_request.refresh_from_db()
        agent = AgentUser.objects.get(email=self.manual_request.email)
        self.assertEqual(self.manual_request.queue_type, AccessRequest.QueueType.NONE)
        self.assertEqual(self.manual_request.decision_status, AccessRequest.DecisionStatus.COMPLETED)
        self.assertIsNotNone(self.manual_request.approval_email_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/sign-in/", mail.outbox[0].body)
        self.assertTrue(agent.is_active)
        self.assertEqual(agent.signup_status, AgentUser.SignupStatus.ACTIVE)

    def test_waitlist_manager_can_activate_waitlist_record(self):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            reverse("intake_verify_request", args=[self.waitlist_request.id]),
            {"decision_reason": "Whisper is live in this market now."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[self.waitlist_request.id]))
        self.waitlist_request.refresh_from_db()
        agent = AgentUser.objects.get(email=self.waitlist_request.email)
        self.assertEqual(self.waitlist_request.queue_type, AccessRequest.QueueType.NONE)
        self.assertEqual(self.waitlist_request.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(self.waitlist_request.decision_status, AccessRequest.DecisionStatus.COMPLETED)
        self.assertIsNotNone(self.waitlist_request.approval_email_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Whisper is live for you now")
        self.assertTrue(agent.is_active)
        self.assertEqual(agent.signup_status, AgentUser.SignupStatus.ACTIVE)

    def test_waitlist_manager_can_bulk_activate_checked_waitlist_records(self):
        self.client.force_login(self.reviewer)
        second_waitlist = AccessRequest.objects.create(
            email="waitlist-portal-two@example.com",
            full_name="Waitlist Portal Two",
            state="CA",
            license_number="LIC-WAITLIST-PORTAL-2",
            status=AccessRequest.Status.WAITLIST,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.UNSUPPORTED_STATE,
        )
        completed = AccessRequest.objects.create(
            email="waitlist-completed@example.com",
            status=AccessRequest.Status.COMPLETED,
            queue_type=AccessRequest.QueueType.NONE,
            decision_status=AccessRequest.DecisionStatus.COMPLETED,
            completed_at=timezone.now(),
        )

        response = self.client.post(
            reverse("intake_activate_waitlist_requests"),
            {
                "request_ids": [str(self.waitlist_request.id), str(second_waitlist.id), str(completed.id)],
                "decision_reason": "Launch complete for selected market.",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_waitlist"))
        queued_messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn("2 requests activated. 1 skipped. 0 failed. Skipped: waitlist-completed@example.com.", queued_messages)
        self.waitlist_request.refresh_from_db()
        second_waitlist.refresh_from_db()
        completed.refresh_from_db()
        self.assertEqual(self.waitlist_request.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(second_waitlist.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(completed.status, AccessRequest.Status.COMPLETED)
        self.assertTrue(AgentUser.objects.get(email=self.waitlist_request.email).is_active)
        self.assertTrue(AgentUser.objects.get(email=second_waitlist.email).is_active)
        self.assertEqual(len(mail.outbox), 2)

    def test_waitlist_manager_can_bulk_invite_active_records_without_status_change(self):
        self.client.force_login(self.reviewer)
        active_request = AccessRequest.objects.create(
            email="active-invite@example.com",
            full_name="Active Invite",
            state="NY",
            status=AccessRequest.Status.COMPLETED,
            queue_type=AccessRequest.QueueType.NONE,
            decision_status=AccessRequest.DecisionStatus.COMPLETED,
            completed_at=timezone.now(),
        )
        create_agent(
            name="Active Invite",
            email=active_request.email,
            license_number="LIC-ACTIVE-INVITE",
            state="NY",
            signup_status=AgentUser.SignupStatus.ACTIVE,
            is_active=True,
            is_verified=True,
        )

        response = self.client.post(
            reverse("intake_activate_waitlist_requests"),
            {
                "bulk_action": "invite",
                "request_ids": [str(active_request.id), str(self.waitlist_request.id)],
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_waitlist"))
        queued_messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn("1 access emails resent. 1 skipped. 0 failed. Skipped: waitlist-portal@example.com.", queued_messages)
        active_request.refresh_from_db()
        self.waitlist_request.refresh_from_db()
        self.assertEqual(active_request.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(self.waitlist_request.queue_type, AccessRequest.QueueType.WAITLIST)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Whisper is live for you now")

    def test_reviewer_can_bulk_approve_manual_review_records(self):
        self.client.force_login(self.reviewer)
        second_manual = AccessRequest.objects.create(
            email="manual-portal-two@example.com",
            full_name="Manual Portal Two",
            state="NY",
            license_number="LIC-MANUAL-PORTAL-2",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.NO_MATCH,
            verification_status=AccessRequest.VerificationStatus.NO_MATCH,
        )

        response = self.client.post(
            reverse("intake_process_manual_review_requests"),
            {
                "bulk_action": "approve",
                "request_ids": [str(self.manual_request.id), str(second_manual.id)],
                "decision_reason": "Bulk approval completed.",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_manual_review"))
        queued_messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn("2 requests approved. 0 skipped. 0 failed.", queued_messages)
        self.manual_request.refresh_from_db()
        second_manual.refresh_from_db()
        self.assertEqual(self.manual_request.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(second_manual.status, AccessRequest.Status.COMPLETED)
        self.assertTrue(AgentUser.objects.get(email=self.manual_request.email).is_active)
        self.assertTrue(AgentUser.objects.get(email=second_manual.email).is_active)
        self.assertEqual(len(mail.outbox), 2)

    def test_reviewer_can_bulk_reject_manual_review_records(self):
        self.client.force_login(self.reviewer)
        second_manual = AccessRequest.objects.create(
            email="manual-reject-two@example.com",
            full_name="Manual Reject Two",
            state="NY",
            license_number="LIC-MANUAL-REJECT-2",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            reason_code=AccessRequest.Reason.NAME_MISMATCH,
            verification_status=AccessRequest.VerificationStatus.NAME_MISMATCH,
        )

        response = self.client.post(
            reverse("intake_process_manual_review_requests"),
            {
                "bulk_action": "reject",
                "request_ids": [str(self.manual_request.id), str(second_manual.id)],
                "decision_reason": "Bulk rejection completed.",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_manual_review"))
        queued_messages = [message.message for message in get_messages(response.wsgi_request)]
        self.assertIn("2 requests rejected. 0 skipped. 0 failed.", queued_messages)
        self.manual_request.refresh_from_db()
        second_manual.refresh_from_db()
        self.assertEqual(self.manual_request.decision_status, AccessRequest.DecisionStatus.REJECTED)
        self.assertEqual(second_manual.decision_status, AccessRequest.DecisionStatus.REJECTED)
        self.assertEqual(len(mail.outbox), 2)

    def test_reviewer_can_reject_manual_review_record(self):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            reverse("intake_reject_request", args=[self.manual_request.id]),
            {"decision_reason": "Name mismatch confirmed."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[self.manual_request.id]))
        self.manual_request.refresh_from_db()
        self.assertEqual(self.manual_request.decision_status, AccessRequest.DecisionStatus.REJECTED)
        self.assertIsNotNone(self.manual_request.rejection_email_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Update on your Whisper access request")

    @patch("listings.internal_views.reject_access_request", side_effect=lambda **kwargs: False)
    def test_reviewer_reject_fail_soft_when_notification_email_fails(self, mock_reject_access_request):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            reverse("intake_reject_request", args=[self.manual_request.id]),
            {"decision_reason": "Name mismatch confirmed."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[self.manual_request.id]))
        self.assertContains(response, f"0 requests rejected. 0 skipped. 1 failed. Failed: {self.manual_request.email}.")
        self.assertTrue(mock_reject_access_request.called)

    @patch("listings.internal_views.approve_access_request", side_effect=lambda **kwargs: ("https://example.com/continue", None, False))
    def test_reviewer_verify_fail_soft_when_notification_email_fails(self, mock_approve_access_request):
        self.client.force_login(self.reviewer)

        response = self.client.post(
            reverse("intake_verify_request", args=[self.manual_request.id]),
            {"decision_reason": "Reviewed manually."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[self.manual_request.id]))
        self.assertContains(response, f"0 requests activated. 0 skipped. 1 failed. Failed: {self.manual_request.email}.")
        self.assertTrue(mock_approve_access_request.called)

    def test_waitlist_records_remain_visible_in_waitlist_view(self):
        self.client.force_login(self.reviewer)

        response = self.client.get(reverse("intake_request_detail", args=[self.waitlist_request.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Waitlist Portal")
        self.assertContains(response, "Unsupported state")

    def test_single_row_activate_is_blocked_for_completed_record(self):
        self.client.force_login(self.reviewer)
        completed_waitlist = AccessRequest.objects.create(
            email="blocked-completed@example.com",
            full_name="Blocked Completed",
            state="CA",
            status=AccessRequest.Status.COMPLETED,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.COMPLETED,
            completed_at=timezone.now(),
        )

        response = self.client.post(
            reverse("intake_verify_request", args=[completed_waitlist.id]),
            {"decision_reason": "Should not reactivate."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[completed_waitlist.id]))
        self.assertContains(response, "This request can no longer be modified.")
        completed_waitlist.refresh_from_db()
        self.assertEqual(completed_waitlist.status, AccessRequest.Status.COMPLETED)
        self.assertEqual(len(mail.outbox), 0)

    def test_single_row_activate_is_blocked_for_rejected_record(self):
        self.client.force_login(self.reviewer)
        rejected_manual = AccessRequest.objects.create(
            email="blocked-rejected@example.com",
            full_name="Blocked Rejected",
            state="NY",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
        )

        response = self.client.post(
            reverse("intake_verify_request", args=[rejected_manual.id]),
            {"decision_reason": "Should not reactivate."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[rejected_manual.id]))
        self.assertContains(response, "This request can no longer be modified.")
        rejected_manual.refresh_from_db()
        self.assertEqual(rejected_manual.decision_status, AccessRequest.DecisionStatus.REJECTED)
        self.assertEqual(len(mail.outbox), 0)

    def test_single_row_reject_is_blocked_for_completed_record(self):
        self.client.force_login(self.reviewer)
        completed_manual = AccessRequest.objects.create(
            email="completed-manual@example.com",
            full_name="Completed Manual",
            state="NY",
            status=AccessRequest.Status.COMPLETED,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.COMPLETED,
            completed_at=timezone.now(),
        )

        response = self.client.post(
            reverse("intake_reject_request", args=[completed_manual.id]),
            {"decision_reason": "Should not reject."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[completed_manual.id]))
        self.assertContains(response, "This request can no longer be modified.")
        completed_manual.refresh_from_db()
        self.assertEqual(completed_manual.decision_status, AccessRequest.DecisionStatus.COMPLETED)
        self.assertEqual(len(mail.outbox), 0)

    def test_single_row_reject_is_blocked_for_already_rejected_record(self):
        self.client.force_login(self.reviewer)
        rejected_manual = AccessRequest.objects.create(
            email="already-rejected@example.com",
            full_name="Already Rejected",
            state="NY",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
        )

        response = self.client.post(
            reverse("intake_reject_request", args=[rejected_manual.id]),
            {"decision_reason": "Should not reject again."},
            follow=True,
        )

        self.assertRedirects(response, reverse("intake_request_detail", args=[rejected_manual.id]))
        self.assertContains(response, "This request can no longer be modified.")
        rejected_manual.refresh_from_db()
        self.assertEqual(rejected_manual.decision_status, AccessRequest.DecisionStatus.REJECTED)
        self.assertEqual(len(mail.outbox), 0)

    def test_detail_page_hides_actions_for_non_actionable_records(self):
        self.client.force_login(self.reviewer)
        completed_waitlist = AccessRequest.objects.create(
            email="completed-hidden@example.com",
            full_name="Completed Hidden",
            state="CA",
            status=AccessRequest.Status.COMPLETED,
            queue_type=AccessRequest.QueueType.WAITLIST,
            decision_status=AccessRequest.DecisionStatus.COMPLETED,
            completed_at=timezone.now(),
        )
        rejected_manual = AccessRequest.objects.create(
            email="rejected-hidden@example.com",
            full_name="Rejected Hidden",
            state="NY",
            status=AccessRequest.Status.MANUAL_REVIEW,
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
        )

        completed_response = self.client.get(reverse("intake_request_detail", args=[completed_waitlist.id]))
        rejected_response = self.client.get(reverse("intake_request_detail", args=[rejected_manual.id]))

        self.assertNotContains(completed_response, ">Activate Access<", html=False)
        self.assertNotContains(completed_response, ">Reject<", html=False)
        self.assertNotContains(rejected_response, ">Activate Access<", html=False)
        self.assertNotContains(rejected_response, ">Reject<", html=False)

    def test_internal_portal_does_not_expose_unrelated_listing_data(self):
        self.client.force_login(self.reviewer)
        agent = create_agent(
            name="Portal Listing Agent",
            email="portal-listing@example.com",
            license_number="LIC-PORTAL-LISTING",
        )
        Listing.objects.create(
            agent=agent,
            title="Hidden Listing",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Should not appear in intake portal.",
        )

        response = self.client.get(reverse("intake_request_detail", args=[self.manual_request.id]))

        self.assertNotContains(response, "Hidden Listing")
        self.assertNotContains(response, "Should not appear in intake portal.")


class ListingPriceFormattingTests(TestCase):
    def setUp(self):
        self.agent = create_agent(
            name="Feed Agent",
            email="feed@example.com",
            license_number="LIC-FEED",
        )
        login_agent(self.client, self.agent)

    def test_format_listing_price_uses_compact_suffixes(self):
        self.assertEqual(format_listing_price(850000), "$850K")
        self.assertEqual(format_listing_price(1200000), "$1.2M")
        self.assertEqual(format_listing_price(2500000), "$2.5M")

    def test_feed_displays_compact_price_range(self):
        Listing.objects.create(
            agent=self.agent,
            title="Loft",
            city="Manhattan",
            property_type="Condo",
            beds=2,
            baths="2.0",
            price_min=850000,
            price_max=1200000,
            stage=Listing.Stage.PRIVATE,
            description="Open plan with skyline views.",
        )

        response = self.client.get(reverse("feed"))

        self.assertContains(response, "$850K &ndash; $1.2M")


class ListingPriceInputParsingTests(TestCase):
    def get_base_form_data(self):
        return {
            "city": "Scarsdale",
            "beds": 3,
            "baths": "2.5",
            "price_min": "900000",
            "price_max": "950000",
            "stage": Listing.Stage.PREMARKET,
            "property_type": "Townhouse",
            "description": "Quiet block with finished basement.",
            "seller_direction_certified": "on",
            "agent_compliance_acknowledged": "on",
            "information_accuracy_certified": "on",
        }

    def test_price_input_parses_millions_suffix(self):
        data = self.get_base_form_data()
        data["price_min"] = "1.2M"
        form = ListingForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["price_min"], 1200000)

    def test_price_input_parses_thousands_suffix(self):
        data = self.get_base_form_data()
        data["price_min"] = "850K"
        form = ListingForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["price_min"], 850000)

    def test_price_input_parses_decimal_without_suffix_as_millions(self):
        data = self.get_base_form_data()
        data["price_min"] = "1.2"
        form = ListingForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["price_min"], 1200000)

    def test_price_input_keeps_large_whole_number_unchanged(self):
        data = self.get_base_form_data()
        data["price_min"] = "1000000"
        form = ListingForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["price_min"], 1000000)

    def test_price_input_shows_friendly_error_for_invalid_value(self):
        data = self.get_base_form_data()
        data["price_min"] = "abc"
        form = ListingForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn("Enter a valid price like 1000000, 1.2, 1.2M, or 850K.", form.errors["price_min"])


class ListingCertificationFormTests(TestCase):
    def get_base_form_data(self):
        return {
            "city": "Scarsdale",
            "beds": 3,
            "baths": "2.5",
            "price_min": "900000",
            "price_max": "950000",
            "stage": Listing.Stage.PREMARKET,
            "property_type": "Townhouse",
            "description": "Quiet block with finished basement.",
            "seller_direction_certified": "on",
            "agent_compliance_acknowledged": "on",
            "information_accuracy_certified": "on",
        }

    def test_premarket_succeeds_with_universal_certifications(self):
        form = ListingForm(data=self.get_base_form_data())

        self.assertTrue(form.is_valid(), form.errors)

    def test_private_listing_fails_without_universal_certifications(self):
        data = self.get_base_form_data()
        data["stage"] = Listing.Stage.PRIVATE
        data["private_marketing_certified"] = "on"
        data.pop("seller_direction_certified")
        data.pop("agent_compliance_acknowledged")

        form = ListingForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn("You must certify seller direction before posting.", form.errors["seller_direction_certified"])
        self.assertIn("You must acknowledge compliance responsibility before posting.", form.errors["agent_compliance_acknowledged"])

    def test_listing_fails_without_accuracy_certification(self):
        data = self.get_base_form_data()
        data.pop("information_accuracy_certified")

        form = ListingForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn(
            "You must certify that the submitted information is accurate before sharing this opportunity.",
            form.errors["information_accuracy_certified"],
        )

    def test_private_listing_succeeds_with_all_required_certifications(self):
        data = self.get_base_form_data()
        data["stage"] = Listing.Stage.PRIVATE
        data["private_marketing_certified"] = "on"

        form = ListingForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)

    def test_listing_form_rejects_description_with_exact_identifier(self):
        data = self.get_base_form_data()
        data["description"] = "Quiet seller at 12 West 34th Street."

        form = ListingForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Please remove exact property identifiers, direct contact details, and links. Share only high-level opportunity information in Whisper.",
            form.errors["description"],
        )

    def test_listing_form_rejects_property_type_with_address_like_text(self):
        data = self.get_base_form_data()
        data["property_type"] = "Loft at 44-12 Broadway Ave"

        form = ListingForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Please remove exact property identifiers, direct contact details, and links. Share only high-level opportunity information in Whisper.",
            form.errors["property_type"],
        )

    def test_listing_form_allows_broad_area_narrative_without_exact_identifiers(self):
        data = self.get_base_form_data()
        data["property_type"] = "Townhouse"
        data["description"] = "Quiet premarket opportunity in Scarsdale near the village with flexible timing."

        form = ListingForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)


class TownAreaChoiceTests(TestCase):
    def test_listing_form_rejects_town_area_outside_controlled_list(self):
        form = ListingForm(
            data={
                "city": "Brooklyn",
                "beds": 3,
                "baths": "2.5",
                "price_min": "900000",
                "price_max": "950000",
                "stage": Listing.Stage.PREMARKET,
                "property_type": "",
                "description": "",
                "seller_direction_certified": "on",
                "agent_compliance_acknowledged": "on",
                "information_accuracy_certified": "on",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("city", form.errors)

    def test_feed_filter_form_rejects_town_area_outside_controlled_list(self):
        form = FeedFilterForm(data={"city": "Brooklyn"})

        self.assertFalse(form.is_valid())
        self.assertIn("city", form.errors)

    def test_listing_and_filter_forms_share_same_town_area_choices(self):
        listing_form = ListingForm()
        filter_form = FeedFilterForm()
        expected_choices = get_town_area_choices()

        self.assertEqual(list(listing_form.fields["city"].choices), expected_choices)
        self.assertEqual(list(filter_form.fields["city"].choices)[1:], expected_choices)


class FeedListingModalTests(TestCase):
    def setUp(self):
        self.agent = create_agent(
            name="Feed Viewer",
            email="feedviewer@example.com",
            license_number="LIC-FEED-VIEW",
        )
        login_agent(self.client, self.agent)

    def test_feed_includes_embedded_listing_form(self):
        response = self.client.get(reverse("feed"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "class=\"desktop-shell-toggle\"")
        self.assertContains(response, "id=\"desktop-shell-toggle\"")
        self.assertContains(response, "id=\"desktop-app-panel\"")
        self.assertContains(response, "id=\"desktop-app-panel-body\"")
        self.assertContains(response, "id=\"feed-layout\"")
        self.assertContains(response, "aria-label=\"Toggle navigation panel\"")
        self.assertContains(response, "Workspace")
        self.assertContains(response, "Account")
        self.assertContains(response, "Log Out")
        self.assertContains(response, "class=\"desktop-panel-footer-copy\"")
        self.assertContains(response, "class=\"content-brand\">Whisper<")
        self.assertContains(response, "Current Opportunities")
        self.assertContains(response, "id=\"listing-overlay\"")
        self.assertContains(response, "id=\"filter-overlay\"")
        self.assertContains(response, "id=\"account-overlay\"")
        self.assertContains(response, "action=\"/post/?source=feed\"")
        self.assertContains(response, "Filters")
        self.assertContains(response, "Share Opportunity")
        self.assertContains(response, "aria-label=\"Mobile navigation\"")
        self.assertContains(response, "href=\"/account/\" class=\"mobile-account-entry\"")
        self.assertContains(response, ">Home</span>", html=False)
        self.assertContains(response, ">Post</span>", html=False)
        self.assertContains(response, ">Workspace</span>", html=False)
        self.assertContains(response, "class=\"mobile-bottom-nav-item", count=3)
        self.assertNotContains(response, "class=\"content-actions\"")
        self.assertContains(response, "class=\"mobile-board-filter-button open-filter-panel\"")
        self.assertContains(response, "Save Collection")
        self.assertContains(response, "Apply filters first to save this collection.")

    def test_feed_renders_dynamic_agent_initials_in_desktop_filter_shell(self):
        agent = create_agent(
            name="Samantha Torres",
            email="samantha@example.com",
            license_number="LIC-SAM",
        )
        login_agent(self.client, agent)

        response = self.client.get(reverse("feed"))

        self.assertContains(response, "ST")
        self.assertContains(response, "Samantha Torres")
        self.assertContains(response, "Free")

    def test_feed_uses_safe_fallbacks_for_missing_listing_fields(self):
        agent = create_agent(
            name="Fallback Agent",
            email="fallback@example.com",
            license_number="LIC-FALLBACK",
        )
        Listing.objects.create(
            agent=agent,
            title="",
            city="Scarsdale",
            property_type="House",
            beds=0,
            baths="0.0",
            price_min=0,
            price_max=0,
            stage=Listing.Stage.PREMARKET,
            description="",
        )

        response = self.client.get(reverse("feed"))

        self.assertContains(response, "Scarsdale Opportunity")
        self.assertNotContains(response, "Shared privately through Whisper with details available on request.")
        self.assertNotContains(response, "??")

    def test_feed_renders_dynamic_agent_initials_in_mobile_account_entry(self):
        agent = create_agent(
            name="Samantha Torres",
            email="samantha@example.com",
            license_number="LIC-SAM",
        )
        login_agent(self.client, agent)

        response = self.client.get(reverse("feed"))

        self.assertContains(response, "class=\"mobile-account-avatar\">ST<")
        self.assertContains(response, "id=\"account-overlay\"")


class BoardAccessTests(TestCase):
    def test_anonymous_user_cannot_access_board(self):
        response = self.client.get(reverse("feed"), follow=True)

        self.assertRedirects(response, reverse("landing"))
        self.assertContains(response, "Log in to access the Whisper board.")

    def test_valid_logged_in_user_can_access_board(self):
        agent = create_agent(
            name="Board Agent",
            email="board@example.com",
            license_number="LIC-BOARD",
        )
        login_agent(self.client, agent)

        response = self.client.get(reverse("feed"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current Opportunities")

    def test_verified_user_without_legal_acceptance_is_redirected_to_legal_step(self):
        agent = create_agent(
            name="Board Legal Agent",
            email="board-legal@example.com",
            license_number="LIC-BOARD-LEGAL",
            terms_accepted=False,
            privacy_accepted=False,
            terms_accepted_at=None,
            privacy_accepted_at=None,
            terms_version="",
            privacy_version="",
        )
        login_agent(self.client, agent)

        response = self.client.get(reverse("feed"), follow=True)

        self.assertRedirects(response, reverse("legal_acceptance"))

    def test_verified_user_without_legal_acceptance_is_redirected_from_other_protected_route(self):
        agent = create_agent(
            name="Workspace Legal Agent",
            email="workspace-legal@example.com",
            license_number="LIC-WORKSPACE-LEGAL",
            terms_accepted=False,
            privacy_accepted=False,
            terms_accepted_at=None,
            privacy_accepted_at=None,
            terms_version="",
            privacy_version="",
        )
        login_agent(self.client, agent)

        response = self.client.get(reverse("workspace"), follow=True)

        self.assertRedirects(response, reverse("legal_acceptance"))

    def test_public_routes_remain_accessible_without_legal_acceptance(self):
        agent = create_agent(
            name="Public Route Agent",
            email="public-route@example.com",
            license_number="LIC-PUBLIC-ROUTE",
            terms_accepted=False,
            privacy_accepted=False,
            terms_accepted_at=None,
            privacy_accepted_at=None,
            terms_version="",
            privacy_version="",
        )
        login_agent(self.client, agent)

        self.assertEqual(self.client.get(reverse("legal_acceptance")).status_code, 200)
        self.assertEqual(self.client.get(reverse("terms_of_use")).status_code, 200)
        self.assertEqual(self.client.get(reverse("privacy_policy")).status_code, 200)
        self.assertEqual(self.client.get(reverse("request_access")).status_code, 200)

    def test_magic_link_sign_in_redirects_to_legal_step_when_acceptance_incomplete(self):
        agent = create_agent(
            name="Magic Legal Agent",
            email="magic-legal@example.com",
            license_number="LIC-MAGIC-LEGAL",
            terms_accepted=False,
            privacy_accepted=False,
            terms_accepted_at=None,
            privacy_accepted_at=None,
            terms_version="",
            privacy_version="",
        )
        token = AuthAccessToken.objects.create(
            agent=agent,
            email=agent.email,
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        response = self.client.get(reverse("consume_auth_access_token", args=[token.token]), follow=True)

        self.assertRedirects(response, reverse("legal_acceptance"))

    def test_logout_clears_board_access_state(self):
        agent = create_agent(
            name="Logged Out Agent",
            email="loggedout@example.com",
            license_number="LIC-LOGOUT",
        )
        login_agent(self.client, agent)

        response = self.client.get(reverse("logout_account"), follow=True)

        self.assertRedirects(response, reverse("landing"))
        session = self.client.session
        self.assertNotIn(CURRENT_AGENT_SESSION_KEY, session)
        self.assertTrue(session.get(CURRENT_AGENT_LOCKED_OUT_KEY))

    def test_logged_out_user_cannot_access_board(self):
        agent = create_agent(
            name="Post Logout Agent",
            email="postlogout@example.com",
            license_number="LIC-POST-LOGOUT",
        )
        login_agent(self.client, agent)
        self.client.get(reverse("logout_account"))

        response = self.client.get(reverse("feed"), follow=True)

        self.assertRedirects(response, reverse("landing"))
        self.assertContains(response, "Log in to access the Whisper board.")


class AccountAreaTests(TestCase):
    def setUp(self):
        self.agent = create_agent(
            name="Tester Example",
            email="tester@example.com",
            license_number="LIC-TESTER-42",
            is_verified=True,
        )
        self.agent.phones.all().delete()
        AgentPhone.objects.create(agent=self.agent, phone_number="914-555-1212")
        self.listing = Listing.objects.create(
            agent=self.agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Scarsdale premarket.",
        )
        SavedListing.objects.create(agent=self.agent, listing=self.listing)
        Collection.objects.create(agent=self.agent, name="Core Buyers")
        login_agent(self.client, self.agent)

    def test_account_page_renders_membership_activity_and_danger_zone(self):
        response = self.client.get(reverse("account"))

        self.assertContains(response, "Membership")
        self.assertContains(response, "My Activity")
        self.assertContains(response, "Notifications")
        self.assertContains(response, "Settings")
        self.assertContains(response, "Danger Zone")
        self.assertContains(response, "Founding Member")
        self.assertContains(response, "My Active Posts")
        self.assertContains(response, "Saved Opportunities")
        self.assertContains(response, "Collections")
        self.assertContains(response, "Delete Membership")
        self.assertContains(response, "Collection match emails")
        self.assertContains(response, "Product update emails")
        self.assertContains(response, "Freshness reminder emails")
        self.assertContains(response, "Account &amp; security emails")
        self.assertContains(response, "Always On")
        self.assertContains(response, "Occasional emails when Whisper launches in new areas or rolls out major features.")
        self.assertNotContains(response, "Contact and activity emails")
        self.assertNotContains(response, "Planned")

    def test_feed_settings_and_identity_link_to_account_area(self):
        response = self.client.get(reverse("feed"))

        self.assertContains(response, "href=\"/account/\"", html=False)
        self.assertContains(response, "href=\"/account/logout/\"", html=False)
        self.assertContains(response, "href=\"/account/\"", html=False)

    def test_logout_account_route_signs_out_from_get(self):
        response = self.client.get(reverse("logout_account"), follow=True)

        self.assertRedirects(response, reverse("landing"))
        self.assertContains(response, "logged out")
        board_response = self.client.get(reverse("feed"))
        self.assertRedirects(board_response, reverse("landing"))

    def test_delete_account_requires_delete_confirmation_text(self):
        response = self.client.post(reverse("delete_account"), {"confirm_text": "NOPE"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Type")
        self.assertContains(response, "DELETE")
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.is_active)

    def test_delete_account_soft_deactivates_agent_and_hides_posts(self):
        response = self.client.post(reverse("delete_account"), {"confirm_text": "DELETE"}, follow=True)

        self.assertRedirects(response, reverse("landing"))
        self.agent.refresh_from_db()
        self.listing.refresh_from_db()
        self.assertFalse(self.agent.is_active)
        self.assertIsNotNone(self.agent.deleted_at)
        self.assertFalse(self.listing.is_active)
        self.assertNotContains(response, "Scarsdale premarket.")

    @patch("listings.email_flows.send_email")
    def test_add_email_creates_unverified_email_and_sends_verification(self, mock_send_email):
        response = self.client.post(reverse("add_agent_email"), {"email": "new@example.com"}, follow=True)

        self.assertRedirects(response, reverse("account"))
        added_email = AgentEmail.objects.get(email="new@example.com")
        self.assertEqual(added_email.agent, self.agent)
        self.assertFalse(added_email.is_verified)
        self.assertFalse(added_email.is_primary)
        self.assertTrue(mock_send_email.called)

    def test_verify_email_marks_email_verified(self):
        email = AgentEmail.objects.create(agent=self.agent, email="verify@example.com")

        response = self.client.get(reverse("verify_agent_email", args=[build_agent_email_verification_token(email)]))

        self.assertContains(response, "Your email has been verified.")
        email.refresh_from_db()
        self.assertTrue(email.is_verified)
        self.assertIsNotNone(email.verified_at)

    def test_unverified_email_cannot_become_primary(self):
        email = AgentEmail.objects.create(agent=self.agent, email="secondary@example.com")

        response = self.client.post(reverse("make_primary_agent_email", args=[email.id]), follow=True)

        self.assertRedirects(response, reverse("account"))
        self.assertContains(response, "Verify this email before making it primary.")
        email.refresh_from_db()
        self.assertFalse(email.is_primary)

    def test_verified_email_can_become_primary_and_syncs_agent_email(self):
        email = AgentEmail.objects.create(
            agent=self.agent,
            email="secondary@example.com",
            is_verified=True,
            verified_at=timezone.now(),
        )

        response = self.client.post(reverse("make_primary_agent_email", args=[email.id]), follow=True)

        self.assertRedirects(response, reverse("account"))
        email.refresh_from_db()
        self.agent.refresh_from_db()
        self.assertTrue(email.is_primary)
        self.assertEqual(self.agent.email, "secondary@example.com")

    def test_last_email_cannot_be_removed(self):
        primary_email = self.agent.emails.get(is_primary=True)

        response = self.client.post(reverse("remove_agent_email", args=[primary_email.id]), follow=True)

        self.assertRedirects(response, reverse("account"))
        self.assertContains(response, "You must keep at least one email on the account.")
        self.assertTrue(AgentEmail.objects.filter(pk=primary_email.id).exists())

    def test_account_page_renders_contact_settings(self):
        response = self.client.get(reverse("account"))

        self.assertContains(response, "Contact Settings")
        self.assertContains(response, "Email Addresses")
        self.assertContains(response, "Phone Numbers")
        self.assertContains(response, "tester@example.com")
        self.assertContains(response, "914-555-1212")
        self.assertContains(response, "Primary")
        self.assertContains(response, "Verified")
        self.assertContains(response, "Change")
        self.assertContains(response, "Show my verified primary email to other agents")

    def test_contact_visibility_preference_updates(self):
        response = self.client.post(
            reverse("update_contact_visibility"),
            {"show_email_to_agents": "on"},
            follow=True,
        )

        self.assertRedirects(response, reverse("account"))
        self.agent.refresh_from_db()
        self.assertTrue(self.agent.show_email_to_agents)

    def test_notification_settings_save_behavior(self):
        response = self.client.post(
            reverse("update_notification_preferences"),
            {
                "freshness_reminder_emails": "",
                "collection_match_emails": "on",
                "product_update_emails": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("account"))
        self.assertContains(response, "Notification preferences updated")
        self.agent.refresh_from_db()
        self.assertFalse(self.agent.freshness_reminder_emails)
        self.assertTrue(self.agent.collection_match_emails)
        self.assertTrue(self.agent.product_update_emails)

    def test_account_security_section_is_informational_only(self):
        response = self.client.get(reverse("account"))

        self.assertContains(response, "Account &amp; security emails")
        self.assertContains(response, "access-status emails stay on")
        self.assertNotContains(response, 'name="account_security_emails"', html=False)

    def test_add_update_and_delete_phone(self):
        add_response = self.client.post(reverse("add_agent_phone"), {"phone_number": "914-555-0000"}, follow=True)
        self.assertRedirects(add_response, reverse("account"))
        phone = AgentPhone.objects.get(phone_number="914-555-0000")

        update_response = self.client.post(
            reverse("update_agent_phone", args=[phone.id]),
            {"phone_number": "914-555-9999"},
            follow=True,
        )
        self.assertRedirects(update_response, reverse("account"))
        phone.refresh_from_db()
        self.assertEqual(phone.phone_number, "914-555-9999")

        delete_response = self.client.post(reverse("delete_agent_phone", args=[phone.id]), follow=True)
        self.assertRedirects(delete_response, reverse("account"))
        self.assertFalse(AgentPhone.objects.filter(pk=phone.id).exists())

    def test_last_phone_cannot_be_deleted(self):
        only_phone = self.agent.phones.get()

        response = self.client.post(reverse("delete_agent_phone", args=[only_phone.id]), follow=True)

        self.assertRedirects(response, reverse("account"))
        self.assertContains(response, "A phone number is required for agent contact.")
        self.assertTrue(AgentPhone.objects.filter(pk=only_phone.id).exists())

    def test_listing_card_hides_direct_agent_contact_details(self):
        agent = create_agent(
            name="Samantha Torres",
            email="samantha@example.com",
            license_number="LIC-SAM",
        )
        agent.phones.all().delete()
        AgentPhone.objects.create(agent=agent, phone_number="914-555-7777")
        Listing.objects.create(
            agent=agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Private contact flow test.",
        )

        response = self.client.get(reverse("feed"))
        html = response.content.decode()
        card_markup = re.search(r"<article class=\"card listing-card\">(.*?)</article>", html, re.DOTALL)

        self.assertIsNotNone(card_markup)
        self.assertIn("Contact Agent", card_markup.group(1))
        self.assertIn("listing-contact-panel", card_markup.group(1))
        self.assertIn("hidden", card_markup.group(1))
        self.assertNotIn("Still Available", card_markup.group(1))

    def test_contact_section_renders_phone_and_hides_email_by_default(self):
        agent = create_agent(
            name="Samantha Torres",
            email="samantha@example.com",
            license_number="LIC-SAM",
        )
        agent.phones.all().delete()
        AgentPhone.objects.create(agent=agent, phone_number="914-555-7777")
        listing = Listing.objects.create(
            agent=agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Private contact flow test.",
        )

        response = self.client.get(reverse("feed"))

        self.assertContains(response, f"data-listing-contact-target=\"listing-contact-{listing.id}\"")
        self.assertContains(response, f"id=\"listing-contact-{listing.id}\"")
        self.assertContains(response, "914-555-7777")
        self.assertContains(response, "Samantha Torres")
        self.assertNotContains(response, "mailto:samantha@example.com")

    def test_contact_section_shows_email_when_agent_has_enabled_sharing(self):
        agent = create_agent(
            name="Samantha Torres",
            email="samantha@example.com",
            license_number="LIC-SAM-SHARE",
            show_email_to_agents=True,
        )
        agent.phones.all().delete()
        AgentPhone.objects.create(agent=agent, phone_number="914-555-8888")
        listing = Listing.objects.create(
            agent=agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Private contact flow test.",
        )

        response = self.client.get(reverse("feed"))

        self.assertContains(response, f"id=\"listing-contact-{listing.id}\"")
        self.assertContains(response, "mailto:samantha@example.com")


class FeedFilteringTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.agent = create_agent(
            name="Filter Agent",
            email="filters@example.com",
            license_number="LIC-FILTER",
        )
        Listing.objects.create(
            agent=cls.agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Scarsdale premarket.",
        )
        Listing.objects.create(
            agent=cls.agent,
            title="4 Bed / 3.0 Bath in Rye",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2200000,
            price_max=2500000,
            stage=Listing.Stage.PRIVATE,
            description="Rye private listing.",
        )
        Listing.objects.create(
            agent=cls.agent,
            title="2 Bed / 1.5 Bath in White Plains",
            city="White Plains",
            property_type="Condo",
            beds=2,
            baths="1.5",
            price_min=750000,
            price_max=850000,
            stage=Listing.Stage.PREMARKET,
            description="White Plains condo.",
        )

    def setUp(self):
        login_agent(self.client, self.agent)

    def test_city_filter(self):
        response = self.client.get(reverse("feed"), {"city": "Scarsdale"})

        self.assertContains(response, "Scarsdale premarket.")
        self.assertNotContains(response, "Rye private listing.")

    def test_stage_filter(self):
        response = self.client.get(reverse("feed"), {"stage": Listing.Stage.PRIVATE})

        self.assertContains(response, "Rye private listing.")
        self.assertNotContains(response, "Scarsdale premarket.")

    def test_min_beds_filter(self):
        response = self.client.get(reverse("feed"), {"min_beds": "3"})

        self.assertContains(response, "Scarsdale premarket.")
        self.assertContains(response, "Rye private listing.")
        self.assertNotContains(response, "White Plains condo.")

    def test_min_baths_filter(self):
        response = self.client.get(reverse("feed"), {"min_baths": "2.0"})

        self.assertContains(response, "Scarsdale premarket.")
        self.assertContains(response, "Rye private listing.")
        self.assertNotContains(response, "White Plains condo.")

    def test_min_price_filter_accepts_natural_input(self):
        response = self.client.get(reverse("feed"), {"min_price": "1.2"})

        self.assertContains(response, "Scarsdale premarket.")
        self.assertContains(response, "Rye private listing.")
        self.assertNotContains(response, "White Plains condo.")

    def test_max_price_filter_accepts_natural_input(self):
        response = self.client.get(reverse("feed"), {"max_price": "850K"})

        self.assertContains(response, "White Plains condo.")
        self.assertNotContains(response, "Scarsdale premarket.")
        self.assertNotContains(response, "Rye private listing.")

    def test_combined_filters(self):
        response = self.client.get(
            reverse("feed"),
            {
                "city": "Scarsdale",
                "stage": Listing.Stage.PREMARKET,
                "min_beds": "3",
                "min_baths": "2.0",
                "min_price": "1M",
                "max_price": "1.2M",
            },
        )

        self.assertContains(response, "Scarsdale premarket.")
        self.assertNotContains(response, "Rye private listing.")
        self.assertNotContains(response, "White Plains condo.")

    def test_active_filter_chips_render(self):
        response = self.client.get(
            reverse("feed"),
            {
                "city": "Scarsdale",
                "stage": Listing.Stage.PREMARKET,
                "min_beds": "3",
                "min_baths": "2.0",
                "min_price": "1M",
                "max_price": "2M",
            },
        )

        self.assertContains(response, "<a class=\"filter-chip\"", count=10)
        self.assertContains(response, "$1M–$2M")
        self.assertContains(response, "2.0+ Baths")
        self.assertContains(response, "Clear All")
        self.assertContains(response, "id=\"desktop-app-panel\"")
        self.assertContains(response, "id=\"filter-overlay\"")
        self.assertContains(response, "class=\"filter-chip-row board-filter-chip-row\"")

        html = response.content.decode()
        self.assertLess(
            html.index("class=\"filter-chip-row board-filter-chip-row\""),
            html.index("class=\"listing-grid\""),
        )

    def test_clear_filters_behavior(self):
        response = self.client.get(
            reverse("feed"),
            {
                "city": "Scarsdale",
                "stage": Listing.Stage.PREMARKET,
            },
        )

        self.assertContains(response, f'href="{reverse("feed")}"', html=False)

        cleared_response = self.client.get(reverse("feed"))

        self.assertNotContains(cleared_response, "<a class=\"filter-chip\"")

    def test_my_listings_workspace_filter(self):
        response = self.client.get(reverse("feed"), {"mine": "on"})

        self.assertContains(response, "Scarsdale premarket.")
        self.assertContains(response, "Rye private listing.")
        self.assertContains(response, "White Plains condo.")
        self.assertContains(response, "My Opportunities")

    def test_saved_workspace_view_filters_to_saved_listings(self):
        saved_listing = Listing.objects.get(city="Scarsdale")
        agent = AgentUser.objects.get(email="filters@example.com")
        SavedListing.objects.create(agent=agent, listing=saved_listing)

        response = self.client.get(reverse("feed"), {"view": "saved"})

        self.assertContains(response, "Scarsdale premarket.")
        self.assertNotContains(response, "Rye private listing.")
        self.assertNotContains(response, "White Plains condo.")


@override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class CollectionTests(TestCase):
    def setUp(self):
        self.agent = create_agent(
            name="Collections Agent",
            email="collections@example.com",
            license_number="LIC-COLLECTIONS",
        )
        Listing.objects.create(
            agent=self.agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Scarsdale premarket.",
        )
        Listing.objects.create(
            agent=self.agent,
            title="4 Bed / 3.0 Bath in Rye",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2200000,
            price_max=2500000,
            stage=Listing.Stage.PRIVATE,
            description="Rye private listing.",
        )
        login_agent(self.client, self.agent)

    def test_save_collection_persists_current_filter_set(self):
        response = self.client.post(
            reverse("save_collection"),
            {
                "collection_choice": "__new__",
                "new_collection_name": "Scarsdale Premarket",
                "city": "Scarsdale",
                "stage": Listing.Stage.PREMARKET,
                "min_beds": "3",
                "min_baths": "2.0",
                "min_price": "1M",
                "max_price": "1.2M",
            },
        )

        self.assertRedirects(
            response,
            reverse("feed") + "?city=Scarsdale&stage=premarket&min_beds=3&min_baths=2.0&min_price=1000000&max_price=1200000",
        )
        collection = Collection.objects.get(name="Scarsdale Premarket")
        self.assertEqual(collection.agent, self.agent)
        self.assertEqual(collection.saved_filter.city, "Scarsdale")
        self.assertEqual(collection.saved_filter.stage, Listing.Stage.PREMARKET)
        self.assertEqual(collection.saved_filter.min_beds, 3)
        self.assertEqual(collection.saved_filter.min_baths, 2)
        self.assertEqual(collection.saved_filter.min_price, 1000000)
        self.assertEqual(collection.saved_filter.max_price, 1200000)

        saved_response = self.client.post(
            reverse("save_collection"),
            {
                "collection_choice": "__new__",
                "new_collection_name": "Scarsdale Premarket Two",
                "city": "Scarsdale",
                "stage": Listing.Stage.PREMARKET,
                "min_beds": "3",
                "min_baths": "2.0",
                "min_price": "1000000",
                "max_price": "1200000",
            },
            follow=True,
        )
        self.assertContains(saved_response, "Collection saved")

    def test_feed_shows_explicit_save_collection_action_when_filters_are_set(self):
        response = self.client.get(
            reverse("feed"),
            {
                "city": "Scarsdale",
                "min_beds": "3",
            },
        )

        self.assertContains(response, "Save this intent")
        self.assertContains(response, "Save to Collection")
        self.assertContains(response, "Notify me when a new opportunity matches this filter")

    def test_feed_disables_save_collection_without_filters(self):
        response = self.client.get(reverse("feed"))

        self.assertNotContains(response, "Save this intent")

    def test_feed_renders_saved_collection_links_and_loads_them(self):
        collection = Collection.objects.create(agent=self.agent, name="Private Rye")
        CollectionFilter.objects.create(
            collection=collection,
            city="Rye",
            stage=Listing.Stage.PRIVATE,
            min_beds=4,
            min_baths="3.0",
            min_price=2200000,
            max_price=2600000,
        )

        response = self.client.get(reverse("feed"))

        self.assertContains(response, "Private Rye")
        self.assertContains(
            response,
            f'href="{reverse("feed")}?city=Rye&amp;stage=private&amp;min_beds=4&amp;min_baths=3.0&amp;min_price=2200000&amp;max_price=2600000"',
            html=False,
        )

        loaded_response = self.client.get(
            reverse("feed"),
            {
                "city": "Rye",
                "stage": Listing.Stage.PRIVATE,
                "min_beds": "4",
                "min_baths": "3.0",
                "min_price": "2200000",
                "max_price": "2600000",
            },
        )

        self.assertContains(loaded_response, "Rye private listing.")
        self.assertNotContains(loaded_response, "Scarsdale premarket.")

    def test_empty_state_shows_collection_alert_checkbox_flow(self):
        response = self.client.get(
            reverse("feed"),
            {
                "city": "Scarsdale",
                "stage": Listing.Stage.PRIVATE,
            },
        )

        self.assertContains(response, "No current opportunities")
        self.assertContains(response, "Adjust your filters or clear them to widen the board.")
        self.assertContains(response, "Notify me when a new opportunity matches this filter")
        self.assertContains(response, "We’ll save this as a collection alert.")

    def test_save_filtered_intent_to_existing_collection_enables_notifications(self):
        existing_collection = Collection.objects.create(agent=self.agent, name="Scarsdale Buyers")

        response = self.client.post(
            reverse("save_collection"),
            {
                "collection_choice": str(existing_collection.id),
                "new_collection_name": "",
                "notifications_enabled": "on",
                "city": "Scarsdale",
                "stage": Listing.Stage.PREMARKET,
                "min_beds": "3",
                "min_baths": "2.0",
                "min_price": "1M",
                "max_price": "1.2M",
            },
            follow=True,
        )

        self.assertContains(response, "Collection alert saved")
        existing_collection.refresh_from_db()
        self.assertTrue(existing_collection.notifications_enabled)
        self.assertEqual(existing_collection.saved_filter.city, "Scarsdale")

    def test_save_filtered_intent_can_create_new_named_collection(self):
        response = self.client.post(
            reverse("save_collection"),
            {
                "collection_choice": "__new__",
                "new_collection_name": "Beekman Collection",
                "notifications_enabled": "on",
                "city": "Scarsdale",
                "stage": Listing.Stage.PREMARKET,
                "min_beds": "3",
            },
            follow=True,
        )

        self.assertContains(response, "Collection alert saved")
        collection = Collection.objects.get(name="Beekman Collection")
        self.assertTrue(collection.notifications_enabled)
        self.assertEqual(collection.saved_filter.city, "Scarsdale")

    def test_new_matching_post_sends_collection_alert_email_once(self):
        subscriber = create_agent(
            name="Subscriber Agent",
            email="subscriber@example.com",
            license_number="LIC-SUBSCRIBER",
            is_verified=True,
        )
        subscriber_collection = Collection.objects.create(
            agent=subscriber,
            name="Beekman Collection",
            notifications_enabled=True,
        )
        CollectionFilter.objects.create(
            collection=subscriber_collection,
            city="Scarsdale",
            stage=Listing.Stage.PREMARKET,
            min_beds=3,
        )

        poster = create_agent(
            name="Poster Agent",
            email="poster@example.com",
            license_number="LIC-POSTER",
            is_verified=True,
        )
        login_agent(self.client, poster)

        response = self.client.post(
            reverse("post_listing"),
            {
                "city": "Scarsdale",
                "beds": 3,
                "baths": "2.0",
                "price_min": "1M",
                "price_max": "1.2M",
                "stage": Listing.Stage.PREMARKET,
                "property_type": "House",
                "description": "New matching post.",
                "seller_direction_certified": "on",
                "agent_compliance_acknowledged": "on",
                "information_accuracy_certified": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("feed"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Whisper — New Opportunity matches Beekman Collection")
        listing = Listing.objects.get(agent=poster, city="Scarsdale")
        self.assertTrue(
            EmailNotificationLog.objects.filter(
                collection=subscriber_collection,
                listing=listing,
                notification_type=EmailNotificationLog.NotificationType.COLLECTION_MATCH,
            ).exists()
        )

    def test_collection_match_duplicate_prevention(self):
        subscriber = create_agent(
            name="Repeat Subscriber",
            email="repeat-subscriber@example.com",
            license_number="LIC-REPEAT-SUBSCRIBER",
            is_verified=True,
        )
        subscriber_collection = Collection.objects.create(
            agent=subscriber,
            name="Repeat Collection",
            notifications_enabled=True,
        )
        CollectionFilter.objects.create(collection=subscriber_collection, city="Scarsdale")
        listing = Listing.objects.create(
            agent=self.agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Scarsdale premarket.",
        )

        from .collection_alerts import send_collection_match_alerts_for_listing

        send_collection_match_alerts_for_listing(listing)
        send_collection_match_alerts_for_listing(listing)

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            EmailNotificationLog.objects.filter(
                collection=subscriber_collection,
                listing=listing,
                notification_type=EmailNotificationLog.NotificationType.COLLECTION_MATCH,
            ).count(),
            1,
        )

    def test_in_app_notification_is_created_on_collection_match(self):
        subscriber = create_agent(
            name="In App Subscriber",
            email="inapp@example.com",
            license_number="LIC-INAPP",
            is_verified=True,
        )
        subscriber_collection = Collection.objects.create(
            agent=subscriber,
            name="Scarsdale Match",
            notifications_enabled=True,
        )
        CollectionFilter.objects.create(collection=subscriber_collection, city="Scarsdale")

        poster = create_agent(
            name="In App Poster",
            email="inapp-poster@example.com",
            license_number="LIC-INAPP-POSTER",
            is_verified=True,
        )
        login_agent(self.client, poster)

        self.client.post(
            reverse("post_listing"),
            {
                "city": "Scarsdale",
                "beds": 3,
                "baths": "2.0",
                "price_min": "1M",
                "price_max": "1.2M",
                "stage": Listing.Stage.PREMARKET,
                "property_type": "House",
                "description": "In-app match post.",
                "seller_direction_certified": "on",
                "agent_compliance_acknowledged": "on",
                "information_accuracy_certified": "on",
            },
        )

        notification = InAppNotification.objects.get(agent=subscriber)
        self.assertEqual(notification.notification_type, InAppNotification.NotificationType.COLLECTION_MATCH)
        self.assertEqual(notification.collection, subscriber_collection)
        self.assertFalse(notification.is_read)
        self.assertIn("Scarsdale Match", notification.title)
        self.assertIn("New opportunity matches", notification.title)

    def test_notification_open_marks_read_and_redirects_to_destination(self):
        notification = InAppNotification.objects.create(
            agent=self.agent,
            notification_type=InAppNotification.NotificationType.COLLECTION_MATCH,
            title="New opportunity matches Core Buyers",
            body="A matching opportunity is live.",
            link_url="/board/?city=Scarsdale",
        )

        response = self.client.get(reverse("open_notification", args=[notification.id]))

        self.assertRedirects(response, "/board/?city=Scarsdale", fetch_redirect_response=False)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_notifications_page_shows_unread_items(self):
        InAppNotification.objects.create(
            agent=self.agent,
            notification_type=InAppNotification.NotificationType.COLLECTION_MATCH,
            title="New opportunity matches Core Buyers",
            body="A matching opportunity is live.",
            link_url="/board/?city=Scarsdale",
        )

        response = self.client.get(reverse("notifications"))

        self.assertContains(response, "Notifications")
        self.assertContains(response, "New opportunity matches Core Buyers")
        self.assertContains(response, "Unread")

    def test_collection_alert_fallback_command_dry_run_is_safe(self):
        subscriber = create_agent(
            name="Fallback Subscriber",
            email="fallback@example.com",
            license_number="LIC-FALLBACK",
            is_verified=True,
        )
        subscriber_collection = Collection.objects.create(
            agent=subscriber,
            name="Fallback Collection",
            notifications_enabled=True,
        )
        CollectionFilter.objects.create(collection=subscriber_collection, city="White Plains")
        listing = Listing.objects.create(
            agent=self.agent,
            title="2 Bed / 2.0 Bath in White Plains",
            city="White Plains",
            property_type="House",
            beds=2,
            baths="2.0",
            price_min=700000,
            price_max=850000,
            stage=Listing.Stage.PREMARKET,
            description="White Plains match.",
        )

        output = StringIO()
        call_command("send_collection_match_alerts", "--dry-run", stdout=output)

        self.assertIn("Fallback Collection", output.getvalue())
        self.assertEqual(EmailNotificationLog.objects.count(), 0)
        self.assertEqual(InAppNotification.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_collection_alert_fallback_command_skips_duplicates(self):
        subscriber = create_agent(
            name="Fallback Duplicate",
            email="fallback-dup@example.com",
            license_number="LIC-FALLBACK-DUP",
            is_verified=True,
        )
        subscriber_collection = Collection.objects.create(
            agent=subscriber,
            name="Duplicate Collection",
            notifications_enabled=True,
        )
        CollectionFilter.objects.create(collection=subscriber_collection, city="White Plains")
        listing = Listing.objects.create(
            agent=self.agent,
            title="2 Bed / 2.0 Bath in White Plains",
            city="White Plains",
            property_type="House",
            beds=2,
            baths="2.0",
            price_min=700000,
            price_max=850000,
            stage=Listing.Stage.PREMARKET,
            description="White Plains match.",
        )

        output = StringIO()
        call_command("send_collection_match_alerts", stdout=output)
        self.assertIn("Sent 1 collection alert email(s).", output.getvalue())
        self.assertEqual(EmailNotificationLog.objects.count(), 1)
        self.assertEqual(InAppNotification.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

        second_output = StringIO()
        call_command("send_collection_match_alerts", stdout=second_output)
        self.assertIn("Sent 0 collection alert email(s).", second_output.getvalue())
        self.assertEqual(EmailNotificationLog.objects.count(), 1)
        self.assertEqual(InAppNotification.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)


class SavedListingTests(TestCase):
    def setUp(self):
        self.agent = create_agent(
            name="Saved Agent",
            email="saved@example.com",
            license_number="LIC-SAVED",
        )
        self.listing = Listing.objects.create(
            agent=self.agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Scarsdale premarket.",
        )
        login_agent(self.client, self.agent)

    def test_saving_a_listing_creates_saved_listing(self):
        response = self.client.post(
            reverse("toggle_saved_listing", args=[self.listing.id]),
            {"next": reverse("feed") + "?city=Scarsdale"},
        )

        self.assertRedirects(response, reverse("feed") + "?city=Scarsdale")
        self.assertTrue(
            SavedListing.objects.filter(agent=self.agent, listing=self.listing).exists()
        )

    def test_unsaving_a_listing_removes_saved_listing(self):
        SavedListing.objects.create(agent=self.agent, listing=self.listing)

        response = self.client.post(
            reverse("toggle_saved_listing", args=[self.listing.id]),
            {"next": reverse("feed") + "?city=Scarsdale"},
        )

        self.assertRedirects(response, reverse("feed") + "?city=Scarsdale")
        self.assertFalse(
            SavedListing.objects.filter(agent=self.agent, listing=self.listing).exists()
        )

    def test_uniqueness_behavior_prevents_duplicate_saved_listing(self):
        SavedListing.objects.create(agent=self.agent, listing=self.listing)

        with self.assertRaises(IntegrityError):
            SavedListing.objects.create(agent=self.agent, listing=self.listing)

    def test_feed_renders_star_state_for_saved_and_unsaved_listing(self):
        unsaved_response = self.client.get(reverse("feed"))

        self.assertContains(unsaved_response, "aria-label=\"Save opportunity\"")
        self.assertContains(unsaved_response, "☆")

        SavedListing.objects.create(agent=self.agent, listing=self.listing)

        saved_response = self.client.get(reverse("feed"))

        self.assertContains(saved_response, "aria-label=\"Unsave opportunity\"")
        self.assertContains(saved_response, "★")
        self.assertContains(saved_response, "save-listing-button is-saved")


class WorkspaceTests(TestCase):
    def setUp(self):
        self.agent = create_agent(
            name="Workspace Agent",
            email="workspace@example.com",
            license_number="LIC-WORK",
        )
        self.listing_one = Listing.objects.create(
            agent=self.agent,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Scarsdale premarket.",
        )
        self.listing_two = Listing.objects.create(
            agent=self.agent,
            title="4 Bed / 3.0 Bath in Rye",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2200000,
            price_max=2500000,
            stage=Listing.Stage.PRIVATE,
            description="Rye private listing.",
        )
        login_agent(self.client, self.agent)
        self.collection = Collection.objects.create(agent=self.agent, name="Scarsdale Buyers")
        CollectionFilter.objects.create(
            collection=self.collection,
            city="Scarsdale",
            stage=Listing.Stage.PREMARKET,
            min_beds=3,
            min_baths="2.0",
            min_price=1000000,
            max_price=1300000,
        )
        SavedListing.objects.create(agent=self.agent, listing=self.listing_one)

    def test_workspace_defaults_to_collections_section(self):
        response = self.client.get(reverse("workspace"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Collections")
        self.assertContains(response, "Saved")
        self.assertContains(response, "My Posts")
        self.assertContains(response, "Scarsdale Buyers")
        self.assertContains(response, "Scarsdale, Premarket, 3+ Beds, 2.0+ Baths, $1M–$1.3M")
        self.assertContains(response, "0 items")

    def test_workspace_saved_section_shows_saved_listings(self):
        response = self.client.get(reverse("workspace"), {"section": "saved"})

        self.assertContains(response, "3 Bed / 2.0 Bath in Scarsdale")
        self.assertContains(response, "Add to Collection")
        self.assertContains(response, "id=\"workspace-sheet-overlay\"")
        self.assertNotContains(response, "Choose a collection")

    def test_workspace_my_posts_section_shows_current_agent_posts(self):
        response = self.client.get(reverse("workspace"), {"section": "posts"})

        self.assertContains(response, "3 Bed / 2.0 Bath in Scarsdale")
        self.assertContains(response, "4 Bed / 3.0 Bath in Rye")
        self.assertContains(response, "Last confirmed")
        self.assertContains(response, reverse("edit_listing", args=[self.listing_one.id]))
        self.assertContains(response, reverse("remove_listing", args=[self.listing_one.id]))

    def test_workspace_collection_detail_renders_matching_listings(self):
        CollectionItem.objects.create(collection=self.collection, listing=self.listing_one)
        response = self.client.get(reverse("workspace_collection_detail", args=[self.collection.id]))

        self.assertContains(response, "Scarsdale Buyers")
        self.assertContains(response, "3 Bed / 2.0 Bath in Scarsdale")
        self.assertNotContains(response, "4 Bed / 3.0 Bath in Rye")
        self.assertContains(response, "Alert Settings")

    def test_collection_alert_edit_and_clear_behavior(self):
        response = self.client.post(
            reverse("workspace_collection_detail", args=[self.collection.id]),
            {
                "name": "Updated Buyers",
                "notifications_enabled": "on",
                "city": "Rye",
                "stage": Listing.Stage.PRIVATE,
                "min_beds": "4",
                "min_baths": "3.0",
                "min_price": "2.2M",
                "max_price": "2.6M",
            },
            follow=True,
        )

        self.assertContains(response, "Collection alert updated")
        self.collection.refresh_from_db()
        self.assertEqual(self.collection.name, "Updated Buyers")
        self.assertTrue(self.collection.notifications_enabled)
        self.assertEqual(self.collection.saved_filter.city, "Rye")

        cleared = self.client.post(
            reverse("workspace_collection_detail", args=[self.collection.id]),
            {"clear_alert": "1"},
            follow=True,
        )

        self.assertContains(cleared, "Collection alert cleared")
        self.collection.refresh_from_db()
        self.assertFalse(self.collection.notifications_enabled)
        self.assertFalse(CollectionFilter.objects.filter(collection=self.collection).exists())

    def test_saved_listing_can_be_added_to_existing_collection(self):
        response = self.client.post(
            reverse("add_saved_listing_to_collection", args=[self.listing_one.id]),
            {
                "saved-{}-collection_choice".format(self.listing_one.id): str(self.collection.id),
                "saved-{}-new_collection_name".format(self.listing_one.id): "",
                "next": reverse("workspace") + "?section=saved",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("workspace") + "?section=saved")
        self.assertTrue(CollectionItem.objects.filter(collection=self.collection, listing=self.listing_one).exists())
        self.assertContains(response, "Added to collection")

        refreshed_response = self.client.get(reverse("workspace"), {"section": "saved"})
        self.assertNotContains(refreshed_response, "3 Bed / 2.0 Bath in Scarsdale")

    def test_saved_listing_can_create_new_collection_during_assignment(self):
        response = self.client.post(
            reverse("add_saved_listing_to_collection", args=[self.listing_one.id]),
            {
                "saved-{}-collection_choice".format(self.listing_one.id): "__new__",
                "saved-{}-new_collection_name".format(self.listing_one.id): "Downtown Watchlist",
                "next": reverse("workspace") + "?section=saved",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("workspace") + "?section=saved")
        collection = Collection.objects.get(name="Downtown Watchlist")
        self.assertTrue(CollectionItem.objects.filter(collection=collection, listing=self.listing_one).exists())
        self.assertContains(response, "Added to collection")

    def test_edit_form_prefills_listing_values_for_owner(self):
        response = self.client.get(reverse("edit_listing", args=[self.listing_one.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit Opportunity")
        self.assertContains(response, "value=\"Scarsdale\"", html=False)
        self.assertContains(response, "value=\"3\"", html=False)
        self.assertContains(response, "value=\"2.0\"", html=False)
        self.assertContains(response, "value=\"1000000\"", html=False)
        self.assertContains(response, "value=\"1200000\"", html=False)
        self.assertContains(response, "Scarsdale premarket.")
        self.assertContains(response, "seller_direction_certified")
        self.assertContains(response, "agent_compliance_acknowledged")
        self.assertContains(response, "information_accuracy_certified")

    def test_edit_listing_updates_existing_post_and_redirects_to_workspace(self):
        response = self.client.post(
            reverse("edit_listing", args=[self.listing_one.id]),
            {
                "city": "Rye",
                "beds": 5,
                "baths": "4.0",
                "price_min": "2.1M",
                "price_max": "2.4M",
                "stage": Listing.Stage.PRIVATE,
                "property_type": "House",
                "description": "Updated owner copy.",
                "seller_direction_certified": "on",
                "agent_compliance_acknowledged": "on",
                "information_accuracy_certified": "on",
                "private_marketing_certified": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("workspace") + "?section=posts")
        self.listing_one.refresh_from_db()
        self.assertEqual(self.listing_one.city, "Rye")
        self.assertEqual(self.listing_one.beds, 5)
        self.assertEqual(str(self.listing_one.baths), "4.0")
        self.assertEqual(self.listing_one.price_min, 2100000)
        self.assertEqual(self.listing_one.price_max, 2400000)
        self.assertEqual(self.listing_one.stage, Listing.Stage.PRIVATE)
        self.assertEqual(self.listing_one.description, "Updated owner copy.")
        self.assertEqual(self.listing_one.title, "5 Bed / 4.0 Bath in Rye")
        self.assertContains(response, "Opportunity updated")

    def test_edit_listing_preserves_existing_certification_timestamps(self):
        original_time = timezone.now() - timedelta(days=3)
        self.listing_one.seller_direction_certified = True
        self.listing_one.seller_direction_certified_at = original_time
        self.listing_one.agent_compliance_acknowledged = True
        self.listing_one.agent_compliance_acknowledged_at = original_time
        self.listing_one.information_accuracy_certified = True
        self.listing_one.information_accuracy_certified_at = original_time
        self.listing_one.private_marketing_certified = True
        self.listing_one.private_marketing_certified_at = original_time
        self.listing_one.stage = Listing.Stage.PRIVATE
        self.listing_one.save()

        response = self.client.post(
            reverse("edit_listing", args=[self.listing_one.id]),
            {
                "city": "Rye",
                "beds": 5,
                "baths": "4.0",
                "price_min": "2.1M",
                "price_max": "2.4M",
                "stage": Listing.Stage.PRIVATE,
                "property_type": "House",
                "description": "Updated owner copy.",
                "seller_direction_certified": "on",
                "agent_compliance_acknowledged": "on",
                "information_accuracy_certified": "on",
                "private_marketing_certified": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("workspace") + "?section=posts")
        self.listing_one.refresh_from_db()
        self.assertEqual(self.listing_one.seller_direction_certified_at, original_time)
        self.assertEqual(self.listing_one.agent_compliance_acknowledged_at, original_time)
        self.assertEqual(self.listing_one.information_accuracy_certified_at, original_time)
        self.assertEqual(self.listing_one.private_marketing_certified_at, original_time)

    def test_edit_listing_fails_when_required_certifications_are_removed(self):
        response = self.client.post(
            reverse("edit_listing", args=[self.listing_one.id]),
            {
                "city": "Rye",
                "beds": 5,
                "baths": "4.0",
                "price_min": "2.1M",
                "price_max": "2.4M",
                "stage": Listing.Stage.PRIVATE,
                "property_type": "House",
                "description": "Updated owner copy.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You must certify seller direction before posting.")
        self.assertContains(response, "You must acknowledge compliance responsibility before posting.")
        self.assertContains(response, "You must certify seller direction for a private listing before posting.")
        self.listing_one.refresh_from_db()
        self.assertEqual(self.listing_one.city, "Scarsdale")

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_edit_active_listing_from_non_matching_to_matching_sends_collection_alert_and_in_app_notification(self):
        subscriber = create_agent(
            name="Edit Alert Subscriber",
            email="edit-alert@example.com",
            license_number="LIC-EDIT-ALERT",
            is_verified=True,
        )
        subscriber_collection = Collection.objects.create(
            agent=subscriber,
            name="Rye Watch",
            notifications_enabled=True,
        )
        CollectionFilter.objects.create(
            collection=subscriber_collection,
            city="Rye",
            stage=Listing.Stage.PRIVATE,
            min_beds=5,
        )

        response = self.client.post(
            reverse("edit_listing", args=[self.listing_one.id]),
            {
                "city": "Rye",
                "beds": 5,
                "baths": "4.0",
                "price_min": "2.1M",
                "price_max": "2.4M",
                "stage": Listing.Stage.PRIVATE,
                "property_type": "House",
                "description": "Updated owner copy.",
                "seller_direction_certified": "on",
                "agent_compliance_acknowledged": "on",
                "information_accuracy_certified": "on",
                "private_marketing_certified": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("workspace") + "?section=posts")
        self.listing_one.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Whisper — New Opportunity matches Rye Watch")
        self.assertTrue(
            EmailNotificationLog.objects.filter(
                collection=subscriber_collection,
                listing=self.listing_one,
                notification_type=EmailNotificationLog.NotificationType.COLLECTION_MATCH,
            ).exists()
        )
        notification = InAppNotification.objects.get(agent=subscriber, collection=subscriber_collection, listing=self.listing_one)
        self.assertEqual(notification.notification_type, InAppNotification.NotificationType.COLLECTION_MATCH)
        self.assertFalse(notification.is_read)

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_editing_again_does_not_duplicate_collection_alert(self):
        subscriber = create_agent(
            name="Edit Duplicate Subscriber",
            email="edit-duplicate@example.com",
            license_number="LIC-EDIT-DUPLICATE",
            is_verified=True,
        )
        subscriber_collection = Collection.objects.create(
            agent=subscriber,
            name="Rye Buyers",
            notifications_enabled=True,
        )
        CollectionFilter.objects.create(
            collection=subscriber_collection,
            city="Rye",
            stage=Listing.Stage.PRIVATE,
            min_beds=5,
        )

        edit_payload = {
            "city": "Rye",
            "beds": 5,
            "baths": "4.0",
            "price_min": "2.1M",
            "price_max": "2.4M",
            "stage": Listing.Stage.PRIVATE,
            "property_type": "House",
            "description": "Updated owner copy.",
            "seller_direction_certified": "on",
            "agent_compliance_acknowledged": "on",
            "information_accuracy_certified": "on",
            "private_marketing_certified": "on",
        }

        self.client.post(reverse("edit_listing", args=[self.listing_one.id]), edit_payload, follow=True)
        self.client.post(reverse("edit_listing", args=[self.listing_one.id]), edit_payload, follow=True)

        self.listing_one.refresh_from_db()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            EmailNotificationLog.objects.filter(
                collection=subscriber_collection,
                listing=self.listing_one,
                notification_type=EmailNotificationLog.NotificationType.COLLECTION_MATCH,
            ).count(),
            1,
        )
        self.assertEqual(
            InAppNotification.objects.filter(
                agent=subscriber,
                collection=subscriber_collection,
                listing=self.listing_one,
                notification_type=InAppNotification.NotificationType.COLLECTION_MATCH,
            ).count(),
            1,
        )

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_editing_inactive_listing_does_not_trigger_collection_alerts(self):
        subscriber = create_agent(
            name="Inactive Edit Subscriber",
            email="inactive-edit@example.com",
            license_number="LIC-INACTIVE-EDIT",
            is_verified=True,
        )
        subscriber_collection = Collection.objects.create(
            agent=subscriber,
            name="Inactive Rye Watch",
            notifications_enabled=True,
        )
        CollectionFilter.objects.create(
            collection=subscriber_collection,
            city="Rye",
            stage=Listing.Stage.PRIVATE,
            min_beds=5,
        )
        self.listing_one.is_active = False
        self.listing_one.status = Listing.Status.REMOVED_BY_AGENT
        self.listing_one.save(update_fields=["is_active", "status"])

        response = self.client.post(
            reverse("edit_listing", args=[self.listing_one.id]),
            {
                "city": "Rye",
                "beds": 5,
                "baths": "4.0",
                "price_min": "2.1M",
                "price_max": "2.4M",
                "stage": Listing.Stage.PRIVATE,
                "property_type": "House",
                "description": "Updated owner copy.",
                "seller_direction_certified": "on",
                "agent_compliance_acknowledged": "on",
                "information_accuracy_certified": "on",
                "private_marketing_certified": "on",
            },
            follow=True,
        )

        self.assertRedirects(response, reverse("workspace") + "?section=posts")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(
            EmailNotificationLog.objects.filter(
                collection=subscriber_collection,
                listing=self.listing_one,
                notification_type=EmailNotificationLog.NotificationType.COLLECTION_MATCH,
            ).exists()
        )
        self.assertFalse(
            InAppNotification.objects.filter(
                agent=subscriber,
                collection=subscriber_collection,
                listing=self.listing_one,
                notification_type=InAppNotification.NotificationType.COLLECTION_MATCH,
            ).exists()
        )

    def test_non_owner_cannot_edit_another_users_post(self):
        other_agent = create_agent(
            name="Other Agent",
            email="other-workspace@example.com",
            license_number="LIC-WORK-OTHER",
        )
        other_listing = Listing.objects.create(
            agent=other_agent,
            title="2 Bed / 1.5 Bath in White Plains",
            city="White Plains",
            property_type="Condo",
            beds=2,
            baths="1.5",
            price_min=750000,
            price_max=850000,
            stage=Listing.Stage.PREMARKET,
            description="Other agent listing.",
        )

        response = self.client.get(reverse("edit_listing", args=[other_listing.id]), follow=True)

        self.assertRedirects(response, reverse("workspace") + "?section=posts")
        self.assertContains(response, "Only the opportunity owner can edit this post.")

    def test_owner_can_remove_listing_from_my_posts(self):
        response = self.client.post(
            reverse("remove_listing", args=[self.listing_one.id]),
            follow=True,
        )

        self.assertRedirects(response, reverse("workspace") + "?section=posts")
        self.listing_one.refresh_from_db()
        self.assertFalse(self.listing_one.is_active)
        self.assertContains(response, "Opportunity removed")

        refreshed_response = self.client.get(reverse("workspace"), {"section": "posts"})
        self.assertNotContains(refreshed_response, "3 Bed / 2.0 Bath in Scarsdale")

    def test_non_owner_cannot_remove_another_users_post(self):
        other_agent = create_agent(
            name="Remove Other Agent",
            email="remove-other@example.com",
            license_number="LIC-REMOVE-OTHER",
        )
        other_listing = Listing.objects.create(
            agent=other_agent,
            title="2 Bed / 1.5 Bath in White Plains",
            city="White Plains",
            property_type="Condo",
            beds=2,
            baths="1.5",
            price_min=750000,
            price_max=850000,
            stage=Listing.Stage.PREMARKET,
            description="Other agent listing.",
        )

        response = self.client.post(reverse("remove_listing", args=[other_listing.id]), follow=True)

        self.assertRedirects(response, reverse("workspace") + "?section=posts")
        other_listing.refresh_from_db()
        self.assertTrue(other_listing.is_active)
        self.assertContains(response, "Only the opportunity owner can remove this post.")


class ListingCheckInTests(TestCase):
    def setUp(self):
        self.agent = create_agent(
            name="Check In Agent",
            email="checkin@example.com",
            license_number="LIC-CHECKIN",
        )
        self.listing = Listing.objects.create(
            agent=self.agent,
            title="4 Bed / 3.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=1900000,
            price_max=2100000,
            stage=Listing.Stage.PRIVATE,
            description="Check-in listing.",
        )
        login_agent(self.client, self.agent)

    def test_reminder_selection_logic_uses_schedule(self):
        now = timezone.now()
        first_due = Listing.objects.create(
            agent=self.agent,
            title="First Due",
            city="Rye",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="First due.",
            last_confirmed_at=now - timedelta(days=7),
            reminder_count=0,
        )
        second_due = Listing.objects.create(
            agent=self.agent,
            title="Second Due",
            city="Bronxville",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2200000,
            price_max=2500000,
            stage=Listing.Stage.PRIVATE,
            description="Second due.",
            last_confirmed_at=now - timedelta(days=21),
            reminder_count=1,
        )
        not_due = Listing.objects.create(
            agent=self.agent,
            title="Not Due",
            city="Larchmont",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=1800000,
            price_max=2000000,
            stage=Listing.Stage.PRIVATE,
            description="Not due.",
            last_confirmed_at=now - timedelta(days=10),
            reminder_count=0,
        )

        due_ids = {listing.id for listing in get_listings_requiring_checkin(now=now)}

        self.assertIn(first_due.id, due_ids)
        self.assertNotIn(second_due.id, due_ids)
        self.assertNotIn(not_due.id, due_ids)
        self.assertEqual(get_reminder_due_at(first_due), get_stale_at(first_due) - timedelta(days=7))
        self.assertEqual(get_freshness_state_label(first_due, now=now), "Refresh Due")
        self.assertEqual(get_freshness_state_label(not_due, now=now), "Fresh")

    def test_premarket_stale_deadline_is_earlier_than_private(self):
        now = timezone.now()
        premarket_listing = Listing.objects.create(
            agent=self.agent,
            title="Premarket Timing",
            city="Rye",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Premarket timing.",
            last_confirmed_at=now,
        )
        private_listing = Listing.objects.create(
            agent=self.agent,
            title="Private Timing",
            city="Rye",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PRIVATE,
            description="Private timing.",
            last_confirmed_at=now,
        )

        self.assertLess(get_stale_at(premarket_listing), get_stale_at(private_listing))
        self.assertLess(get_reminder_due_at(premarket_listing), get_reminder_due_at(private_listing))

    def test_grouping_listings_per_agent_email(self):
        other_agent = create_agent(
            name="Second Agent",
            email="second-checkin@example.com",
            license_number="LIC-CHECKIN-2",
        )
        other_listing = Listing.objects.create(
            agent=other_agent,
            title="Second Agent Listing",
            city="Rye",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1100000,
            price_max=1300000,
            stage=Listing.Stage.PREMARKET,
            description="Grouped.",
        )

        grouped = group_listings_by_agent_email([self.listing, other_listing])

        self.assertEqual(len(grouped["checkin@example.com"]), 1)
        self.assertEqual(len(grouped["second-checkin@example.com"]), 1)

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_management_command_groups_listings_and_updates_tracking(self):
        now = timezone.now()
        Listing.objects.filter(pk=self.listing.pk).update(last_confirmed_at=now - timedelta(days=14))
        second_listing = Listing.objects.create(
            agent=self.agent,
            title="Rye Colonial",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2200000,
            price_max=2300000,
            stage=Listing.Stage.PREMARKET,
            description="Second reminder listing.",
            last_confirmed_at=now - timedelta(days=7),
        )

        call_command("send_listing_checkins")

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Whisper Opportunity Check-In")
        self.assertIn("Rye Colonial", mail.outbox[0].body)
        self.assertIn("4 Bed / 3.0 Bath in Scarsdale", mail.outbox[0].body)

        self.listing.refresh_from_db()
        second_listing.refresh_from_db()
        self.assertEqual(self.listing.reminder_count, 1)
        self.assertEqual(second_listing.reminder_count, 1)
        self.assertIsNotNone(self.listing.last_reminder_sent_at)

    def test_reminder_is_not_sent_before_seven_day_threshold(self):
        now = timezone.now()
        listing = Listing.objects.create(
            agent=self.agent,
            title="Not Yet Due",
            city="Rye",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Not due yet.",
            last_confirmed_at=now - timedelta(days=6),
        )

        self.assertFalse(should_send_checkin_for_listing(listing, now=now))

    def test_single_deadline_listing_is_not_reselected_daily_after_reminder(self):
        now = timezone.now()
        listing = Listing.objects.create(
            agent=self.agent,
            title="Refresh Listing",
            city="Rye",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Reminder cadence.",
            last_confirmed_at=now - timedelta(days=7),
        )

        self.assertTrue(should_send_checkin_for_listing(listing, now=now))
        listing.last_reminder_sent_at = now
        listing.reminder_count = 1
        listing.save(update_fields=["last_reminder_sent_at", "reminder_count"])

        next_day = now + timedelta(days=1)
        self.assertFalse(should_send_checkin_for_listing(listing, now=next_day))

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_grouped_reminder_email_includes_stale_deadline(self):
        now = timezone.now()
        Listing.objects.filter(pk=self.listing.pk).update(last_confirmed_at=now - timedelta(days=14))
        second_due_listing = Listing.objects.create(
            agent=self.agent,
            title="Rye Colonial",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2200000,
            price_max=2300000,
            stage=Listing.Stage.PRIVATE,
            description="Refresh due listing.",
            last_confirmed_at=now - timedelta(days=14),
        )

        call_command("send_listing_checkins")

        self.assertEqual(len(mail.outbox), 1)
        body = mail.outbox[0].body
        self.assertIn("These opportunities will expire soon unless you refresh them.", body)
        self.assertIn("Stale on:", body)
        self.assertIn("Refresh Listing:", body)
        self.assertNotIn("Moved to MLS:", body)
        self.assertIn(second_due_listing.title, body)
        self.assertIn(self.listing.title, body)

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_grouped_email_renders_mixed_premarket_and_private_actions(self):
        now = timezone.now()
        premarket_listing = Listing.objects.create(
            agent=self.agent,
            title="Premarket Mixed",
            city="Rye",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Premarket mixed.",
            last_confirmed_at=now - timedelta(days=7),
        )
        Listing.objects.filter(pk=self.listing.pk).update(last_confirmed_at=now - timedelta(days=14))

        call_command("send_listing_checkins")

        body = mail.outbox[0].body
        self.assertIn("Still Premarket:", body)
        self.assertIn("Moved to MLS:", body)
        self.assertIn("Refresh Listing:", body)

    def test_confirmation_endpoint_resets_listing(self):
        Listing.objects.filter(pk=self.listing.pk).update(
            reminder_count=2,
            last_reminder_sent_at=timezone.now() - timedelta(days=1),
        )
        token = build_signed_listing_token(self.listing, "confirm")

        response = self.client.get(reverse("confirm_listing_from_email", args=[token]))

        self.assertEqual(response.status_code, 200)
        self.listing.refresh_from_db()
        self.assertEqual(self.listing.reminder_count, 0)
        self.assertIsNone(self.listing.last_reminder_sent_at)
        self.assertTrue(self.listing.is_active)
        self.assertContains(response, "Thanks! Your opportunity has been refreshed.")

    def test_removal_endpoint_marks_listing_removed(self):
        token = build_signed_listing_token(self.listing, "remove")

        response = self.client.get(reverse("remove_listing_from_email", args=[token]))

        self.assertEqual(response.status_code, 200)
        self.listing.refresh_from_db()
        self.assertFalse(self.listing.is_active)
        self.assertEqual(self.listing.status, Listing.Status.REMOVED_BY_AGENT)
        self.assertIsNotNone(self.listing.removed_at)
        self.assertContains(response, "Your opportunity has been removed.")

    def test_remove_after_stale_deadline_shows_expired_page(self):
        stale_listing = Listing.objects.create(
            agent=self.agent,
            title="Expired Remove Listing",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2100000,
            price_max=2300000,
            stage=Listing.Stage.PRIVATE,
            description="Expired remove link.",
            last_confirmed_at=timezone.now() - timedelta(days=21),
        )
        token = build_signed_listing_token(stale_listing, "remove")

        response = self.client.get(reverse("remove_listing_from_email", args=[token]))

        self.assertEqual(response.status_code, 410)
        stale_listing.refresh_from_db()
        self.assertFalse(stale_listing.is_active)
        self.assertEqual(stale_listing.status, Listing.Status.STALE)
        self.assertContains(response, "This refresh link has expired", status_code=410)

    def test_premarket_moved_to_mls_action_removes_listing_with_distinct_reason(self):
        listing = Listing.objects.create(
            agent=self.agent,
            title="Moved To MLS",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2100000,
            price_max=2300000,
            stage=Listing.Stage.PREMARKET,
            description="Moved to MLS action.",
            last_confirmed_at=timezone.now() - timedelta(days=7),
        )
        token = build_signed_listing_token(listing, "moved_to_mls")

        response = self.client.get(reverse("move_listing_to_mls_from_email", args=[token]))

        self.assertEqual(response.status_code, 200)
        listing.refresh_from_db()
        self.assertFalse(listing.is_active)
        self.assertEqual(listing.status, Listing.Status.REMOVED_BY_AGENT)
        self.assertEqual(listing.removed_reason, Listing.REMOVED_REASON_MOVED_TO_MLS)
        self.assertContains(response, "moved to MLS")

    @patch("listings.views.send_collection_match_alerts_for_listing")
    def test_premarket_moved_to_mls_after_stale_deadline_shows_expired_result(self, mock_send_collection_match_alerts):
        listing = Listing.objects.create(
            agent=self.agent,
            title="Expired MLS Move",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2100000,
            price_max=2300000,
            stage=Listing.Stage.PREMARKET,
            description="Expired MLS move.",
            last_confirmed_at=timezone.now() - timedelta(days=14),
        )
        token = build_signed_listing_token(listing, "moved_to_mls")

        response = self.client.get(reverse("move_listing_to_mls_from_email", args=[token]))

        self.assertEqual(response.status_code, 410)
        listing.refresh_from_db()
        self.assertFalse(listing.is_active)
        self.assertEqual(listing.status, Listing.Status.STALE)
        self.assertContains(response, "This refresh link has expired", status_code=410)
        self.assertFalse(mock_send_collection_match_alerts.called)

    def test_no_response_causes_stale_takedown_at_deadline(self):
        stale_listing = Listing.objects.create(
            agent=self.agent,
            title="Expired Listing",
            city="Bronxville",
            property_type="House",
            beds=5,
            baths="4.0",
            price_min=3000000,
            price_max=3200000,
            stage=Listing.Stage.PRIVATE,
            description="Expired freshness window.",
            last_confirmed_at=timezone.now() - timedelta(days=21),
        )

        deactivated_count = deactivate_stale_listings()

        self.assertEqual(deactivated_count, 1)
        stale_listing.refresh_from_db()
        self.assertFalse(stale_listing.is_active)
        self.assertEqual(stale_listing.status, Listing.Status.STALE)
        self.assertIsNotNone(stale_listing.removed_at)

    def test_stale_listing_is_removed_from_live_feed_queries(self):
        stale_listing = Listing.objects.create(
            agent=self.agent,
            title="Stale Board Listing",
            city="Bronxville",
            property_type="House",
            beds=5,
            baths="4.0",
            price_min=3000000,
            price_max=3200000,
            stage=Listing.Stage.PRIVATE,
            description="Should not appear on the live board.",
            last_confirmed_at=timezone.now() - timedelta(days=24),
        )

        deactivate_stale_listings()
        response = self.client.get(reverse("feed"))

        self.assertNotContains(response, "Stale Board Listing")
        stale_listing.refresh_from_db()
        self.assertFalse(stale_listing.is_active)
        self.assertEqual(stale_listing.status, Listing.Status.STALE)

    @patch("listings.views.send_collection_match_alerts_for_listing")
    def test_confirm_after_stale_deadline_shows_expired_page_and_does_not_reactivate(self, mock_send_collection_match_alerts):
        stale_listing = Listing.objects.create(
            agent=self.agent,
            title="Reactivatable Listing",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2100000,
            price_max=2300000,
            stage=Listing.Stage.PRIVATE,
            description="Was stale but can return live.",
            last_confirmed_at=timezone.now() - timedelta(days=21),
        )
        token = build_signed_listing_token(stale_listing, "confirm")

        response = self.client.get(reverse("confirm_listing_from_email", args=[token]))

        self.assertEqual(response.status_code, 410)
        stale_listing.refresh_from_db()
        self.assertFalse(stale_listing.is_active)
        self.assertEqual(stale_listing.status, Listing.Status.STALE)
        self.assertContains(response, "This refresh link has expired", status_code=410)
        self.assertFalse(mock_send_collection_match_alerts.called)

    def test_listing_remains_live_before_stale_deadline(self):
        live_listing = Listing.objects.create(
            agent=self.agent,
            title="Live Listing",
            city="Larchmont",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=1800000,
            price_max=2000000,
            stage=Listing.Stage.PRIVATE,
            description="Still live.",
            last_confirmed_at=timezone.now() - timedelta(days=20),
        )

        deactivated_count = deactivate_stale_listings()

        self.assertEqual(deactivated_count, 0)
        live_listing.refresh_from_db()
        self.assertTrue(live_listing.is_active)
        self.assertEqual(live_listing.status, Listing.Status.ACTIVE)

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    @patch("listings.checkins.send_email", side_effect=RuntimeError("send failed"))
    def test_stale_deactivation_still_occurs_when_sending_fails(self, mock_send_email):
        stale_listing = Listing.objects.create(
            agent=self.agent,
            title="Expired Listing",
            city="Bronxville",
            property_type="House",
            beds=5,
            baths="4.0",
            price_min=3000000,
            price_max=3200000,
            stage=Listing.Stage.PRIVATE,
            description="Expired freshness window.",
            last_confirmed_at=timezone.now() - timedelta(days=21),
        )
        Listing.objects.filter(pk=self.listing.pk).update(last_confirmed_at=timezone.now() - timedelta(days=14))

        with self.assertRaises(RuntimeError):
            send_grouped_listing_checkins()

        stale_listing.refresh_from_db()
        self.assertFalse(stale_listing.is_active)
        self.assertEqual(stale_listing.status, Listing.Status.STALE)

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_management_command_dry_run_prints_grouped_recipients_and_states_without_mutation(self):
        stale_listing = Listing.objects.create(
            agent=self.agent,
            title="Expired Listing",
            city="Bronxville",
            property_type="House",
            beds=5,
            baths="4.0",
            price_min=3000000,
            price_max=3200000,
            stage=Listing.Stage.PRIVATE,
            description="Expired freshness window.",
            last_confirmed_at=timezone.now() - timedelta(days=21),
        )
        Listing.objects.filter(pk=self.listing.pk).update(last_confirmed_at=timezone.now() - timedelta(days=14))
        due_listing = Listing.objects.create(
            agent=self.agent,
            title="Due Listing",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2200000,
            price_max=2300000,
            stage=Listing.Stage.PRIVATE,
            description="Refresh due listing.",
            last_confirmed_at=timezone.now() - timedelta(days=14),
        )
        out = StringIO()

        call_command("send_listing_checkins", "--dry-run", stdout=out)

        output = out.getvalue()
        self.assertIn("Dry run: listing check-in emails would be sent to:", output)
        self.assertIn("checkin@example.com", output)
        self.assertIn("Refresh Due", output)
        self.assertNotIn("Expired Listing", output)
        stale_listing.refresh_from_db()
        due_listing.refresh_from_db()
        self.assertTrue(stale_listing.is_active)
        self.assertTrue(due_listing.is_active)

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_email_provider_abstraction_uses_selected_provider(self):
        response = send_email(
            to_email="provider@example.com",
            subject="Provider Test",
            html_body="<p>Hello</p>",
            text_body="Hello",
        )

        self.assertEqual(response, 1)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_PROVIDER="emailit")
    def test_email_provider_selection_supports_emailit(self):
        provider = get_email_provider()

        self.assertEqual(provider.__class__.__name__, "EmailitProvider")

    @override_settings(EMAIL_PROVIDER="smtp", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_management_command_dry_run_prints_agents_and_listings_without_sending(self):
        Listing.objects.filter(pk=self.listing.pk).update(last_confirmed_at=timezone.now() - timedelta(days=14))
        second_listing = Listing.objects.create(
            agent=self.agent,
            title="Bronxville New Build",
            city="Bronxville",
            property_type="House",
            beds=5,
            baths="4.0",
            price_min=3000000,
            price_max=3200000,
            stage=Listing.Stage.PRIVATE,
            description="Dry run listing.",
            last_confirmed_at=timezone.now() - timedelta(days=14),
        )
        out = StringIO()

        call_command("send_listing_checkins", "--dry-run", stdout=out)

        output = out.getvalue()
        self.assertIn("Dry run: listing check-in emails would be sent to:", output)
        self.assertIn("checkin@example.com", output)
        self.assertIn("4 Bed / 3.0 Bath in Scarsdale | Scarsdale | Private | Refresh Due", output)
        self.assertIn(f"{second_listing.title} | Bronxville | Private | Refresh Due", output)
        self.assertEqual(len(mail.outbox), 0)


class EmailRenderingTests(TestCase):
    def test_render_email_returns_html_and_text_content(self):
        from services.email.render import render_email

        html_body, text_body = render_email(
            html_template="emails/account/verify_email.html",
            text_template="emails/account/verify_email.txt",
            context={
                "agent_name": "Samantha Torres",
                "verification_url": "https://example.com/verify",
            },
        )

        self.assertIn("Samantha Torres", html_body)
        self.assertIn("https://example.com/verify", html_body)
        self.assertIn("Samantha Torres", text_body)
        self.assertIn("https://example.com/verify", text_body)

    def test_account_verification_message_builder_returns_expected_subject_and_body(self):
        from services.email.messages import build_account_verification_email

        subject, html_body, text_body = build_account_verification_email(
            agent_name="Samantha Torres",
            verification_url="https://example.com/verify",
        )

        self.assertEqual(subject, "Verify your Whisper email")
        self.assertIn("Please verify your Whisper email.", html_body)
        self.assertIn("https://example.com/verify", html_body)
        self.assertIn("https://example.com/verify", text_body)

    def test_checkin_message_builder_returns_expected_subject_and_body(self):
        from services.email.messages import build_listing_checkin_group_email

        subject, html_body, text_body = build_listing_checkin_group_email(
            agent_name="Samantha Torres",
            listings=[
                {
                    "descriptor": "Rye Opportunity — Rye — Private — $1M–$2M",
                    "confirm_url": "https://example.com/confirm",
                    "remove_url": "https://example.com/remove",
                    "last_validated_label": "Mar 1, 2026",
                    "stale_at_label": "Mar 8, 2026",
                    "primary_action_label": "Refresh Listing",
                }
            ],
        )

        self.assertEqual(subject, "Whisper Opportunity Check-In")
        self.assertIn("Rye Opportunity", html_body)
        self.assertIn("These opportunities will expire soon unless you refresh them.", html_body)
        self.assertIn("https://example.com/confirm", html_body)
        self.assertIn("https://example.com/remove", text_body)
        self.assertIn("Last validated: Mar 1, 2026", text_body)
        self.assertIn("Stale on: Mar 8, 2026", text_body)


class ListingFreshnessTests(TestCase):
    def setUp(self):
        self.owner = create_agent(
            name="Owner Agent",
            email="owner@example.com",
            license_number="LIC-OWNER",
        )
        self.other_agent = create_agent(
            name="Other Agent",
            email="other@example.com",
            license_number="LIC-OTHER",
        )
        login_agent(self.client, self.owner)

    def test_listing_sets_last_confirmed_at_when_created(self):
        before_create = timezone.now()
        listing = Listing.objects.create(
            agent=self.owner,
            title="3 Bed / 2.0 Bath in Scarsdale",
            city="Scarsdale",
            property_type="House",
            beds=3,
            baths="2.0",
            price_min=1000000,
            price_max=1200000,
            stage=Listing.Stage.PREMARKET,
            description="Fresh listing.",
        )

        self.assertIsNotNone(listing.last_confirmed_at)
        self.assertGreaterEqual(listing.last_confirmed_at, before_create)

    def test_listing_owner_can_confirm_still_available(self):
        listing = Listing.objects.create(
            agent=self.owner,
            title="4 Bed / 3.0 Bath in Rye",
            city="Rye",
            property_type="House",
            beds=4,
            baths="3.0",
            price_min=2000000,
            price_max=2300000,
            stage=Listing.Stage.PRIVATE,
            description="Owner listing.",
        )
        old_time = timezone.now() - timedelta(days=10)
        Listing.objects.filter(pk=listing.pk).update(last_confirmed_at=old_time)

        response = self.client.post(
            reverse("confirm_listing_availability", args=[listing.id]),
            {"next": reverse("feed") + "?city=Rye"},
        )

        self.assertRedirects(response, reverse("feed") + "?city=Rye")
        listing.refresh_from_db()
        self.assertGreater(listing.last_confirmed_at, old_time)

    def test_non_owner_cannot_confirm_listing(self):
        listing = Listing.objects.create(
            agent=self.other_agent,
            title="2 Bed / 1.5 Bath in White Plains",
            city="White Plains",
            property_type="Condo",
            beds=2,
            baths="1.5",
            price_min=750000,
            price_max=850000,
            stage=Listing.Stage.PREMARKET,
            description="Other agent listing.",
        )
        old_time = timezone.now() - timedelta(days=7)
        Listing.objects.filter(pk=listing.pk).update(last_confirmed_at=old_time)

        response = self.client.post(
            reverse("confirm_listing_availability", args=[listing.id]),
            {"next": reverse("feed")},
            follow=True,
        )

        self.assertRedirects(response, reverse("feed"))
        listing.refresh_from_db()
        self.assertEqual(listing.last_confirmed_at, old_time)
        self.assertContains(response, "Only the opportunity owner can confirm availability.")
