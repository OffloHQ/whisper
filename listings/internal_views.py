from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from .intake import (
    activate_waitlist_requests,
    approve_access_request,
    approve_manual_review_requests,
    can_activate_access_request,
    can_terminate_agent_access,
    can_reject_access_request,
    get_resend_access_email_eligible_emails,
    remove_waitlist_requests,
    reject_access_request,
    reject_manual_review_requests,
    send_access_request_invites,
    terminate_agent_access,
)
from .models import AccessRequest, AgentUser
from .verification.utils import get_state_display_name

QUEUE_PAGE_SIZE = 25
RECORDS_PAGE_SIZE = 30
QUEUE_SORT_OPTIONS = {
    "date_desc": ("-updated_at", "-pk"),
    "date_asc": ("updated_at", "pk"),
    "state": ("state", "-updated_at", "-pk"),
    "name": ("full_name", "email", "-updated_at", "-pk"),
    "status": ("status", "-updated_at", "-pk"),
}
RECORDS_SORT_OPTIONS = {
    "date_desc": ("-updated_at", "-pk"),
    "date_asc": ("updated_at", "pk"),
    "name": ("full_name", "email", "-updated_at", "-pk"),
    "state": ("state", "-updated_at", "-pk"),
}


def build_queue_message(*, action_label, processed_count, skipped_count, failed_count, skipped_emails=None, failed_emails=None):
    message = f"{processed_count} {action_label}. {skipped_count} skipped. {failed_count} failed."
    skipped_emails = skipped_emails or []
    failed_emails = failed_emails or []
    details = []
    if failed_emails and len(failed_emails) <= 3:
        details.append(f"Failed: {', '.join(failed_emails)}.")
    if skipped_emails and len(skipped_emails) <= 3:
        details.append(f"Skipped: {', '.join(skipped_emails)}.")
    if details:
        message = f"{message} {' '.join(details)}"
    return message


def get_waitlist_outreach_action_label(outreach_type):
    if outreach_type == AccessRequest.WaitlistOutreachType.OPEN_SIGNUP:
        return "open-signup waitlist notifications sent"
    return "coming-soon waitlist notifications sent"


def get_waitlist_outreach_single_label(outreach_type):
    if outreach_type == AccessRequest.WaitlistOutreachType.OPEN_SIGNUP:
        return "open-signup waitlist notification"
    return "coming-soon waitlist notification"


def build_query_string(request, *, overrides=None, remove_keys=None):
    params = request.GET.copy()
    remove_keys = remove_keys or []
    for key in remove_keys:
        params.pop(key, None)
    for key, value in (overrides or {}).items():
        if value in ("", None):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""


def get_safe_redirect_target(request, *, default_name, request_id=None):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    if request_id is not None:
        return reverse(default_name, args=[request_id])
    return reverse(default_name)


def get_access_request_display_status(access_request):
    if access_request.decision_status == AccessRequest.DecisionStatus.REJECTED:
        return "Rejected", "rejected"
    if access_request.status == AccessRequest.Status.COMPLETED:
        return "Completed", "completed"
    if access_request.queue_type == AccessRequest.QueueType.MANUAL_REVIEW:
        return "Manual Review", "manual_review"
    if access_request.queue_type == AccessRequest.QueueType.WAITLIST:
        return "Waitlist", "waitlist"
    return access_request.get_status_display(), access_request.status or "default"


def get_waitlist_outreach_queryset():
    return AccessRequest.objects.filter(
        queue_type=AccessRequest.QueueType.WAITLIST,
        waitlist_removed_at__isnull=True,
    )


def build_queue_dashboard_context(
    request,
    *,
    base_queue_type,
    template_name,
    queue_name,
    queue_subtitle,
    bulk_actions,
    bulk_form_action,
    default_note_placeholder,
    empty_message,
    show_reject_action,
    show_invite_action,
    show_open_state_action=False,
):
    status_filter = request.GET.get("status", "").strip()
    state_filter = request.GET.get("state", "").strip()
    search_query = request.GET.get("q", "").strip()
    sort_key = request.GET.get("sort", "date_desc").strip()
    if sort_key not in QUEUE_SORT_OPTIONS:
        sort_key = "date_desc"

    requests = AccessRequest.objects.filter(queue_type=base_queue_type)
    if base_queue_type == AccessRequest.QueueType.WAITLIST:
        requests = get_waitlist_outreach_queryset()
    elif base_queue_type == AccessRequest.QueueType.MANUAL_REVIEW:
        requests = requests.filter(decision_status=AccessRequest.DecisionStatus.PENDING)
    if status_filter:
        requests = requests.filter(status=status_filter)
    if state_filter:
        requests = requests.filter(state=state_filter)
    if search_query:
        requests = requests.filter(Q(full_name__icontains=search_query) | Q(email__icontains=search_query))
    requests = requests.order_by(*QUEUE_SORT_OPTIONS[sort_key])

    paginator = Paginator(requests, QUEUE_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    request_rows = list(page_obj.object_list)
    eligible_resend_emails = get_resend_access_email_eligible_emails([access_request.email for access_request in request_rows])
    for access_request in request_rows:
        access_request.display_state = get_state_display_name(access_request.state)
        access_request.display_status, access_request.display_status_class = get_access_request_display_status(access_request)
        access_request.can_resend_access_email = access_request.email in eligible_resend_emails
        access_request.can_send_waitlist_update = (
            access_request.queue_type == AccessRequest.QueueType.WAITLIST
            and can_activate_access_request(access_request)
        )
        access_request.can_remove_from_waitlist = (
            access_request.queue_type == AccessRequest.QueueType.WAITLIST
            and access_request.waitlist_removed_at is None
        )
        if (
            access_request.queue_type == AccessRequest.QueueType.WAITLIST
            and access_request.waitlist_outreach_sent_at
            and access_request.waitlist_outreach_type
        ):
            access_request.last_email_display = (
                f"{access_request.get_waitlist_outreach_type_display()} "
                f"on {access_request.waitlist_outreach_sent_at.strftime('%b %-d, %Y')}"
            )
        elif access_request.last_notification_sent_at and access_request.last_notification_type:
            access_request.last_email_display = (
                f"{access_request.get_last_notification_type_display()} "
                f"on {access_request.last_notification_sent_at.strftime('%b %-d, %Y')}"
            )
        elif access_request.last_notification_sent_at:
            access_request.last_email_display = access_request.last_notification_sent_at.strftime("%b %-d, %Y")
        else:
            access_request.last_email_display = "No email sent"

    state_source = requests
    state_options = [
        {"value": state_code, "label": get_state_display_name(state_code)}
        for state_code in state_source.exclude(state="").values_list("state", flat=True).distinct().order_by("state")
    ]
    state_groups = []
    if show_open_state_action:
        state_groups = [
            {
                "value": row["state"],
                "label": get_state_display_name(row["state"]),
                "count": row["total"],
                "url": build_query_string(request, overrides={"state": row["state"]}, remove_keys=["page"]),
            }
            for row in (
                (
                    get_waitlist_outreach_queryset().filter(waitlist_unsubscribed_at__isnull=True)
                    if base_queue_type == AccessRequest.QueueType.WAITLIST
                    else AccessRequest.objects.filter(queue_type=base_queue_type)
                )
                .exclude(state="")
                .values("state")
                .annotate(total=Count("id"))
                .order_by("state")
            )
        ]
    start_index = ((page_obj.number - 1) * paginator.per_page) + 1 if paginator.count else 0
    end_index = min(start_index + len(request_rows) - 1, paginator.count) if paginator.count else 0
    filters_active = bool(search_query or state_filter or status_filter or sort_key != "date_desc")

    context = {
        "requests": request_rows,
        "search_query": search_query,
        "selected_state": state_filter,
        "selected_status": status_filter,
        "selected_sort": sort_key,
        "state_options": state_options,
        "status_options": AccessRequest.Status.choices,
        "queue_name": queue_name,
        "queue_subtitle": queue_subtitle,
        "bulk_actions": bulk_actions,
        "bulk_form_action": bulk_form_action,
        "default_note_placeholder": default_note_placeholder,
        "empty_message": empty_message,
        "show_reject_action": show_reject_action,
        "show_invite_action": show_invite_action,
        "show_open_state_action": show_open_state_action,
        "state_groups": state_groups,
        "waitlist_outreach_options": [
            {"value": AccessRequest.WaitlistOutreachType.COMING_SOON, "label": "Coming Soon"},
            {"value": AccessRequest.WaitlistOutreachType.OPEN_SIGNUP, "label": "Open in Your Area — Sign Up Now"},
        ],
        "return_to_queue_url": request.get_full_path(),
        "sort_options": [
            {"value": "date_desc", "label": "Newest first"},
            {"value": "date_asc", "label": "Oldest first"},
            {"value": "state", "label": "State"},
            {"value": "name", "label": "Name"},
            {"value": "status", "label": "Status"},
        ],
        "page_obj": page_obj,
        "total_count": paginator.count,
        "showing_start": start_index,
        "showing_end": end_index,
        "filters_active": filters_active,
        "clear_filters_url": request.path,
        "current_query": build_query_string(request),
        "prev_page_query": build_query_string(request, overrides={"page": page_obj.previous_page_number()} if page_obj.has_previous() else {}, remove_keys=["page"]),
        "next_page_query": build_query_string(request, overrides={"page": page_obj.next_page_number()} if page_obj.has_next() else {}, remove_keys=["page"]),
        "sort_name_query": build_query_string(request, overrides={"sort": "name"}, remove_keys=["page"]),
        "sort_state_query": build_query_string(request, overrides={"sort": "state"}, remove_keys=["page"]),
        "sort_status_query": build_query_string(request, overrides={"sort": "status"}, remove_keys=["page"]),
        "sort_date_query": build_query_string(request, overrides={"sort": "date_desc"}, remove_keys=["page"]),
    }
    return render(request, template_name, context)


def build_records_display_state(access_request):
    return get_state_display_name(access_request.state or getattr(access_request, "agent_state", ""))


def build_records_last_event_display(access_request):
    event_candidates = [
        ("Access terminated", access_request.access_terminated_at),
        ("Manual verification approved", access_request.manual_verification_approved_at),
        ("Manual verification rejected", access_request.manual_verification_rejected_at),
        ("Waitlist outreach", access_request.waitlist_outreach_sent_at),
        ("Reviewed", access_request.reviewed_at),
        ("Completed", access_request.completed_at),
        ("Notification", access_request.last_notification_sent_at),
        ("Updated", access_request.updated_at),
    ]
    label, event_at = next(((label, value) for label, value in event_candidates if value), ("Updated", None))
    if event_at is None:
        return "-"
    return f"{label} · {event_at.strftime('%b %-d, %Y')}"


def get_termination_reason_label(reason_code):
    return dict(AccessRequest.TerminationReason.choices).get(reason_code, reason_code or "-")


def build_request_history(access_request, waitlist_outreach_history):
    history_items = []
    if access_request.manual_verification_approved_at:
        history_items.append({
            "label": "Manual verification approved",
            "at": access_request.manual_verification_approved_at,
            "meta": f"Reviewed by {access_request.reviewed_by.get_username()}" if access_request.reviewed_by else "",
            "note": access_request.manual_decision_reason,
        })
    if access_request.approval_email_sent_at:
        history_items.append({
            "label": "Next-step email sent",
            "at": access_request.approval_email_sent_at,
            "meta": "",
            "note": "",
        })
    if access_request.manual_verification_rejected_at:
        history_items.append({
            "label": "Verification failure recorded",
            "at": access_request.manual_verification_rejected_at,
            "meta": f"Reviewed by {access_request.reviewed_by.get_username()}" if access_request.reviewed_by else "",
            "note": access_request.manual_decision_reason,
        })
    if access_request.rejection_email_sent_at:
        history_items.append({
            "label": "Rejection email sent",
            "at": access_request.rejection_email_sent_at,
            "meta": "",
            "note": "",
        })
    if access_request.completed_at:
        history_items.append({
            "label": "Signup completed",
            "at": access_request.completed_at,
            "meta": "",
            "note": "",
        })
    if access_request.access_terminated_at:
        history_items.append({
            "label": "Access revoked",
            "at": access_request.access_terminated_at,
            "meta": " · ".join(
                part for part in [
                    (
                        f"Revoked by {access_request.access_terminated_by.get_username()}"
                        if access_request.access_terminated_by else ""
                    ),
                    (
                        f"Reason: {get_termination_reason_label(access_request.access_termination_reason)}"
                        if access_request.access_termination_reason else ""
                    ),
                ] if part
            ),
            "note": access_request.access_termination_note,
        })
    for item in waitlist_outreach_history:
        history_items.append({
            "label": item.get_outreach_type_display(),
            "at": item.sent_at,
            "meta": f"Sent by {item.sent_by.get_username()}" if item.sent_by else "System event",
            "note": item.note,
        })
    history_items.sort(key=lambda item: item["at"], reverse=True)
    return history_items


def build_status_age_summary(access_request, history_items):
    if access_request.access_terminated_at:
        status_started_at = access_request.access_terminated_at
    elif access_request.decision_status == AccessRequest.DecisionStatus.APPROVED:
        status_started_at = (
            access_request.manual_verification_approved_at
            or access_request.reviewed_at
            or access_request.updated_at
            or access_request.created_at
        )
    elif access_request.decision_status == AccessRequest.DecisionStatus.REJECTED:
        status_started_at = (
            access_request.manual_verification_rejected_at
            or access_request.reviewed_at
            or access_request.updated_at
            or access_request.created_at
        )
    elif access_request.status == AccessRequest.Status.COMPLETED:
        status_started_at = access_request.completed_at or access_request.updated_at or access_request.created_at
    elif access_request.queue_type == AccessRequest.QueueType.WAITLIST:
        status_started_at = (
            access_request.waitlist_outreach_sent_at
            or access_request.waitlist_email_sent_at
            or access_request.reviewed_at
            or access_request.verification_attempted_at
            or access_request.created_at
        )
    elif access_request.queue_type == AccessRequest.QueueType.MANUAL_REVIEW:
        status_started_at = (
            access_request.verification_attempted_at
            or access_request.reviewed_at
            or access_request.updated_at
            or access_request.created_at
        )
    else:
        status_started_at = access_request.updated_at or access_request.created_at

    days_in_current_status = None
    if status_started_at:
        days_in_current_status = max((timezone.localdate() - status_started_at.date()).days, 0)

    last_event_at = history_items[0]["at"] if history_items else access_request.updated_at or access_request.created_at
    return {
        "status_started_at": status_started_at,
        "days_in_current_status": days_in_current_status,
        "last_event_at": last_event_at,
    }


def build_email_activity_summary(access_request, waitlist_outreach_history):
    email_events = []
    if access_request.signup_sent_at:
        email_events.append(("Signup link", access_request.signup_sent_at))
    if access_request.approval_email_sent_at:
        email_events.append(("Next-step email", access_request.approval_email_sent_at))
    if access_request.rejection_email_sent_at:
        email_events.append(("Rejection email", access_request.rejection_email_sent_at))
    if access_request.waitlist_email_sent_at:
        email_events.append(("Waitlist confirmation", access_request.waitlist_email_sent_at))
    for item in waitlist_outreach_history:
        if item.outreach_type in {
            AccessRequest.WaitlistOutreachType.COMING_SOON,
            AccessRequest.WaitlistOutreachType.OPEN_SIGNUP,
        }:
            email_events.append((item.get_outreach_type_display(), item.sent_at))

    email_events.sort(key=lambda item: item[1], reverse=True)
    last_email_type, last_email_sent_at = email_events[0] if email_events else ("-", None)
    return {
        "total_emails_sent": len(email_events),
        "last_email_type": last_email_type,
        "last_email_sent_at": last_email_sent_at,
    }


def has_portal_permission(user, perm):
    return user.is_authenticated and user.is_staff and user.has_perm(f"listings.{perm}")


def require_portal_permission(perm):
    def decorator(view_func):
        @never_cache
        @login_required(login_url="intake_login")
        def wrapped(request, *args, **kwargs):
            if not has_portal_permission(request.user, "can_access_intake_portal"):
                return HttpResponseForbidden("You do not have access to the Admin Portal.")
            if perm and not has_portal_permission(request.user, perm):
                return HttpResponseForbidden("You do not have permission to view this Admin Portal area.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


@never_cache
def intake_login(request):
    if request.user.is_authenticated and has_portal_permission(request.user, "can_access_intake_portal"):
        return redirect("intake_home")

    form = AuthenticationForm(request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)
        if not has_portal_permission(user, "can_access_intake_portal"):
            logout(request)
            return HttpResponseForbidden("You do not have access to the Admin Portal.")
        messages.success(request, "Signed in to the admin portal.")
        return redirect("intake_home")

    return render(request, "intake/login.html", {"form": form})


@never_cache
@login_required(login_url="intake_login")
def intake_logout(request):
    logout(request)
    messages.success(request, "Signed out of the admin portal.")
    return redirect("intake_login")


@require_portal_permission(None)
def intake_home(request):
    context = {
        "manual_review_count": AccessRequest.objects.filter(
            queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
            decision_status=AccessRequest.DecisionStatus.PENDING,
        ).count(),
        "waitlist_count": get_waitlist_outreach_queryset().count(),
        "records_count": AccessRequest.objects.count(),
        "can_review_manual_requests": has_portal_permission(request.user, "can_review_manual_requests"),
        "can_manage_waitlist": has_portal_permission(request.user, "can_manage_waitlist"),
    }
    return render(request, "intake/index.html", context)


@require_portal_permission(None)
def intake_records(request):
    search_query = request.GET.get("q", "").strip()
    queue_filter = request.GET.get("queue_type", "").strip()
    decision_filter = request.GET.get("decision_status", "").strip()
    verified_filter = request.GET.get("verified", "").strip()
    active_filter = request.GET.get("active", "").strip()
    state_filter = request.GET.get("state", "").strip()
    unsubscribed_filter = request.GET.get("unsubscribed", "").strip()
    sort_key = request.GET.get("sort", "date_desc").strip()
    if sort_key not in RECORDS_SORT_OPTIONS:
        sort_key = "date_desc"

    agent_records = AgentUser.objects.filter(email=OuterRef("email")).order_by("-created_at")
    records = AccessRequest.objects.select_related("reviewed_by").annotate(
        agent_exists=Exists(agent_records),
        agent_name=Subquery(agent_records.values("name")[:1]),
        agent_state=Subquery(agent_records.values("state")[:1]),
        agent_license_number=Subquery(agent_records.values("license_number")[:1]),
        agent_signup_status=Subquery(agent_records.values("signup_status")[:1]),
        agent_is_verified=Subquery(agent_records.values("is_verified")[:1]),
        agent_is_active=Subquery(agent_records.values("is_active")[:1]),
    )

    if search_query:
        matching_agent_emails = AgentUser.objects.filter(
            Q(name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(state__icontains=search_query)
            | Q(license_number__icontains=search_query)
        ).values_list("email", flat=True)
        records = records.filter(
            Q(full_name__icontains=search_query)
            | Q(email__icontains=search_query)
            | Q(state__icontains=search_query)
            | Q(license_number__icontains=search_query)
            | Q(email__in=matching_agent_emails)
        )
    if queue_filter:
        records = records.filter(queue_type=queue_filter)
    if decision_filter:
        records = records.filter(decision_status=decision_filter)
    if verified_filter == "yes":
        records = records.filter(email__in=AgentUser.objects.filter(is_verified=True).values_list("email", flat=True))
    elif verified_filter == "no":
        records = records.exclude(email__in=AgentUser.objects.filter(is_verified=True).values_list("email", flat=True))
    if active_filter == "yes":
        records = records.filter(email__in=AgentUser.objects.filter(is_active=True).values_list("email", flat=True))
    elif active_filter == "no":
        records = records.exclude(email__in=AgentUser.objects.filter(is_active=True).values_list("email", flat=True))
    if state_filter:
        records = records.filter(
            Q(state=state_filter)
            | Q(email__in=AgentUser.objects.filter(state=state_filter).values_list("email", flat=True))
        )
    if unsubscribed_filter == "yes":
        records = records.filter(waitlist_unsubscribed_at__isnull=False)
    elif unsubscribed_filter == "no":
        records = records.filter(waitlist_unsubscribed_at__isnull=True)

    records = records.order_by(*RECORDS_SORT_OPTIONS[sort_key])
    paginator = Paginator(records, RECORDS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get("page"))
    request_rows = list(page_obj.object_list)
    signup_status_labels = dict(AgentUser.SignupStatus.choices)

    for access_request in request_rows:
        access_request.display_name = access_request.full_name or access_request.agent_name or access_request.email
        access_request.display_state = build_records_display_state(access_request)
        access_request.display_verified = bool(access_request.agent_is_verified)
        access_request.display_active = bool(access_request.agent_is_active)
        access_request.display_signup_status = signup_status_labels.get(access_request.agent_signup_status, "-")
        access_request.display_last_event = build_records_last_event_display(access_request)

    state_options = [
        {"value": state_code, "label": get_state_display_name(state_code)}
        for state_code in records.exclude(state__isnull=True).exclude(state="").values_list("state", flat=True).distinct().order_by("state")
    ]
    filters_active = bool(
        search_query or queue_filter or decision_filter or verified_filter or active_filter or state_filter or unsubscribed_filter or sort_key != "date_desc"
    )
    start_index = ((page_obj.number - 1) * paginator.per_page) + 1 if paginator.count else 0
    end_index = min(start_index + len(request_rows) - 1, paginator.count) if paginator.count else 0

    return render(
        request,
        "intake/records_list.html",
        {
            "requests": request_rows,
            "search_query": search_query,
            "selected_queue_type": queue_filter,
            "selected_decision_status": decision_filter,
            "selected_verified": verified_filter,
            "selected_active": active_filter,
            "selected_state": state_filter,
            "selected_unsubscribed": unsubscribed_filter,
            "selected_sort": sort_key,
            "queue_options": AccessRequest.QueueType.choices,
            "decision_status_options": AccessRequest.DecisionStatus.choices,
            "state_options": state_options,
            "page_obj": page_obj,
            "total_count": paginator.count,
            "showing_start": start_index,
            "showing_end": end_index,
            "filters_active": filters_active,
            "clear_filters_url": request.path,
            "return_to_queue_url": request.get_full_path(),
            "prev_page_query": build_query_string(request, overrides={"page": page_obj.previous_page_number()} if page_obj.has_previous() else {}, remove_keys=["page"]),
            "next_page_query": build_query_string(request, overrides={"page": page_obj.next_page_number()} if page_obj.has_next() else {}, remove_keys=["page"]),
        },
    )


@require_portal_permission("can_review_manual_requests")
def intake_manual_review(request):
    return build_queue_dashboard_context(
        request,
        base_queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
        template_name="intake/manual_review_list.html",
        queue_name="Manual Review",
        queue_subtitle="Verification exceptions that need adjudication before the user can continue signup.",
        bulk_actions=[
            {"value": "approve", "label": "Manually Verify & Send Next Step (Bulk)"},
            {"value": "reject", "label": "Record Verification Failure (Bulk)"},
        ],
        bulk_form_action="intake_process_manual_review_requests",
        default_note_placeholder="Reviewer note or finding",
        empty_message="No manual review requests matched the current filters.",
        show_reject_action=True,
        show_invite_action=False,
    )


@require_portal_permission("can_manage_waitlist")
def intake_waitlist(request):
    return build_queue_dashboard_context(
        request,
        base_queue_type=AccessRequest.QueueType.WAITLIST,
        template_name="intake/waitlist_list.html",
        queue_name="Waitlist",
        queue_subtitle="Interest by state, waitlist outreach, and future market launch readiness.",
        bulk_actions=[
            {"value": AccessRequest.WaitlistOutreachType.COMING_SOON, "label": "Send Update: Coming Soon"},
            {"value": AccessRequest.WaitlistOutreachType.OPEN_SIGNUP, "label": "Send Update: Open in Your Area"},
            {"value": "remove", "label": "Remove from Waitlist"},
        ],
        bulk_form_action="intake_activate_waitlist_requests",
        default_note_placeholder="Optional note for waitlist outreach or removal",
        empty_message="No waitlist records matched the current filters.",
        show_reject_action=False,
        show_invite_action=False,
        show_open_state_action=True,
    )


@require_portal_permission("can_manage_waitlist")
def intake_open_waitlist_state(request):
    if request.method != "POST":
        return redirect("intake_waitlist")

    state_code = request.POST.get("state", "").strip()
    if not state_code:
        messages.warning(request, "Choose a state before sending a waitlist update.")
        return redirect(get_safe_redirect_target(request, default_name="intake_waitlist"))

    outreach_type = (
        request.POST.get("outreach_type", AccessRequest.WaitlistOutreachType.COMING_SOON).strip()
        or AccessRequest.WaitlistOutreachType.COMING_SOON
    )
    queryset = AccessRequest.objects.filter(
        queue_type=AccessRequest.QueueType.WAITLIST,
        state=state_code,
    ).order_by("-updated_at")
    activated_count, skipped_count, failed_count, skipped_emails, failed_emails = activate_waitlist_requests(
        access_requests=queryset,
        reviewed_by=request.user,
        request=request,
        decision_reason=request.POST.get("decision_reason", "").strip(),
        outreach_type=outreach_type,
    )
    messages.info(
        request,
        build_queue_message(
            action_label=f"{get_state_display_name(state_code) or state_code} {get_waitlist_outreach_action_label(outreach_type)}",
            processed_count=activated_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            skipped_emails=skipped_emails,
            failed_emails=failed_emails,
        ),
    )
    return redirect(get_safe_redirect_target(request, default_name="intake_waitlist"))


@require_portal_permission("can_manage_waitlist")
def intake_activate_waitlist_requests(request):
    if request.method != "POST":
        return redirect("intake_waitlist")

    request_ids = request.POST.getlist("request_ids")
    queryset = AccessRequest.objects.filter(pk__in=request_ids).order_by("-updated_at")
    bulk_action = request.POST.get("bulk_action", AccessRequest.WaitlistOutreachType.COMING_SOON).strip()
    if bulk_action == "remove":
        removed_count, skipped_count, failed_count, skipped_emails, failed_emails = remove_waitlist_requests(
            access_requests=queryset,
            reviewed_by=request.user,
            decision_reason=request.POST.get("decision_reason", "").strip(),
        )
        messages.info(
            request,
            build_queue_message(
                action_label="waitlist records removed",
                processed_count=removed_count,
                skipped_count=skipped_count,
                failed_count=failed_count,
                skipped_emails=skipped_emails,
                failed_emails=failed_emails,
            ),
        )
    else:
        activated_count, skipped_count, failed_count, skipped_emails, failed_emails = activate_waitlist_requests(
            access_requests=queryset,
            reviewed_by=request.user,
            request=request,
            decision_reason=request.POST.get("decision_reason", "").strip(),
            outreach_type=bulk_action,
        )
        messages.info(
            request,
            build_queue_message(
                action_label=get_waitlist_outreach_action_label(bulk_action),
                processed_count=activated_count,
                skipped_count=skipped_count,
                failed_count=failed_count,
                skipped_emails=skipped_emails,
                failed_emails=failed_emails,
            ),
        )
    return redirect(get_safe_redirect_target(request, default_name="intake_waitlist"))


@require_portal_permission("can_review_manual_requests")
def intake_process_manual_review_requests(request):
    if request.method != "POST":
        return redirect("intake_manual_review")

    request_ids = request.POST.getlist("request_ids")
    queryset = AccessRequest.objects.filter(pk__in=request_ids).order_by("-updated_at")
    bulk_action = request.POST.get("bulk_action", "approve").strip()
    decision_reason = request.POST.get("decision_reason", "").strip()
    if bulk_action == "reject":
        rejected_count, skipped_count, failed_count, skipped_emails, failed_emails = reject_manual_review_requests(
            access_requests=queryset,
            reviewed_by=request.user,
            decision_reason=decision_reason,
        )
        messages.info(
            request,
            build_queue_message(
                action_label="verification failures recorded",
                processed_count=rejected_count,
                skipped_count=skipped_count,
                failed_count=failed_count,
                skipped_emails=skipped_emails,
                failed_emails=failed_emails,
            ),
        )
    else:
        approved_count, skipped_count, failed_count, skipped_emails, failed_emails = approve_manual_review_requests(
            access_requests=queryset,
            reviewed_by=request.user,
            request=request,
            decision_reason=decision_reason,
        )
        messages.info(
            request,
            build_queue_message(
                action_label="manual verification continuation emails sent",
                processed_count=approved_count,
                skipped_count=skipped_count,
                failed_count=failed_count,
                skipped_emails=skipped_emails,
                failed_emails=failed_emails,
            ),
        )
    return redirect(get_safe_redirect_target(request, default_name="intake_manual_review"))


@require_portal_permission(None)
def intake_request_detail(request, request_id):
    access_request = get_object_or_404(AccessRequest, pk=request_id)
    agent_account = AgentUser.objects.filter(email=access_request.email).first()
    waitlist_outreach_history = list(access_request.waitlist_outreach_logs.select_related("sent_by").all()[:10])
    history_items = build_request_history(access_request, waitlist_outreach_history)
    status_summary = build_status_age_summary(access_request, history_items)
    email_activity_summary = build_email_activity_summary(access_request, waitlist_outreach_history)

    return render(
        request,
        "intake/request_detail.html",
        {
            "access_request": access_request,
            "agent_account": agent_account,
            "history_items": history_items,
            "can_activate_request": can_activate_access_request(access_request),
            "can_reject_request": can_reject_access_request(access_request),
            "can_resend_access_email": access_request.email in get_resend_access_email_eligible_emails([access_request.email]),
            "can_terminate_access": can_terminate_agent_access(agent_account),
            "display_state": get_state_display_name(access_request.state or (agent_account.state if agent_account else "")),
            "status_summary": status_summary,
            "email_activity_summary": email_activity_summary,
            "termination_reason_options": AccessRequest.TerminationReason.choices,
            "termination_reason_label": get_termination_reason_label(access_request.access_termination_reason),
            "return_to_queue_url": get_safe_redirect_target(
                request,
                default_name=(
                    "intake_manual_review"
                    if access_request.queue_type == AccessRequest.QueueType.MANUAL_REVIEW
                    else "intake_waitlist"
                    if access_request.queue_type == AccessRequest.QueueType.WAITLIST
                    else "intake_home"
                ),
            ),
        },
    )


@require_portal_permission(None)
def intake_verify_request(request, request_id):
    access_request = get_object_or_404(AccessRequest, pk=request_id)
    if request.method != "POST":
        return redirect("intake_request_detail", request_id=request_id)
    if access_request.queue_type == AccessRequest.QueueType.MANUAL_REVIEW:
        if not has_portal_permission(request.user, "can_review_manual_requests"):
            return HttpResponseForbidden("You do not have permission to review manual requests.")
    elif access_request.queue_type == AccessRequest.QueueType.WAITLIST:
        if not has_portal_permission(request.user, "can_manage_waitlist"):
            return HttpResponseForbidden("You do not have permission to manage waitlist requests.")
    else:
        return HttpResponseForbidden("This intake request cannot be activated.")

    if not can_activate_access_request(access_request):
        messages.warning(request, "This request can no longer be modified.")
        return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))

    if access_request.queue_type == AccessRequest.QueueType.WAITLIST:
        outreach_type = (
            request.POST.get("outreach_type", AccessRequest.WaitlistOutreachType.COMING_SOON).strip()
            or AccessRequest.WaitlistOutreachType.COMING_SOON
        )
        updated_count, skipped_count, failed_count, _, failed_emails = activate_waitlist_requests(
            access_requests=[access_request],
            reviewed_by=request.user,
            request=request,
            decision_reason=request.POST.get("decision_reason", "").strip() or access_request.verification_reason,
            outreach_type=outreach_type,
        )
        if updated_count:
            messages.success(request, f"1 {get_waitlist_outreach_single_label(outreach_type)} sent. 0 skipped. 0 failed.")
        else:
            failed_email = failed_emails[0] if failed_emails else access_request.email
            messages.warning(
                request,
                f"0 {get_waitlist_outreach_action_label(outreach_type)}. {skipped_count} skipped. {failed_count} failed. Failed: {failed_email}.",
            )
    else:
        _, _, notification_sent = approve_access_request(
            access_request=access_request,
            reviewed_by=request.user,
            request=request,
            decision_reason=request.POST.get("decision_reason", "").strip() or access_request.verification_reason,
            evidence_reference=request.POST.get("evidence_reference", "").strip(),
        )
        if notification_sent:
            messages.success(request, "1 manual verification approved. Continuation email sent. 0 skipped. 0 failed.")
        else:
            messages.warning(request, f"0 manual verification emails sent. 0 skipped. 1 failed. Failed: {access_request.email}.")
    return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))


@require_portal_permission("can_review_manual_requests")
def intake_reject_request(request, request_id):
    access_request = get_object_or_404(
        AccessRequest,
        pk=request_id,
        queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
    )
    if request.method != "POST":
        return redirect("intake_request_detail", request_id=request_id)

    if not can_reject_access_request(access_request):
        messages.warning(request, "This request can no longer be modified.")
        return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))

    notification_sent = reject_access_request(
        access_request=access_request,
        reviewed_by=request.user,
        decision_reason=request.POST.get("decision_reason", "").strip() or access_request.verification_reason,
        evidence_reference=request.POST.get("evidence_reference", "").strip(),
    )
    if notification_sent:
        messages.success(request, "1 verification failure recorded. Rejection email sent. 0 skipped. 0 failed.")
    else:
        messages.warning(request, f"0 rejection emails sent. 0 skipped. 1 failed. Failed: {access_request.email}.")
    return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))


@require_portal_permission("can_review_manual_requests")
def intake_terminate_access(request, request_id):
    access_request = get_object_or_404(AccessRequest, pk=request_id)
    agent_account = AgentUser.objects.filter(email=access_request.email).first()
    if request.method != "POST":
        return redirect("intake_request_detail", request_id=request_id)
    if not can_terminate_agent_access(agent_account):
        messages.warning(request, "This account does not currently have active Whisper access.")
        return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))

    termination_reason = request.POST.get("termination_reason", "").strip()
    valid_reasons = {value for value, _ in AccessRequest.TerminationReason.choices}
    if termination_reason not in valid_reasons:
        messages.warning(request, "Choose a termination reason before revoking access.")
        return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))
    if request.POST.get("confirm_termination") != "on":
        messages.warning(request, "Confirm access revocation before continuing.")
        return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))

    _, notification_sent = terminate_agent_access(
        access_request=access_request,
        agent=agent_account,
        terminated_by=request.user,
        termination_reason=termination_reason,
        termination_note=request.POST.get("termination_note", "").strip(),
    )
    if notification_sent:
        messages.success(request, "Access terminated. Termination email sent.")
    else:
        messages.warning(request, "Access terminated, but the termination email could not be sent.")
    return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))
