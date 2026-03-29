"""
Retention selectors for low-risk cleanup only.

V1 intentionally excludes legal- and compliance-sensitive rows such as AgentUser,
Listing, and reviewed/moderated AccessRequest history.
"""

from datetime import timedelta

from django.conf import settings
from django.db.models import DateTimeField, Exists, OuterRef, Q
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import AccessRequest, AgentUser, AuthAccessToken


APPROVED_CLEANUP_KEYS = (
    "auth_tokens.qr_expired",
    "auth_tokens.qr_used",
    "auth_tokens.non_qr",
    "access_requests.rejected",
)


def get_auth_token_cleanup_querysets(*, now=None):
    now = now or timezone.now()
    qr_expired_cutoff = now - timedelta(days=settings.QR_AUTH_TOKEN_EXPIRED_RETENTION_DAYS)
    qr_used_cutoff = now - timedelta(days=settings.QR_AUTH_TOKEN_USED_RETENTION_DAYS)
    auth_cutoff = now - timedelta(days=settings.AUTH_TOKEN_RETENTION_DAYS)

    return {
        "auth_tokens.qr_expired": AuthAccessToken.objects.filter(
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            used_at__isnull=True,
            expires_at__lte=qr_expired_cutoff,
        ),
        "auth_tokens.qr_used": AuthAccessToken.objects.filter(
            delivery_method=AuthAccessToken.DeliveryMethod.QR,
            used_at__isnull=False,
            used_at__lte=qr_used_cutoff,
        ),
        "auth_tokens.non_qr": AuthAccessToken.objects.filter(
            ~Q(delivery_method=AuthAccessToken.DeliveryMethod.QR),
        ).filter(
            Q(used_at__isnull=False, used_at__lte=auth_cutoff)
            | Q(used_at__isnull=True, expires_at__lte=auth_cutoff)
        ),
    }


def get_access_request_cleanup_querysets(*, now=None):
    now = now or timezone.now()
    rejected_cutoff = now - timedelta(days=settings.REJECTED_ACCESS_REQUEST_RETENTION_DAYS)

    # AccessRequest does not have a direct FK to AgentUser, so the safest current
    # protection is to exclude any onboarding row whose email now belongs to an
    # account. This is intentionally conservative for V1.
    related_agent = AgentUser.objects.filter(email=OuterRef("email"))
    base_queryset = AccessRequest.objects.annotate(has_agent=Exists(related_agent)).filter(
        completed_at__isnull=True,
        reviewed_at__isnull=True,
        has_agent=False,
    )

    return {
        "access_requests.rejected": base_queryset.filter(
            updated_at__lte=rejected_cutoff,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
        ),
    }


def get_incomplete_access_request_base_queryset(*, now=None):
    now = now or timezone.now()
    related_agent = AgentUser.objects.filter(email=OuterRef("email"))
    return AccessRequest.objects.annotate(
        has_agent=Exists(related_agent),
        lifecycle_started_at=Coalesce("signup_sent_at", "created_at", output_field=DateTimeField()),
    ).filter(
        completed_at__isnull=True,
        reviewed_at__isnull=True,
        has_agent=False,
        queue_type=AccessRequest.QueueType.NONE,
        requires_manual_review=False,
        decision_status=AccessRequest.DecisionStatus.PENDING,
        status__in=[
            AccessRequest.Status.REQUESTED,
            AccessRequest.Status.LINK_SENT,
        ],
    )


def get_incomplete_access_request_reminder_querysets(*, now=None):
    now = now or timezone.now()
    first_cutoff = now - timedelta(days=settings.INCOMPLETE_ACCESS_REQUEST_FIRST_REMINDER_DAYS)
    second_cutoff = now - timedelta(days=settings.INCOMPLETE_ACCESS_REQUEST_SECOND_REMINDER_DAYS)
    purge_cutoff = now - timedelta(days=settings.INCOMPLETE_ACCESS_REQUEST_PURGE_DAYS)
    base_queryset = get_incomplete_access_request_base_queryset(now=now)

    return {
        "access_requests.incomplete_signup_first_reminder": base_queryset.filter(
            lifecycle_started_at__lte=first_cutoff,
            lifecycle_started_at__gt=second_cutoff,
            signup_reminder_sent_at__isnull=True,
        ),
        "access_requests.incomplete_signup_second_reminder": base_queryset.filter(
            lifecycle_started_at__lte=second_cutoff,
            lifecycle_started_at__gt=purge_cutoff,
            signup_final_reminder_sent_at__isnull=True,
        ),
        "access_requests.incomplete_signup_purge": base_queryset.filter(
            lifecycle_started_at__lte=purge_cutoff,
        ),
    }


def get_cleanup_querysets(*, now=None):
    now = now or timezone.now()
    auth_querysets = get_auth_token_cleanup_querysets(now=now)
    access_request_querysets = get_access_request_cleanup_querysets(now=now)

    # Deny by default: V1 cleanup categories are explicitly enumerated here so new
    # deletion targets are not activated accidentally by a helper change.
    return {
        "auth_tokens.qr_expired": auth_querysets["auth_tokens.qr_expired"],
        "auth_tokens.qr_used": auth_querysets["auth_tokens.qr_used"],
        "auth_tokens.non_qr": auth_querysets["auth_tokens.non_qr"],
        "access_requests.rejected": access_request_querysets["access_requests.rejected"],
    }
