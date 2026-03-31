from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from services.email import send_email
from services.email.messages import build_magic_sign_in_email

from .models import AuthAccessToken


def create_auth_access_token(
    *,
    agent,
    email,
    scope=AuthAccessToken.Scope.SIGN_IN,
    delivery_method=AuthAccessToken.DeliveryMethod.EMAIL,
    max_age_seconds=None,
):
    return AuthAccessToken.objects.create(
        agent=agent,
        email=email,
        scope=scope,
        delivery_method=delivery_method,
        expires_at=timezone.now()
        + timedelta(seconds=max_age_seconds or getattr(settings, "AUTH_ACCESS_TOKEN_MAX_AGE", 60 * 30)),
    )


def build_auth_access_url(request, auth_access_token):
    path = reverse("consume_auth_access_token", args=[auth_access_token.token])
    site_base_url = getattr(settings, "SITE_BASE_URL", "").rstrip("/")
    if site_base_url:
        return f"{site_base_url}{path}"
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def send_magic_sign_in_link(request, *, agent, email):
    auth_access_token = create_auth_access_token(
        agent=agent,
        email=email,
        scope=AuthAccessToken.Scope.SIGN_IN,
        delivery_method=AuthAccessToken.DeliveryMethod.EMAIL,
    )
    sign_in_url = build_auth_access_url(request, auth_access_token)
    subject, html_body, text_body = build_magic_sign_in_email(sign_in_url=sign_in_url)
    send_email(
        to_email=email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
    return auth_access_token, sign_in_url


def create_qr_sign_in_token(request, *, agent, email):
    auth_access_token = create_auth_access_token(
        agent=agent,
        email=email,
        scope=AuthAccessToken.Scope.SIGN_IN,
        delivery_method=AuthAccessToken.DeliveryMethod.QR,
        max_age_seconds=getattr(settings, "QR_AUTH_ACCESS_TOKEN_MAX_AGE", 60 * 10),
    )
    return auth_access_token, build_auth_access_url(request, auth_access_token=auth_access_token)


def get_valid_auth_access_token(token_value, *, scope=AuthAccessToken.Scope.SIGN_IN):
    token = AuthAccessToken.objects.filter(token=token_value, scope=scope).select_related("agent").first()
    if token is None or not token.is_active:
        return None
    return token


def get_auth_access_token(token_value, *, scope=AuthAccessToken.Scope.SIGN_IN):
    return AuthAccessToken.objects.filter(token=token_value, scope=scope).select_related("agent").first()


def build_qr_image_url(sign_in_url):
    return f"https://api.qrserver.com/v1/create-qr-code/?{urlencode({'size': '220x220', 'data': sign_in_url})}"
