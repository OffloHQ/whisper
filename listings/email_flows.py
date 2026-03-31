from django.conf import settings
from django.core import signing
from django.urls import reverse
from django.utils import timezone
from urllib.parse import urlencode

from services.email import send_email
from services.email.messages import (
    build_access_request_activation_email,
    build_front_door_request_access_email,
    build_access_request_signup_email,
    build_access_request_signup_reminder_email,
    build_access_request_manual_approval_email,
    build_access_request_rejection_email,
    build_access_request_terminated_email,
    build_access_request_waitlist_email,
    build_waitlist_coming_soon_email,
    build_waitlist_open_signup_email,
    build_account_verification_email,
)
from .auth_links import build_auth_access_url, create_auth_access_token, send_magic_sign_in_link
from .models import AccessRequest, AgentUser


EMAIL_VERIFICATION_SALT = "agent-email-verification"
ACCESS_REQUEST_SIGNUP_SALT = "access-request-signup"
ACCESS_REQUEST_CONTINUATION_SALT = "access-request-continuation"
ACCESS_REQUEST_WAITLIST_UNSUBSCRIBE_SALT = "access-request-waitlist-unsubscribe"


def build_agent_email_verification_token(agent_email):
    return signing.dumps(
        {"agent_email_id": agent_email.id, "agent_id": agent_email.agent_id},
        salt=EMAIL_VERIFICATION_SALT,
    )


def load_agent_email_verification_token(token):
    return signing.loads(
        token,
        salt=EMAIL_VERIFICATION_SALT,
        max_age=getattr(settings, "ACCOUNT_EMAIL_VERIFICATION_LINK_MAX_AGE", 60 * 60 * 24 * 7),
    )


def send_agent_email_verification(request, agent_email):
    verification_url = request.build_absolute_uri(
        reverse("verify_agent_email", args=[build_agent_email_verification_token(agent_email)])
    )
    subject, html_body, text_body = build_account_verification_email(
        agent_name=agent_email.agent.name,
        verification_url=verification_url,
    )
    return send_email(
        to_email=agent_email.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def build_access_request_signup_token(access_request):
    return signing.dumps(
        {"access_request_id": access_request.id, "email": access_request.email},
        salt=ACCESS_REQUEST_SIGNUP_SALT,
    )


def load_access_request_signup_token(token):
    return signing.loads(
        token,
        salt=ACCESS_REQUEST_SIGNUP_SALT,
        max_age=getattr(settings, "ACCESS_REQUEST_SIGNUP_LINK_MAX_AGE", 60 * 60 * 24 * 7),
    )


def send_access_request_signup_email(request, access_request):
    signup_url = request.build_absolute_uri(
        reverse("signup_identity", args=[build_access_request_signup_token(access_request)])
    )
    subject, html_body, text_body = build_access_request_signup_email(signup_url=signup_url)
    return send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def build_access_request_signup_url(access_request):
    return f"{settings.SITE_BASE_URL.rstrip('/')}{reverse('signup_identity', args=[build_access_request_signup_token(access_request)])}"


def build_access_request_continuation_url(access_request):
    return f"{settings.SITE_BASE_URL.rstrip('/')}{reverse('signup_contact_continue', args=[build_access_request_continuation_token(access_request)])}"


def send_access_request_signup_reminder_email(*, access_request, reminder_day):
    subject, html_body, text_body = build_access_request_signup_reminder_email(
        signup_url=build_access_request_signup_url(access_request),
        reminder_day=reminder_day,
    )
    return send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def build_access_request_continuation_token(access_request):
    return signing.dumps(
        {"access_request_id": access_request.id, "email": access_request.email},
        salt=ACCESS_REQUEST_CONTINUATION_SALT,
    )


def load_access_request_continuation_token(token):
    return signing.loads(
        token,
        salt=ACCESS_REQUEST_CONTINUATION_SALT,
        max_age=getattr(settings, "ACCESS_REQUEST_CONTINUATION_LINK_MAX_AGE", 60 * 60 * 24 * 7),
    )


def build_waitlist_unsubscribe_token(access_request):
    return signing.dumps(
        {"access_request_id": access_request.id, "email": access_request.email},
        salt=ACCESS_REQUEST_WAITLIST_UNSUBSCRIBE_SALT,
    )


def load_waitlist_unsubscribe_token(token):
    return signing.loads(
        token,
        salt=ACCESS_REQUEST_WAITLIST_UNSUBSCRIBE_SALT,
        max_age=getattr(settings, "ACCESS_REQUEST_WAITLIST_UNSUBSCRIBE_LINK_MAX_AGE", 60 * 60 * 24 * 365),
    )


def send_access_request_manual_approval_email(*, access_request, continuation_link):
    subject, html_body, text_body = build_access_request_manual_approval_email(
        continuation_link=continuation_link,
    )
    return send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def send_access_request_continuation_email(*, access_request):
    return send_access_request_manual_approval_email(
        access_request=access_request,
        continuation_link=build_access_request_continuation_url(access_request),
    )


def send_access_request_rejection_email(*, access_request, review_reason=""):
    subject, html_body, text_body = build_access_request_rejection_email(review_reason=review_reason)
    return send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def send_access_request_waitlist_email(*, access_request):
    subject, html_body, text_body = build_access_request_waitlist_email()
    return send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def send_access_request_terminated_email(*, access_request, termination_reason=""):
    subject, html_body, text_body = build_access_request_terminated_email(termination_reason=termination_reason)
    return send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def build_waitlist_unsubscribe_url(request, access_request):
    return request.build_absolute_uri(
        reverse("unsubscribe_waitlist", args=[build_waitlist_unsubscribe_token(access_request)])
    )


def build_waitlist_signup_url(request, access_request):
    return request.build_absolute_uri(
        f"{reverse('request_access')}?{urlencode({'email': access_request.email})}"
    )


def send_waitlist_coming_soon_email(request, *, access_request):
    subject, html_body, text_body = build_waitlist_coming_soon_email(
        unsubscribe_url=build_waitlist_unsubscribe_url(request, access_request),
    )
    return send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def send_waitlist_open_signup_email(request, *, access_request):
    subject, html_body, text_body = build_waitlist_open_signup_email(
        signup_url=build_waitlist_signup_url(request, access_request),
        unsubscribe_url=build_waitlist_unsubscribe_url(request, access_request),
    )
    return send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )


def send_access_request_activation_email(request, *, access_request, agent):
    auth_access_token = create_auth_access_token(agent=agent, email=access_request.email)
    sign_in_url = build_auth_access_url(request, auth_access_token)
    subject, html_body, text_body = build_access_request_activation_email(sign_in_url=sign_in_url)
    send_email(
        to_email=access_request.email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    return auth_access_token, sign_in_url


def build_front_door_request_access_url(request, *, email):
    return request.build_absolute_uri(
        f"{reverse('request_access')}?{urlencode({'email': email})}"
    )


def send_front_door_sign_in_email(request, *, email):
    active_agent = AgentUser.objects.filter(
        email=email,
        is_active=True,
        is_verified=True,
        signup_status=AgentUser.SignupStatus.ACTIVE,
        deleted_at__isnull=True,
    ).first()
    if active_agent is not None:
        return "magic_link", send_magic_sign_in_link(request, agent=active_agent, email=email)

    access_request = AccessRequest.objects.filter(email=email).first()
    pending_contact_agent = AgentUser.objects.filter(
        email=email,
        signup_status=AgentUser.SignupStatus.PENDING_CONTACT,
        deleted_at__isnull=True,
    ).first()

    if access_request is not None and access_request.completed_at is None and pending_contact_agent is not None:
        return "continue_signup", send_access_request_continuation_email(access_request=access_request)

    if access_request is not None and access_request.completed_at is None:
        if access_request.status in {
            AccessRequest.Status.REQUESTED,
            AccessRequest.Status.LINK_SENT,
        }:
            access_request.status = AccessRequest.Status.LINK_SENT
            access_request.signup_sent_at = timezone.now()
            access_request.save(update_fields=["status", "signup_sent_at", "updated_at"])
            return "signup_resume", send_access_request_signup_email(request, access_request)

        request_access_url = build_front_door_request_access_url(request, email=email)
        subject, html_body, text_body = build_front_door_request_access_email(request_access_url=request_access_url)
        return "request_access", send_email(
            to_email=email,
            subject=subject,
            html_body=html_body,
            text_body=text_body,
        )

    request_access_url = build_front_door_request_access_url(request, email=email)
    subject, html_body, text_body = build_front_door_request_access_email(request_access_url=request_access_url)
    return "request_access", send_email(
        to_email=email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
