from django.shortcuts import redirect
from django.urls import resolve, Resolver404, reverse

from .models import AgentUser
from .views import get_session_agent, requires_legal_acceptance


class LegalAcceptanceMiddleware:
    PUBLIC_ROUTE_NAMES = {
        "landing",
        "request_access",
        "signup_identity",
        "signup_contact",
        "signup_contact_continue",
        "legal_acceptance",
        "terms_of_use",
        "privacy_policy",
        "consume_auth_access_token",
        "qr_sign_in_status",
        "logout_account",
    }

    PUBLIC_PATH_PREFIXES = (
        "/admin/",
        "/intake/",
        "/static/",
        "/media/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._request_is_public(request):
            return self.get_response(request)

        current_agent = get_session_agent(request)
        if (
            current_agent is not None
            and current_agent.signup_status == AgentUser.SignupStatus.ACTIVE
            and requires_legal_acceptance(current_agent)
        ):
            return redirect("legal_acceptance")

        return self.get_response(request)

    def _request_is_public(self, request):
        if any(request.path.startswith(prefix) for prefix in self.PUBLIC_PATH_PREFIXES):
            return True

        try:
            match = resolve(request.path_info)
        except Resolver404:
            return False

        return match.url_name in self.PUBLIC_ROUTE_NAMES
