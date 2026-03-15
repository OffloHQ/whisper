from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

from services.email import send_email
from services.email.messages import build_listing_checkin_group_email

from .models import Listing
from .utils import format_listing_price

TOKEN_SALT = "listings.checkin"
OPTIONAL_UPDATE_STATE = "optional"
REQUIRED_UPDATE_STATE = "required"


def get_optional_update_due_at(listing):
    return listing.last_confirmed_at + timedelta(
        days=getattr(settings, "LISTING_FRESHNESS_OPTIONAL_UPDATE_DAYS", 14)
    )


def get_required_update_due_at(listing):
    return listing.last_confirmed_at + timedelta(
        days=getattr(settings, "LISTING_FRESHNESS_REQUIRED_UPDATE_DAYS", 21)
    )


def get_required_update_expires_at(listing):
    return get_required_update_due_at(listing) + timedelta(
        days=getattr(settings, "LISTING_FRESHNESS_REQUIRED_UPDATE_GRACE_DAYS", 3)
    )


def get_freshness_state(listing, now=None):
    now = now or timezone.now()
    if now >= get_required_update_due_at(listing):
        return REQUIRED_UPDATE_STATE
    if now >= get_optional_update_due_at(listing):
        return OPTIONAL_UPDATE_STATE
    return None


def get_freshness_state_due_at(listing, now=None):
    state = get_freshness_state(listing, now=now)
    if state == REQUIRED_UPDATE_STATE:
        return get_required_update_due_at(listing)
    if state == OPTIONAL_UPDATE_STATE:
        return get_optional_update_due_at(listing)
    return None


def get_freshness_state_label(listing, now=None):
    state = get_freshness_state(listing, now=now)
    if state == REQUIRED_UPDATE_STATE:
        return "Required Update"
    if state == OPTIONAL_UPDATE_STATE:
        return "Soft Update"
    return "Fresh"


def is_listing_stale(listing, now=None):
    now = now or timezone.now()
    return now >= get_required_update_expires_at(listing)


def deactivate_stale_listings(now=None):
    now = now or timezone.now()
    listings = (
        Listing.objects.filter(
            is_active=True,
            status=Listing.Status.ACTIVE,
            removed_at__isnull=True,
        )
        .select_related("agent")
        .order_by("pk")
    )
    stale_listings = [listing for listing in listings if is_listing_stale(listing, now=now)]
    for listing in stale_listings:
        listing.mark_stale()
    return len(stale_listings)


def should_send_checkin_for_listing(listing, now=None):
    now = now or timezone.now()
    if is_listing_stale(listing, now=now):
        return False

    state_due_at = get_freshness_state_due_at(listing, now=now)
    if state_due_at is None:
        return False

    if listing.last_reminder_sent_at is None:
        return True

    return listing.last_reminder_sent_at < state_due_at


def get_listings_requiring_checkin(now=None, *, deactivate_stale_on_run=True):
    now = now or timezone.now()
    if deactivate_stale_on_run:
        deactivate_stale_listings(now=now)
    listings = (
        Listing.objects.filter(
            is_active=True,
            status=Listing.Status.ACTIVE,
            removed_at__isnull=True,
        )
        .select_related("agent")
        .order_by("agent__email", "pk")
    )
    due_listings = []
    for listing in listings:
        if should_send_checkin_for_listing(listing, now=now):
            due_listings.append(listing)
    return due_listings


def group_listings_by_agent_email(listings):
    grouped = defaultdict(list)
    for listing in listings:
        grouped[listing.agent.email].append(listing)
    return grouped


def build_signed_listing_token(listing, action):
    return signing.dumps(
        {
            "listing_id": listing.id,
            "agent_id": listing.agent_id,
            "action": action,
        },
        salt=TOKEN_SALT,
    )


def load_signed_listing_token(token, action):
    payload = signing.loads(
        token,
        salt=TOKEN_SALT,
        max_age=getattr(settings, "LISTING_CHECKIN_LINK_MAX_AGE", 60 * 60 * 24 * 90),
    )
    if payload.get("action") != action:
        raise signing.BadSignature("Invalid listing action.")
    return payload


def build_checkin_links(listing):
    base_url = settings.SITE_BASE_URL.rstrip("/")
    confirm_token = build_signed_listing_token(listing, "confirm")
    remove_token = build_signed_listing_token(listing, "remove")
    return {
        "confirm_url": f"{base_url}/confirm-listing/{confirm_token}/",
        "remove_url": f"{base_url}/remove-listing/{remove_token}/",
    }


def build_grouped_checkin_email(agent, listings, now=None):
    email_listings = []
    now = now or timezone.now()
    for listing in listings:
        links = build_checkin_links(listing)
        price_label = f"{format_listing_price(listing.price_min)}–{format_listing_price(listing.price_max)}"
        freshness_state = get_freshness_state(listing, now=now)
        is_required = freshness_state == REQUIRED_UPDATE_STATE
        email_listings.append(
            {
                "descriptor": f"{listing.title} — {listing.city} — {listing.get_stage_display()} — {price_label}",
                "confirm_url": links["confirm_url"],
                "remove_url": links["remove_url"],
                "last_validated_label": (
                    "Required Update"
                    if is_required
                    else listing.last_confirmed_at.strftime("%b %-d, %Y")
                ),
                "is_required": is_required,
                "primary_action_label": "Validate Now" if is_required else "Update",
                "state_label": get_freshness_state_label(listing, now=now),
            }
        )
    return build_listing_checkin_group_email(
        agent_name=agent.name,
        listings=email_listings,
    )


def send_grouped_listing_checkins(now=None, *, deactivate_stale_on_run=True):
    now = now or timezone.now()
    due_listings = get_listings_requiring_checkin(now=now, deactivate_stale_on_run=deactivate_stale_on_run)
    grouped = group_listings_by_agent_email(due_listings)
    sent_count = 0

    for listings in grouped.values():
        agent = listings[0].agent
        if not agent.freshness_reminder_emails:
            continue
        subject, html_body, text_body = build_grouped_checkin_email(agent, listings, now=now)
        send_email(
            to_email=agent.email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )
        for listing in listings:
            listing.last_reminder_sent_at = now
            listing.reminder_count += 1
        Listing.objects.bulk_update(listings, ["last_reminder_sent_at", "reminder_count"])
        sent_count += 1

    return sent_count
