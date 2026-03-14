from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .base import BaseEmailProvider


class SMTPProvider(BaseEmailProvider):
    def send_email(self, to_email, subject, html_body, text_body=None):
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body or "",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
        )
        if html_body:
            message.attach_alternative(html_body, "text/html")
        return message.send()
