import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def generate_access_token():
    return secrets.token_urlsafe(32)


class AgentUser(models.Model):
    class SignupStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        PENDING_CONTACT = "pending_contact", "Pending Contact"
        MANUAL_REVIEW = "manual_review", "Manual Review"

    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    state = models.CharField(max_length=32, blank=True)
    license_number = models.CharField(max_length=100, unique=True)
    brokerage = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    is_verified = models.BooleanField(default=False)
    show_email_to_agents = models.BooleanField(default=False)
    freshness_reminder_emails = models.BooleanField(default=True)
    collection_match_emails = models.BooleanField(default=True)
    product_update_emails = models.BooleanField(default=False)
    terms_accepted = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_version = models.CharField(max_length=64, blank=True, default="")
    privacy_accepted = models.BooleanField(default=False)
    privacy_accepted_at = models.DateTimeField(null=True, blank=True)
    privacy_version = models.CharField(max_length=64, blank=True, default="")
    legal_acceptance_ip = models.GenericIPAddressField(null=True, blank=True)
    legal_acceptance_user_agent = models.TextField(blank=True, default="")
    signup_status = models.CharField(
        max_length=32,
        choices=SignupStatus.choices,
        default=SignupStatus.ACTIVE,
    )
    is_active = models.BooleanField(default=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.email and not self.emails.filter(email=self.email).exists():
            self.emails.create(
                email=self.email,
                is_verified=True,
                is_primary=not self.emails.filter(is_primary=True).exists(),
                verified_at=self.created_at or timezone.now(),
            )

    def __str__(self) -> str:
        return f"{self.name} ({self.license_number})"

    def deactivate_account(self):
        self.is_active = False
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_active", "deleted_at"])
        self.listings.filter(is_active=True).update(
            is_active=False,
            status=Listing.Status.REMOVED_BY_AGENT,
            removed_at=self.deleted_at,
            removed_reason="account_deleted",
        )

    def get_primary_email(self):
        return self.emails.filter(is_primary=True).first()

    def get_primary_phone(self):
        return self.phones.order_by("created_at", "pk").first()

    @property
    def primary_phone(self):
        return self.get_primary_phone()

    @property
    def contact_email(self):
        primary_email = self.get_primary_email()
        if self.show_email_to_agents and primary_email and primary_email.is_verified:
            return primary_email.email
        return ""

    @property
    def has_completed_legal_acceptance(self):
        return self.terms_accepted and self.privacy_accepted


class AccessRequest(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        LINK_SENT = "link_sent", "Link Sent"
        MANUAL_REVIEW = "manual_review", "Manual Review"
        WAITLIST = "waitlist", "Wait List"
        COMPLETED = "completed", "Completed"

    class QueueType(models.TextChoices):
        NONE = "", "None"
        MANUAL_REVIEW = "manual_review", "Manual Review"
        WAITLIST = "waitlist", "Wait List"

    class DecisionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        COMPLETED = "completed", "Completed"

    class Reason(models.TextChoices):
        NONE = "", "None"
        NAME_MISMATCH = "name_mismatch", "Name mismatch"
        PROVIDER_ERROR = "provider_error", "Provider error"
        NO_MATCH = "no_match", "No match"
        EXPIRED = "expired", "Expired"
        DUPLICATE_LICENSE = "duplicate_license", "Duplicate license"
        MALFORMED_PROVIDER_RESPONSE = "malformed_provider_response", "Malformed provider response"
        UNSUPPORTED_STATE = "unsupported_state", "Unsupported state"
        UNSUPPORTED_COUNTY = "unsupported_county", "Unsupported county"
        UNSUPPORTED_BOROUGH = "unsupported_borough", "Unsupported borough"
        UNSUPPORTED_MARKET = "unsupported_market", "Unsupported market"

    class NotificationType(models.TextChoices):
        APPROVAL = "approval", "Approval"
        REJECTION = "rejection", "Rejection"
        WAITLIST = "waitlist", "Wait List"

    class VerificationStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        MANUAL_REVIEW = "manual_review", "Manual Review"
        UNSUPPORTED_STATE = "unsupported_state", "Unsupported State"
        PROVIDER_ERROR = "provider_error", "Provider Error"
        NO_MATCH = "no_match", "No Match"
        EXPIRED = "expired", "Expired"
        NAME_MISMATCH = "name_mismatch", "Name Mismatch"

    email = models.EmailField(unique=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.REQUESTED)
    full_name = models.CharField(max_length=255, blank=True)
    state = models.CharField(max_length=32, blank=True)
    county = models.CharField(max_length=120, blank=True)
    borough = models.CharField(max_length=120, blank=True)
    market_area = models.CharField(max_length=120, blank=True)
    license_number = models.CharField(max_length=100, blank=True)
    queue_type = models.CharField(max_length=32, choices=QueueType.choices, blank=True, default=QueueType.NONE)
    decision_status = models.CharField(
        max_length=32,
        choices=DecisionStatus.choices,
        default=DecisionStatus.PENDING,
    )
    reason_code = models.CharField(max_length=64, choices=Reason.choices, blank=True, default=Reason.NONE)
    verification_status = models.CharField(
        max_length=32,
        choices=VerificationStatus.choices,
        default=VerificationStatus.PENDING,
    )
    verification_provider = models.CharField(max_length=64, blank=True)
    verification_attempted_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_reason = models.CharField(max_length=255, blank=True)
    requires_manual_review = models.BooleanField(default=False)
    verification_payload = models.JSONField(default=dict, blank=True)
    matched_license_name = models.CharField(max_length=255, blank=True)
    matched_license_type = models.CharField(max_length=255, blank=True)
    matched_business_name = models.CharField(max_length=255, blank=True)
    matched_business_city = models.CharField(max_length=120, blank=True)
    matched_business_state = models.CharField(max_length=32, blank=True)
    matched_expiration_date = models.DateField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_access_requests",
    )
    manual_decision_reason = models.TextField(blank=True)
    approval_email_sent_at = models.DateTimeField(null=True, blank=True)
    rejection_email_sent_at = models.DateTimeField(null=True, blank=True)
    waitlist_email_sent_at = models.DateTimeField(null=True, blank=True)
    last_notification_type = models.CharField(max_length=32, choices=NotificationType.choices, blank=True)
    last_notification_sent_at = models.DateTimeField(null=True, blank=True)
    signup_sent_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        permissions = (
            ("can_access_intake_portal", "Can access intake portal"),
            ("can_review_manual_requests", "Can review manual intake requests"),
            ("can_manage_waitlist", "Can manage intake waitlist"),
        )

    def __str__(self) -> str:
        return self.email

    def route_to_manual_review(self, reason_code, reason_text=""):
        self.status = self.Status.MANUAL_REVIEW
        self.queue_type = self.QueueType.MANUAL_REVIEW
        self.decision_status = self.DecisionStatus.PENDING
        self.reason_code = reason_code
        self.verification_reason = reason_text or self.verification_reason
        self.requires_manual_review = True

    def route_to_waitlist(self, reason_code, reason_text=""):
        self.status = self.Status.WAITLIST
        self.queue_type = self.QueueType.WAITLIST
        self.decision_status = self.DecisionStatus.PENDING
        self.reason_code = reason_code
        self.verification_reason = reason_text or self.verification_reason
        self.requires_manual_review = False

    def mark_review_decision(self, *, decision_status, reviewed_by=None, decision_reason=""):
        self.decision_status = decision_status
        self.reviewed_at = timezone.now()
        self.reviewed_by = reviewed_by
        self.manual_decision_reason = decision_reason

    def record_notification(self, *, notification_type, when=None):
        when = when or timezone.now()
        self.last_notification_type = notification_type
        self.last_notification_sent_at = when
        if notification_type == self.NotificationType.APPROVAL:
            self.approval_email_sent_at = when
        elif notification_type == self.NotificationType.REJECTION:
            self.rejection_email_sent_at = when
        elif notification_type == self.NotificationType.WAITLIST:
            self.waitlist_email_sent_at = when


class AuthAccessToken(models.Model):
    class Scope(models.TextChoices):
        SIGN_IN = "sign_in", "Sign In"

    class DeliveryMethod(models.TextChoices):
        EMAIL = "email", "Email"
        QR = "qr", "QR"

    agent = models.ForeignKey(
        AgentUser,
        on_delete=models.CASCADE,
        related_name="auth_access_tokens",
    )
    email = models.EmailField()
    scope = models.CharField(max_length=32, choices=Scope.choices, default=Scope.SIGN_IN)
    delivery_method = models.CharField(
        max_length=16,
        choices=DeliveryMethod.choices,
        default=DeliveryMethod.EMAIL,
    )
    token = models.CharField(max_length=128, unique=True, default=generate_access_token)
    expires_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    desktop_authenticated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.email} ({self.scope})"

    @property
    def is_active(self):
        return self.used_at is None and self.expires_at > timezone.now()

    @property
    def is_expired(self):
        return self.expires_at <= timezone.now()

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def mark_qr_completed(self):
        now = timezone.now()
        self.completed_at = now
        self.used_at = now
        self.save(update_fields=["completed_at", "used_at"])

    def mark_desktop_authenticated(self):
        self.desktop_authenticated_at = timezone.now()
        self.save(update_fields=["desktop_authenticated_at"])


class Listing(models.Model):
    class Stage(models.TextChoices):
        PREMARKET = "premarket", "Premarket"
        PRIVATE = "private", "Private"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REMOVED_BY_AGENT = "removed_by_agent", "Removed by agent"
        STALE = "stale", "Stale"

    agent = models.ForeignKey(
        AgentUser,
        on_delete=models.CASCADE,
        related_name="listings",
    )
    title = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120)
    property_type = models.CharField(max_length=100, blank=True)
    beds = models.PositiveSmallIntegerField()
    baths = models.DecimalField(max_digits=3, decimal_places=1)
    price_min = models.PositiveIntegerField()
    price_max = models.PositiveIntegerField()
    stage = models.CharField(max_length=20, choices=Stage.choices)
    seller_direction_certified = models.BooleanField(default=False)
    seller_direction_certified_at = models.DateTimeField(null=True, blank=True)
    agent_compliance_acknowledged = models.BooleanField(default=False)
    agent_compliance_acknowledged_at = models.DateTimeField(null=True, blank=True)
    information_accuracy_certified = models.BooleanField(default=False)
    information_accuracy_certified_at = models.DateTimeField(null=True, blank=True)
    private_marketing_certified = models.BooleanField(default=False)
    private_marketing_certified_at = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_confirmed_at = models.DateTimeField(default=timezone.now)
    reminder_count = models.PositiveSmallIntegerField(default=0)
    last_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)
    removed_reason = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title

    def clean(self):
        super().clean()
        if self._state.adding and self.agent_id and not self.agent.phones.exists():
            raise ValidationError("Agents must have a phone number to post listings.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def mark_confirmed(self):
        # This keeps freshness tracking in one place for later stale-listing automation.
        self.last_confirmed_at = timezone.now()
        self.reminder_count = 0
        self.last_reminder_sent_at = None
        self.removed_at = None
        self.removed_reason = ""
        self.status = self.Status.ACTIVE
        self.is_active = True
        self.save(
            update_fields=[
                "last_confirmed_at",
                "reminder_count",
                "last_reminder_sent_at",
                "removed_at",
                "removed_reason",
                "status",
                "is_active",
            ]
        )

    def mark_removed_by_agent(self):
        self.is_active = False
        self.status = self.Status.REMOVED_BY_AGENT
        self.removed_at = timezone.now()
        self.removed_reason = self.Status.REMOVED_BY_AGENT
        self.save(update_fields=["is_active", "status", "removed_at", "removed_reason"])

    def mark_stale(self):
        self.is_active = False
        self.status = self.Status.STALE
        self.removed_at = timezone.now()
        self.removed_reason = self.Status.STALE
        self.save(update_fields=["is_active", "status", "removed_at", "removed_reason"])


class Collection(models.Model):
    agent = models.ForeignKey(
        AgentUser,
        on_delete=models.CASCADE,
        related_name="collections",
    )
    name = models.CharField(max_length=255)
    notifications_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "-created_at"]
        unique_together = ("agent", "name")

    def __str__(self) -> str:
        return self.name

    def get_filter_query_params(self):
        if not hasattr(self, "saved_filter"):
            return {}

        params = {}
        if self.saved_filter.city:
            params["city"] = self.saved_filter.city
        if self.saved_filter.stage:
            params["stage"] = self.saved_filter.stage
        if self.saved_filter.min_beds is not None:
            params["min_beds"] = str(self.saved_filter.min_beds)
        if self.saved_filter.min_baths is not None:
            params["min_baths"] = str(self.saved_filter.min_baths)
        if self.saved_filter.min_price is not None:
            params["min_price"] = str(self.saved_filter.min_price)
        if self.saved_filter.max_price is not None:
            params["max_price"] = str(self.saved_filter.max_price)
        return params


class CollectionFilter(models.Model):
    collection = models.OneToOneField(
        Collection,
        on_delete=models.CASCADE,
        related_name="saved_filter",
    )
    city = models.CharField(max_length=120, blank=True)
    stage = models.CharField(max_length=20, choices=Listing.Stage.choices, blank=True)
    min_beds = models.PositiveSmallIntegerField(null=True, blank=True)
    min_baths = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    min_price = models.PositiveIntegerField(null=True, blank=True)
    max_price = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self) -> str:
        return f"Filters for {self.collection.name}"


class CollectionItem(models.Model):
    collection = models.ForeignKey(
        Collection,
        on_delete=models.CASCADE,
        related_name="items",
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="collection_items",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "listing"],
                name="unique_listing_per_collection",
            )
        ]

    def __str__(self) -> str:
        return f"{self.listing} in {self.collection}"


class SavedListing(models.Model):
    agent = models.ForeignKey(
        AgentUser,
        on_delete=models.CASCADE,
        related_name="saved_listings",
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="saved_by_agents",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "listing"],
                name="unique_saved_listing_per_agent",
            )
        ]

    def __str__(self) -> str:
        return f"{self.agent} saved {self.listing}"


class AgentEmail(models.Model):
    agent = models.ForeignKey(
        AgentUser,
        on_delete=models.CASCADE,
        related_name="emails",
    )
    email = models.EmailField(unique=True)
    is_verified = models.BooleanField(default=False)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-is_primary", "-is_verified", "email"]

    def __str__(self) -> str:
        return self.email


class AgentPhone(models.Model):
    agent = models.ForeignKey(
        AgentUser,
        on_delete=models.CASCADE,
        related_name="phones",
    )
    phone_number = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]

    def __str__(self) -> str:
        return self.phone_number


class EmailNotificationLog(models.Model):
    class NotificationType(models.TextChoices):
        COLLECTION_MATCH = "collection_match", "Collection Match"

    agent = models.ForeignKey(
        AgentUser,
        on_delete=models.CASCADE,
        related_name="email_notification_logs",
    )
    collection = models.ForeignKey(
        Collection,
        on_delete=models.CASCADE,
        related_name="email_notification_logs",
        null=True,
        blank=True,
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="email_notification_logs",
        null=True,
        blank=True,
    )
    recipient_email = models.EmailField()
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection", "listing", "notification_type"],
                name="unique_collection_listing_notification",
            )
        ]

    def __str__(self) -> str:
        return f"{self.notification_type} to {self.recipient_email}"


class InAppNotification(models.Model):
    class NotificationType(models.TextChoices):
        COLLECTION_MATCH = "collection_match", "Collection Match"

    agent = models.ForeignKey(
        AgentUser,
        on_delete=models.CASCADE,
        related_name="in_app_notifications",
    )
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    collection = models.ForeignKey(
        Collection,
        on_delete=models.CASCADE,
        related_name="in_app_notifications",
        null=True,
        blank=True,
    )
    listing = models.ForeignKey(
        Listing,
        on_delete=models.CASCADE,
        related_name="in_app_notifications",
        null=True,
        blank=True,
    )
    link_url = models.CharField(max_length=500, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "collection", "listing", "notification_type"],
                name="unique_in_app_collection_notification",
            )
        ]

    def __str__(self) -> str:
        return self.title
