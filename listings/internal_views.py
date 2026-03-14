from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from .intake import approve_access_request, reject_access_request
from .models import AccessRequest


def has_portal_permission(user, perm):
    return user.is_authenticated and user.has_perm(f"listings.{perm}")


def require_portal_permission(perm):
    def decorator(view_func):
        @never_cache
        @login_required(login_url="intake_login")
        def wrapped(request, *args, **kwargs):
            if not has_portal_permission(request.user, "can_access_intake_portal"):
                return HttpResponseForbidden("You do not have access to the intake portal.")
            if perm and not has_portal_permission(request.user, perm):
                return HttpResponseForbidden("You do not have permission to view this intake area.")
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
            return HttpResponseForbidden("You do not have access to the intake portal.")
        messages.success(request, "Signed in to the intake portal.")
        return redirect("intake_home")

    return render(request, "intake/login.html", {"form": form})


@never_cache
@login_required(login_url="intake_login")
def intake_logout(request):
    logout(request)
    messages.success(request, "Signed out of the intake portal.")
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
    requests = AccessRequest.objects.filter(queue_type=AccessRequest.QueueType.MANUAL_REVIEW).order_by("-updated_at")
    return render(request, "intake/manual_review_list.html", {"requests": requests})


@require_portal_permission("can_manage_waitlist")
def intake_waitlist(request):
    requests = AccessRequest.objects.filter(queue_type=AccessRequest.QueueType.WAITLIST).order_by("-updated_at")
    return render(request, "intake/waitlist_list.html", {"requests": requests})


@require_portal_permission(None)
def intake_request_detail(request, request_id):
    access_request = get_object_or_404(AccessRequest, pk=request_id)
    if access_request.queue_type == AccessRequest.QueueType.MANUAL_REVIEW:
        if not has_portal_permission(request.user, "can_review_manual_requests"):
            return HttpResponseForbidden("You do not have permission to review manual requests.")
    elif access_request.queue_type == AccessRequest.QueueType.WAITLIST:
        if not has_portal_permission(request.user, "can_manage_waitlist"):
            return HttpResponseForbidden("You do not have permission to view waitlist requests.")
    else:
        return HttpResponseForbidden("This intake request is not available in the internal portal.")

    return render(request, "intake/request_detail.html", {"access_request": access_request})


@require_portal_permission("can_review_manual_requests")
def intake_verify_request(request, request_id):
    access_request = get_object_or_404(
        AccessRequest,
        pk=request_id,
        queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
    )
    if request.method != "POST":
        return redirect("intake_request_detail", request_id=request_id)

    _, _, notification_sent = approve_access_request(
        access_request=access_request,
        reviewed_by=request.user,
        request=request,
        decision_reason=request.POST.get("decision_reason", "").strip() or access_request.verification_reason,
    )
    if notification_sent:
        messages.success(request, "Access request verified and continuation email sent.")
    else:
        messages.warning(request, "Access request verified, but notification email could not be sent.")
    return redirect("intake_request_detail", request_id=request_id)


@require_portal_permission("can_review_manual_requests")
def intake_reject_request(request, request_id):
    access_request = get_object_or_404(
        AccessRequest,
        pk=request_id,
        queue_type=AccessRequest.QueueType.MANUAL_REVIEW,
    )
    if request.method != "POST":
        return redirect("intake_request_detail", request_id=request_id)

    notification_sent = reject_access_request(
        access_request=access_request,
        reviewed_by=request.user,
        decision_reason=request.POST.get("decision_reason", "").strip() or access_request.verification_reason,
    )
    if notification_sent:
        messages.success(request, "Access request rejected and rejection email sent.")
    else:
        messages.warning(request, "Request rejected, but notification email could not be sent.")
    return redirect("intake_request_detail", request_id=request_id)
