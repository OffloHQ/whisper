from datetime import timedelta

from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import AccessRequest, AgentUser, AuthAccessToken


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
    stale_cutoff = now - timedelta(days=settings.ACCESS_REQUEST_RETENTION_DAYS)
    rejected_cutoff = now - timedelta(days=settings.REJECTED_ACCESS_REQUEST_RETENTION_DAYS)

    related_agent = AgentUser.objects.filter(email=OuterRef("email"))
    base_queryset = AccessRequest.objects.annotate(has_agent=Exists(related_agent)).filter(
        completed_at__isnull=True,
        has_agent=False,
    )

    return {
        "access_requests.pending_or_waitlist": base_queryset.filter(
            updated_at__lte=stale_cutoff,
            decision_status=AccessRequest.DecisionStatus.PENDING,
            status__in=[
                AccessRequest.Status.REQUESTED,
                AccessRequest.Status.LINK_SENT,
                AccessRequest.Status.MANUAL_REVIEW,
                AccessRequest.Status.WAITLIST,
            ],
        ),
        "access_requests.rejected": base_queryset.filter(
            updated_at__lte=rejected_cutoff,
            decision_status=AccessRequest.DecisionStatus.REJECTED,
        ),
    }


def get_cleanup_querysets(*, now=None):
    now = now or timezone.now()
    querysets = {}
    querysets.update(get_auth_token_cleanup_querysets(now=now))
    querysets.update(get_access_request_cleanup_querysets(now=now))
    return querysets
