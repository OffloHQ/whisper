import logging

from django.utils import timezone
from django.urls import reverse

from .email_flows import (
    build_access_request_continuation_token,
    send_access_request_activation_email,
    send_access_request_manual_approval_email,
    send_access_request_rejection_email,
    send_access_request_terminated_email,
    send_access_request_waitlist_email,
    send_waitlist_coming_soon_email,
    send_waitlist_open_signup_email,
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
        and access_request.waitlist_removed_at is None
        and access_request.waitlist_unsubscribed_at is None
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
            AccessRequest.DecisionStatus.APPROVED,
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
            is_verified=True,
            deleted_at__isnull=True,
        ).values_list("email", flat=True)
    )


def can_resend_access_email(access_request):
    return access_request.email in get_resend_access_email_eligible_emails([access_request.email])


def can_terminate_agent_access(agent):
    return bool(
        agent
        and agent.signup_status == AgentUser.SignupStatus.ACTIVE
        and agent.is_active
        and agent.is_verified
        and agent.deleted_at is None
    )


def approve_access_request(*, access_request, reviewed_by, request, decision_reason="", evidence_reference=""):
    if access_request.queue_type != AccessRequest.QueueType.MANUAL_REVIEW:
        raise ValueError("Only manual-review requests can be approved for access.")
    approved_at = timezone.now()
    agent = get_or_create_pending_agent(access_request)
    access_request.mark_review_decision(
        decision_status=AccessRequest.DecisionStatus.APPROVED,
        reviewed_by=reviewed_by,
        decision_reason=decision_reason,
    )
    access_request.requires_manual_review = False
    access_request.verification_status = AccessRequest.VerificationStatus.VERIFIED
    access_request.verified_at = approved_at
    access_request.manual_verification_approved_at = approved_at
    access_request.manual_verification_rejected_at = None
    access_request.manual_verification_evidence_ref = evidence_reference
    access_request.save(
        update_fields=[
            "decision_status",
            "reviewed_at",
            "reviewed_by",
            "manual_decision_reason",
            "requires_manual_review",
            "verification_status",
            "verified_at",
            "manual_verification_approved_at",
            "manual_verification_rejected_at",
            "manual_verification_evidence_ref",
            "updated_at",
        ]
    )
    notification_sent = False
    continuation_link = build_manual_approval_link(request, access_request)
    try:
        send_access_request_manual_approval_email(
            access_request=access_request,
            continuation_link=continuation_link,
        )
    except Exception:
        logger.warning(
            "Manual-review approval email failed for access_request_id=%s email=%s",
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
    return continuation_link, agent, notification_sent


def terminate_agent_access(*, access_request, agent, terminated_by, termination_reason="", termination_note=""):
    terminated_at = timezone.now()
    termination_reason_label = dict(AccessRequest.TerminationReason.choices).get(termination_reason, termination_reason)
    agent.is_active = False
    agent.save(update_fields=["is_active"])
    agent.listings.filter(is_active=True).update(
        is_active=False,
        status="removed_by_agent",
        removed_at=terminated_at,
        removed_reason="access_terminated",
    )
    access_request.access_terminated_at = terminated_at
    access_request.access_terminated_by = terminated_by
    access_request.access_termination_reason = termination_reason
    access_request.access_termination_note = termination_note
    access_request.save(
        update_fields=[
            "access_terminated_at",
            "access_terminated_by",
            "access_termination_reason",
            "access_termination_note",
            "updated_at",
        ]
    )
    notification_sent = False
    try:
        send_access_request_terminated_email(
            access_request=access_request,
            termination_reason=termination_reason_label,
        )
    except Exception:
        logger.warning(
            "Access termination email failed for access_request_id=%s email=%s",
            access_request.id,
            access_request.email,
            exc_info=True,
        )
    else:
        notification_sent = True
    return terminated_at, notification_sent


def send_waitlist_outreach(*, access_request, reviewed_by, request, outreach_type, decision_reason=""):
    if outreach_type == AccessRequest.WaitlistOutreachType.OPEN_SIGNUP:
        send_waitlist_open_signup_email(request, access_request=access_request)
    else:
        outreach_type = AccessRequest.WaitlistOutreachType.COMING_SOON
        send_waitlist_coming_soon_email(request, access_request=access_request)

    access_request.reviewed_at = timezone.now()
    access_request.reviewed_by = reviewed_by
    access_request.manual_decision_reason = decision_reason
    access_request.record_notification(notification_type=AccessRequest.NotificationType.WAITLIST)
    access_request.record_waitlist_outreach(
        outreach_type=outreach_type,
        sent_by=reviewed_by,
        when=access_request.last_notification_sent_at,
    )
    access_request.save(
        update_fields=[
            "reviewed_at",
            "reviewed_by",
            "manual_decision_reason",
            "waitlist_email_sent_at",
            "waitlist_outreach_type",
            "waitlist_outreach_sent_at",
            "waitlist_outreach_sent_by",
            "last_notification_type",
            "last_notification_sent_at",
            "updated_at",
        ]
    )
    access_request.log_waitlist_outreach_event(
        outreach_type=outreach_type,
        sent_at=access_request.waitlist_outreach_sent_at or timezone.now(),
        sent_by=reviewed_by,
        note=decision_reason,
    )
    return True


def activate_waitlist_requests(*, access_requests, reviewed_by, request, decision_reason="", outreach_type=AccessRequest.WaitlistOutreachType.COMING_SOON):
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
            notification_sent = send_waitlist_outreach(
                access_request=access_request,
                reviewed_by=reviewed_by,
                request=request,
                outreach_type=outreach_type,
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
            if notification_sent:
                activated_count += 1
            else:
                failed_count += 1
                failed_emails.append(access_request.email)

    return activated_count, skipped_count, failed_count, skipped_emails, failed_emails


def remove_waitlist_requests(*, access_requests, reviewed_by, decision_reason=""):
    removed_count = 0
    skipped_count = 0
    failed_count = 0
    skipped_emails = []
    failed_emails = []

    for access_request in access_requests:
        if (
            access_request.queue_type != AccessRequest.QueueType.WAITLIST
            or access_request.status != AccessRequest.Status.WAITLIST
            or access_request.waitlist_removed_at is not None
        ):
            skipped_count += 1
            skipped_emails.append(access_request.email)
            continue
        try:
            removal_reason = decision_reason or access_request.manual_decision_reason or access_request.verification_reason
            access_request.mark_waitlist_removed(
                removed_by=reviewed_by,
                reason=removal_reason,
            )
            access_request.save(
                update_fields=[
                    "waitlist_removed_at",
                    "waitlist_removed_by",
                    "reviewed_at",
                    "reviewed_by",
                    "manual_decision_reason",
                    "updated_at",
                ]
            )
            access_request.log_waitlist_outreach_event(
                outreach_type=AccessRequest.WaitlistOutreachType.REMOVED,
                sent_at=access_request.waitlist_removed_at or timezone.now(),
                sent_by=reviewed_by,
                note=removal_reason,
            )
        except Exception:
            failed_count += 1
            failed_emails.append(access_request.email)
            logger.warning(
                "Remove from waitlist failed for access_request_id=%s email=%s",
                access_request.id,
                access_request.email,
                exc_info=True,
            )
        else:
            removed_count += 1

    return removed_count, skipped_count, failed_count, skipped_emails, failed_emails


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
            is_verified=True,
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


def reject_access_request(*, access_request, reviewed_by, decision_reason="", evidence_reference=""):
    agent = AgentUser.objects.filter(email=access_request.email).first()
    if agent is not None:
        agent.signup_status = AgentUser.SignupStatus.MANUAL_REVIEW
        agent.is_active = False
        agent.is_verified = False
        agent.save(update_fields=["signup_status", "is_active", "is_verified"])
    rejected_at = timezone.now()
    access_request.mark_review_decision(
        decision_status=AccessRequest.DecisionStatus.REJECTED,
        reviewed_by=reviewed_by,
        decision_reason=decision_reason,
    )
    access_request.requires_manual_review = False
    access_request.manual_verification_rejected_at = rejected_at
    access_request.manual_verification_approved_at = None
    access_request.manual_verification_evidence_ref = evidence_reference
    access_request.save(
        update_fields=[
            "decision_status",
            "reviewed_at",
            "reviewed_by",
            "manual_decision_reason",
            "requires_manual_review",
            "manual_verification_rejected_at",
            "manual_verification_approved_at",
            "manual_verification_evidence_ref",
            "updated_at",
        ]
    )
    notification_sent = False
    try:
        send_access_request_rejection_email(
            access_request=access_request,
            review_reason=decision_reason or access_request.verification_reason,
        )
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
