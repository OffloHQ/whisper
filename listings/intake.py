import logging

from django.utils import timezone
from django.urls import reverse

from .email_flows import (
    build_access_request_continuation_token,
    send_access_request_activation_email,
    send_access_request_manual_approval_email,
    send_access_request_rejection_email,
    send_access_request_waitlist_email,
)
from .models import AccessRequest, AgentUser

logger = logging.getLogger(__name__)


WAITLIST_TOAST_MESSAGE = "Whisper isn’t available in your area yet. We’ve added you to the list, and you’ll be first to know when we arrive."
MANUAL_REVIEW_MESSAGE = "We’re having trouble verifying your license right now. A teammate will be in touch shortly."


def get_queue_reason_for_failure(*, verification_status, duplicate_license=False):
    if duplicate_license:
        return AccessRequest.QueueType.MANUAL_REVIEW, AccessRequest.Reason.DUPLICATE_LICENSE
    if verification_status == AccessRequest.VerificationStatus.UNSUPPORTED_STATE:
        return AccessRequest.QueueType.WAITLIST, AccessRequest.Reason.UNSUPPORTED_STATE
    if verification_status == AccessRequest.VerificationStatus.NAME_MISMATCH:
        return AccessRequest.QueueType.MANUAL_REVIEW, AccessRequest.Reason.NAME_MISMATCH
    if verification_status == AccessRequest.VerificationStatus.PROVIDER_ERROR:
        return AccessRequest.QueueType.MANUAL_REVIEW, AccessRequest.Reason.PROVIDER_ERROR
    if verification_status == AccessRequest.VerificationStatus.NO_MATCH:
        return AccessRequest.QueueType.MANUAL_REVIEW, AccessRequest.Reason.NO_MATCH
    if verification_status == AccessRequest.VerificationStatus.EXPIRED:
        return AccessRequest.QueueType.MANUAL_REVIEW, AccessRequest.Reason.EXPIRED
    return AccessRequest.QueueType.MANUAL_REVIEW, AccessRequest.Reason.MALFORMED_PROVIDER_RESPONSE


def apply_failed_verification_routing(access_request, *, verification_status, reason_text="", duplicate_license=False):
    queue_type, reason_code = get_queue_reason_for_failure(
        verification_status=verification_status,
        duplicate_license=duplicate_license,
    )
    if queue_type == AccessRequest.QueueType.WAITLIST:
        access_request.route_to_waitlist(reason_code, reason_text)
    else:
        access_request.route_to_manual_review(reason_code, reason_text)
    return queue_type, reason_code


def build_manual_approval_link(request, access_request):
    token = build_access_request_continuation_token(access_request)
    return request.build_absolute_uri(reverse("signup_contact_continue", args=[token]))


def get_or_create_pending_agent(access_request):
    agent = AgentUser.objects.filter(email=access_request.email).first()
    if agent is None:
        agent = AgentUser(
            email=access_request.email,
            name=access_request.full_name or access_request.email,
            state=access_request.state,
            license_number=access_request.license_number,
            is_active=False,
        )
    else:
        agent.name = access_request.full_name or agent.name
        agent.state = access_request.state
        if access_request.license_number:
            agent.license_number = access_request.license_number
    agent.signup_status = AgentUser.SignupStatus.PENDING_CONTACT
    agent.is_verified = True
    agent.is_active = False
    agent.save()
    return agent


def get_or_create_access_agent(access_request):
    agent = AgentUser.objects.filter(email=access_request.email).first()
    if agent is None:
        agent = AgentUser(
            email=access_request.email,
            name=access_request.full_name or access_request.email,
            state=access_request.state,
            license_number=access_request.license_number,
            is_active=False,
        )
    else:
        agent.name = access_request.full_name or agent.name
        agent.state = access_request.state
        if access_request.license_number:
            agent.license_number = access_request.license_number
    return agent


def can_activate_waitlist_request(access_request):
    return (
        access_request.completed_at is None
        and access_request.queue_type == AccessRequest.QueueType.WAITLIST
        and access_request.status == AccessRequest.Status.WAITLIST
        and access_request.decision_status != AccessRequest.DecisionStatus.COMPLETED
    )


def can_activate_manual_review_request(access_request):
    return (
        access_request.completed_at is None
        and access_request.queue_type == AccessRequest.QueueType.MANUAL_REVIEW
        and access_request.status == AccessRequest.Status.MANUAL_REVIEW
        and access_request.decision_status not in {
            AccessRequest.DecisionStatus.REJECTED,
            AccessRequest.DecisionStatus.COMPLETED,
        }
    )


def can_activate_access_request(access_request):
    return can_activate_waitlist_request(access_request) or can_activate_manual_review_request(access_request)


def can_reject_access_request(access_request):
    return (
        access_request.completed_at is None
        and access_request.queue_type == AccessRequest.QueueType.MANUAL_REVIEW
        and access_request.status == AccessRequest.Status.MANUAL_REVIEW
        and access_request.decision_status not in {
            AccessRequest.DecisionStatus.REJECTED,
            AccessRequest.DecisionStatus.COMPLETED,
        }
    )


def get_resend_access_email_eligible_emails(emails):
    if not emails:
        return set()
    return set(
        AgentUser.objects.filter(
            email__in=emails,
            signup_status=AgentUser.SignupStatus.ACTIVE,
            is_active=True,
            deleted_at__isnull=True,
        ).values_list("email", flat=True)
    )


def can_resend_access_email(access_request):
    return access_request.email in get_resend_access_email_eligible_emails([access_request.email])


def approve_access_request(*, access_request, reviewed_by, request, decision_reason=""):
    agent = get_or_create_access_agent(access_request)
    agent.signup_status = AgentUser.SignupStatus.ACTIVE
    agent.is_verified = True
    agent.is_active = True
    agent.save()
    access_request.mark_review_decision(
        decision_status=AccessRequest.DecisionStatus.COMPLETED,
        reviewed_by=reviewed_by,
        decision_reason=decision_reason,
    )
    access_request.queue_type = AccessRequest.QueueType.NONE
    access_request.status = AccessRequest.Status.COMPLETED
    access_request.requires_manual_review = False
    access_request.completed_at = timezone.now()
    access_request.save(
        update_fields=[
            "decision_status",
            "reviewed_at",
            "reviewed_by",
            "manual_decision_reason",
            "queue_type",
            "status",
            "requires_manual_review",
            "completed_at",
            "updated_at",
        ]
    )
    notification_sent = False
    sign_in_link = ""
    try:
        _, sign_in_link = send_access_request_activation_email(
            request,
            access_request=access_request,
            agent=agent,
        )
    except Exception:
        logger.warning(
            "Activation notification email failed for access_request_id=%s email=%s",
            access_request.id,
            access_request.email,
            exc_info=True,
        )
    else:
        access_request.record_notification(notification_type=AccessRequest.NotificationType.APPROVAL)
        access_request.save(
            update_fields=[
                "approval_email_sent_at",
                "last_notification_type",
                "last_notification_sent_at",
                "updated_at",
            ]
        )
        notification_sent = True
    return sign_in_link, agent, notification_sent


def activate_waitlist_requests(*, access_requests, reviewed_by, request, decision_reason=""):
    activated_count = 0
    skipped_count = 0
    failed_count = 0
    skipped_emails = []
    failed_emails = []

    for access_request in access_requests:
        if not can_activate_waitlist_request(access_request):
            skipped_count += 1
            skipped_emails.append(access_request.email)
            continue
        try:
            approve_access_request(
                access_request=access_request,
                reviewed_by=reviewed_by,
                request=request,
                decision_reason=decision_reason or access_request.manual_decision_reason or access_request.verification_reason,
            )
        except Exception:
            failed_count += 1
            failed_emails.append(access_request.email)
            logger.warning(
                "Bulk waitlist activation failed for access_request_id=%s email=%s",
                access_request.id,
                access_request.email,
                exc_info=True,
            )
        else:
            activated_count += 1

    return activated_count, skipped_count, failed_count, skipped_emails, failed_emails


def send_access_request_invites(*, access_requests, request):
    invited_count = 0
    skipped_count = 0
    failed_count = 0
    skipped_emails = []
    failed_emails = []
    eligible_emails = get_resend_access_email_eligible_emails([access_request.email for access_request in access_requests])

    for access_request in access_requests:
        if access_request.email not in eligible_emails:
            skipped_count += 1
            skipped_emails.append(access_request.email)
            continue
        agent = AgentUser.objects.filter(
            email=access_request.email,
            signup_status=AgentUser.SignupStatus.ACTIVE,
            is_active=True,
            deleted_at__isnull=True,
        ).first()
        if agent is None:
            skipped_count += 1
            skipped_emails.append(access_request.email)
            continue
        try:
            send_access_request_activation_email(
                request,
                access_request=access_request,
                agent=agent,
            )
        except Exception:
            failed_count += 1
            failed_emails.append(access_request.email)
            logger.warning(
                "Invite resend failed for access_request_id=%s email=%s",
                access_request.id,
                access_request.email,
                exc_info=True,
            )
        else:
            access_request.record_notification(notification_type=AccessRequest.NotificationType.APPROVAL)
            access_request.save(
                update_fields=[
                    "approval_email_sent_at",
                    "last_notification_type",
                    "last_notification_sent_at",
                    "updated_at",
                ]
            )
            invited_count += 1

    return invited_count, skipped_count, failed_count, skipped_emails, failed_emails


def approve_manual_review_requests(*, access_requests, reviewed_by, request, decision_reason=""):
    approved_count = 0
    skipped_count = 0
    failed_count = 0
    skipped_emails = []
    failed_emails = []

    for access_request in access_requests:
        if not can_activate_manual_review_request(access_request):
            skipped_count += 1
            skipped_emails.append(access_request.email)
            continue
        try:
            approve_access_request(
                access_request=access_request,
                reviewed_by=reviewed_by,
                request=request,
                decision_reason=decision_reason or access_request.manual_decision_reason or access_request.verification_reason,
            )
        except Exception:
            failed_count += 1
            failed_emails.append(access_request.email)
            logger.warning(
                "Bulk manual-review approval failed for access_request_id=%s email=%s",
                access_request.id,
                access_request.email,
                exc_info=True,
            )
        else:
            approved_count += 1

    return approved_count, skipped_count, failed_count, skipped_emails, failed_emails


def reject_manual_review_requests(*, access_requests, reviewed_by, decision_reason=""):
    rejected_count = 0
    skipped_count = 0
    failed_count = 0
    skipped_emails = []
    failed_emails = []

    for access_request in access_requests:
        if not can_reject_access_request(access_request):
            skipped_count += 1
            skipped_emails.append(access_request.email)
            continue
        try:
            reject_access_request(
                access_request=access_request,
                reviewed_by=reviewed_by,
                decision_reason=decision_reason or access_request.manual_decision_reason or access_request.verification_reason,
            )
        except Exception:
            failed_count += 1
            failed_emails.append(access_request.email)
            logger.warning(
                "Bulk manual-review rejection failed for access_request_id=%s email=%s",
                access_request.id,
                access_request.email,
                exc_info=True,
            )
        else:
            rejected_count += 1

    return rejected_count, skipped_count, failed_count, skipped_emails, failed_emails


def reject_access_request(*, access_request, reviewed_by, decision_reason=""):
    agent = AgentUser.objects.filter(email=access_request.email).first()
    if agent is not None:
        agent.signup_status = AgentUser.SignupStatus.MANUAL_REVIEW
        agent.is_active = False
        agent.is_verified = False
        agent.save(update_fields=["signup_status", "is_active", "is_verified"])
    access_request.mark_review_decision(
        decision_status=AccessRequest.DecisionStatus.REJECTED,
        reviewed_by=reviewed_by,
        decision_reason=decision_reason,
    )
    access_request.queue_type = AccessRequest.QueueType.MANUAL_REVIEW
    access_request.status = AccessRequest.Status.MANUAL_REVIEW
    access_request.save(
        update_fields=[
            "decision_status",
            "reviewed_at",
            "reviewed_by",
            "manual_decision_reason",
            "queue_type",
            "status",
            "updated_at",
        ]
    )
    notification_sent = False
    try:
        send_access_request_rejection_email(access_request=access_request)
    except Exception:
        logger.warning(
            "Rejection notification email failed for access_request_id=%s email=%s",
            access_request.id,
            access_request.email,
            exc_info=True,
        )
    else:
        access_request.record_notification(notification_type=AccessRequest.NotificationType.REJECTION)
        access_request.save(
            update_fields=[
                "rejection_email_sent_at",
                "last_notification_type",
                "last_notification_sent_at",
                "updated_at",
            ]
        )
        notification_sent = True
    return notification_sent


def waitlist_access_request(access_request):
    notification_sent = False
    try:
        send_access_request_waitlist_email(access_request=access_request)
    except Exception:
        logger.warning(
            "Waitlist notification email failed for access_request_id=%s email=%s",
            access_request.id,
            access_request.email,
            exc_info=True,
        )
    else:
        access_request.record_notification(notification_type=AccessRequest.NotificationType.WAITLIST)
        access_request.save(
            update_fields=[
                "waitlist_email_sent_at",
                "last_notification_type",
                "last_notification_sent_at",
                "updated_at",
            ]
        )
        notification_sent = True
    return notification_sent
