from django.conf import settings

from .emailit_provider import EmailitProvider
from .smtp_provider import SMTPProvider


PROVIDERS = {
    "emailit": EmailitProvider,
    "smtp": SMTPProvider,
}


def get_email_provider():
    provider_name = getattr(settings, "EMAIL_PROVIDER", "smtp")
    provider_class = PROVIDERS.get(provider_name)
    if provider_class is None:
        raise ValueError(f"Unsupported email provider: {provider_name}")
    return provider_class()


def send_email(to_email, subject, html_body, text_body=None):
    provider = get_email_provider()
    return provider.send_email(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        text_body=text_body,
    )
