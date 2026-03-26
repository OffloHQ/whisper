from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache

from .intake import (
    activate_waitlist_requests,
    approve_access_request,
    approve_manual_review_requests,
    can_activate_access_request,
    can_reject_access_request,
    get_resend_access_email_eligible_emails,
    reject_access_request,
    reject_manual_review_requests,
    send_access_request_invites,
)
from .models import AccessRequest
from .verification.utils import get_state_display_name

QUEUE_PAGE_SIZE = 25
QUEUE_SORT_OPTIONS = {
    "date_desc": ("-updated_at", "-pk"),
    "date_asc": ("updated_at", "pk"),
    "state": ("state", "-updated_at", "-pk"),
    "name": ("full_name", "email", "-updated_at", "-pk"),
    "status": ("status", "-updated_at", "-pk"),
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
):
    status_filter = request.GET.get("status", "").strip()
    state_filter = request.GET.get("state", "").strip()
    search_query = request.GET.get("q", "").strip()
    sort_key = request.GET.get("sort", "date_desc").strip()
    if sort_key not in QUEUE_SORT_OPTIONS:
        sort_key = "date_desc"

    requests = AccessRequest.objects.filter(queue_type=base_queue_type)
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
        if access_request.last_notification_sent_at and access_request.last_notification_type:
            access_request.last_email_display = (
                f"{access_request.get_last_notification_type_display()} "
                f"on {access_request.last_notification_sent_at.strftime('%b %-d, %Y')}"
            )
        elif access_request.last_notification_sent_at:
            access_request.last_email_display = access_request.last_notification_sent_at.strftime("%b %-d, %Y")
        else:
            access_request.last_email_display = "No email sent"

    state_options = [
        {"value": state_code, "label": get_state_display_name(state_code)}
        for state_code in AccessRequest.objects.exclude(state="").values_list("state", flat=True).distinct().order_by("state")
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


def has_portal_permission(user, perm):
    return user.is_authenticated and user.has_perm(f"listings.{perm}")


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
        "manual_review_count": AccessRequest.objects.filter(queue_type=AccessRequest.QueueType.MANUAL_REVIEW).count(),
        "waitlist_count": AccessRequest.objects.filter(queue_type=AccessRequest.QueueType.WAITLIST).count(),
        "can_review_manual_requests": has_portal_permission(request.user, "can_review_manual_requests"),
        "can_manage_waitlist": has_portal_permission(request.user, "can_manage_waitlist"),
    }
    return render(request, "intake/index.html", context)


@require_portal_permission("can_review_manual_requests")
def intake_manual_review(request):
    return build_queue_dashboard_context(
        request,
        base_queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
        template_name="intake/manual_review_list.html",
        queue_name="Manual Review",
        queue_subtitle="Verification exceptions that need approval or rejection.",
        bulk_actions=[
            {"value": "approve", "label": "Approve Selected"},
            {"value": "reject", "label": "Reject Selected"},
        ],
        bulk_form_action="intake_process_manual_review_requests",
        default_note_placeholder="Optional note for approve or reject actions",
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
        queue_subtitle="Markets awaiting launch, activation, or access-email resend.",
        bulk_actions=[
            {"value": "activate", "label": "Activate Selected"},
            {"value": "invite", "label": "Resend Access Email"},
        ],
        bulk_form_action="intake_activate_waitlist_requests",
        default_note_placeholder="Optional note for activation actions",
        empty_message="No waitlist records matched the current filters.",
        show_reject_action=False,
        show_invite_action=True,
    )


@require_portal_permission("can_manage_waitlist")
def intake_activate_waitlist_requests(request):
    if request.method != "POST":
        return redirect("intake_waitlist")

    request_ids = request.POST.getlist("request_ids")
    queryset = AccessRequest.objects.filter(pk__in=request_ids).order_by("-updated_at")
    bulk_action = request.POST.get("bulk_action", "activate").strip()
    if bulk_action == "invite":
        invited_count, skipped_count, failed_count, skipped_emails, failed_emails = send_access_request_invites(
            access_requests=queryset,
            request=request,
        )
        messages.info(
            request,
            build_queue_message(
                action_label="access emails resent",
                processed_count=invited_count,
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
        )
        messages.info(
            request,
            build_queue_message(
                action_label="requests activated",
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
                action_label="requests rejected",
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
                action_label="requests approved",
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
    if access_request.queue_type == AccessRequest.QueueType.MANUAL_REVIEW:
        if not has_portal_permission(request.user, "can_review_manual_requests"):
            return HttpResponseForbidden("You do not have permission to review manual requests.")
    elif access_request.queue_type == AccessRequest.QueueType.WAITLIST:
        if not has_portal_permission(request.user, "can_manage_waitlist"):
            return HttpResponseForbidden("You do not have permission to view waitlist requests.")
    elif access_request.decision_status in {
        AccessRequest.DecisionStatus.APPROVED,
        AccessRequest.DecisionStatus.COMPLETED,
        AccessRequest.DecisionStatus.REJECTED,
    }:
        if not (
            has_portal_permission(request.user, "can_review_manual_requests")
            or has_portal_permission(request.user, "can_manage_waitlist")
        ):
            return HttpResponseForbidden("You do not have permission to view completed intake requests.")
    else:
        return HttpResponseForbidden("This request is not available in the Admin Portal.")

    return render(
        request,
        "intake/request_detail.html",
        {
            "access_request": access_request,
            "can_activate_request": can_activate_access_request(access_request),
            "can_reject_request": can_reject_access_request(access_request),
            "can_resend_access_email": access_request.email in get_resend_access_email_eligible_emails([access_request.email]),
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
            return HttpResponseForbidden("You do not have permission to activate waitlist requests.")
    else:
        return HttpResponseForbidden("This intake request cannot be activated.")

    if not can_activate_access_request(access_request):
        messages.warning(request, "This request can no longer be modified.")
        return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))

    _, _, notification_sent = approve_access_request(
        access_request=access_request,
        reviewed_by=request.user,
        request=request,
        decision_reason=request.POST.get("decision_reason", "").strip() or access_request.verification_reason,
    )
    if notification_sent:
        messages.success(request, "1 request activated. 0 skipped. 0 failed.")
    else:
        messages.warning(request, f"0 requests activated. 0 skipped. 1 failed. Failed: {access_request.email}.")
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
    )
    if notification_sent:
        messages.success(request, "1 request rejected. 0 skipped. 0 failed.")
    else:
        messages.warning(request, f"0 requests rejected. 0 skipped. 1 failed. Failed: {access_request.email}.")
    return redirect(get_safe_redirect_target(request, default_name="intake_request_detail", request_id=request_id))
