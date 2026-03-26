from django.conf import settings
from django.core import signing
from django.urls import reverse

from services.email import send_email
from services.email.messages import (
    build_access_request_activation_email,
    build_access_request_signup_email,
    build_access_request_manual_approval_email,
    build_access_request_rejection_email,
    build_access_request_waitlist_email,
    build_account_verification_email,
)
from .auth_links import build_auth_access_url, create_auth_access_token


EMAIL_VERIFICATION_SALT = "agent-email-verification"
ACCESS_REQUEST_SIGNUP_SALT = "access-request-signup"
ACCESS_REQUEST_CONTINUATION_SALT = "access-request-continuation"


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


def send_access_request_rejection_email(*, access_request):
    subject, html_body, text_body = build_access_request_rejection_email()
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
