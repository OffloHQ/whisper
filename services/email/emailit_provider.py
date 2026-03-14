import json
import logging
from urllib import error, request

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BaseEmailProvider

logger = logging.getLogger(__name__)


class EmailitProvider(BaseEmailProvider):
    def send_email(self, to_email, subject, html_body, text_body=None):
        if not settings.EMAILIT_API_KEY:
            raise ImproperlyConfigured(
                "EMAILIT_API_KEY must be configured to use the Emailit provider."
            )

        payload = {
            "from": settings.DEFAULT_FROM_EMAIL,
            "to": [to_email] if isinstance(to_email, str) else to_email,
            "subject": subject,
            "html": html_body,
        }

        if text_body:
            payload["text"] = text_body

        req = request.Request(
            settings.EMAILIT_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {settings.EMAILIT_API_KEY}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

        try:
            with request.urlopen(req) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            logger.warning(
                "Emailit request failed status=%s body=%s",
                exc.code,
                response_body,
            )
            raise RuntimeError(
                f"Emailit API request failed with status {exc.code}: {response_body}"
            ) from exc
