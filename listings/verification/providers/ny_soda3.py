import json
import logging
from urllib import error, parse, request

from django.conf import settings
from django.utils import timezone

from listings.verification.base import BaseVerificationProvider
from listings.verification.schemas import VerificationResult, VerificationStatus
from listings.verification.utils import (
    names_confidently_match,
    normalize_license_number,
    normalize_name_for_comparison,
    parse_provider_date,
)

logger = logging.getLogger(__name__)


class NYSoda3Provider(BaseVerificationProvider):
    provider_name = "ny_soda3"
    supported_states = ("NY",)

    def verify(self, *, full_name: str, state: str, license_number: str) -> VerificationResult:
        normalized_name = normalize_name_for_comparison(full_name)
        normalized_license_number = normalize_license_number(license_number)
        missing_settings = [
            setting_name
            for setting_name, setting_value in (
                ("NY_OPEN_DATA_BASE_URL", settings.NY_OPEN_DATA_BASE_URL),
                ("NY_REAL_ESTATE_DATASET_ID", settings.NY_REAL_ESTATE_DATASET_ID),
                ("NY_LICENSE_API_APP_TOKEN", settings.NY_LICENSE_API_APP_TOKEN),
            )
            if not setting_value
        ]
        if missing_settings:
            logger.warning(
                "NY verification provider is not configured. Missing settings: %s",
                ", ".join(missing_settings),
            )
            return VerificationResult(
                success=False,
                status=VerificationStatus.PROVIDER_ERROR,
                state=state,
                submitted_full_name=full_name,
                submitted_license_number=normalized_license_number,
                normalized_submitted_name=normalized_name,
                provider=self.provider_name,
                raw_payload={},
                reason=f"NY verification provider is not configured. Missing settings: {', '.join(missing_settings)}.",
                requires_manual_review=True,
            )

        query = parse.urlencode(
            {
                "license_number": normalized_license_number,
                "$limit": 5,
            }
        )
        api_url = f"{settings.NY_OPEN_DATA_BASE_URL.rstrip('/')}/{settings.NY_REAL_ESTATE_DATASET_ID}.json?{query}"
        headers = {
            "X-App-Token": settings.NY_LICENSE_API_APP_TOKEN,
        }
        req = request.Request(api_url, headers=headers)

        try:
            with request.urlopen(req, timeout=settings.NY_LICENSE_API_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            logger.exception(
                "NY verification request failed for license_number=%s state=%s provider=%s",
                normalized_license_number,
                state,
                self.provider_name,
            )
            return VerificationResult(
                success=False,
                status=VerificationStatus.PROVIDER_ERROR,
                state=state,
                submitted_full_name=full_name,
                submitted_license_number=normalized_license_number,
                normalized_submitted_name=normalized_name,
                provider=self.provider_name,
                raw_payload={},
                reason=str(exc),
                requires_manual_review=True,
            )

        if not payload:
            return VerificationResult(
                success=False,
                status=VerificationStatus.NO_MATCH,
                state=state,
                submitted_full_name=full_name,
                submitted_license_number=normalized_license_number,
                normalized_submitted_name=normalized_name,
                provider=self.provider_name,
                raw_payload=payload,
                reason="No matching NY license record was found.",
                requires_manual_review=True,
            )

        record = next(
            (
                item
                for item in payload
                if normalize_license_number(str(item.get("license_number", ""))) == normalized_license_number
            ),
            payload[0],
        )
        expiration_date = parse_provider_date(record.get("license_expiration_date", ""))
        matched_name = str(record.get("license_holder_name", "")).strip()
        matched_license_number = normalize_license_number(str(record.get("license_number", "")))

        if matched_license_number != normalized_license_number:
            return VerificationResult(
                success=False,
                status=VerificationStatus.NO_MATCH,
                state=state,
                submitted_full_name=full_name,
                submitted_license_number=normalized_license_number,
                normalized_submitted_name=normalized_name,
                provider=self.provider_name,
                raw_payload=record,
                reason="Returned record did not match the submitted license number.",
                requires_manual_review=True,
            )

        if expiration_date is None or expiration_date <= timezone.now().date():
            return VerificationResult(
                success=False,
                status=VerificationStatus.EXPIRED,
                state=state,
                submitted_full_name=full_name,
                submitted_license_number=normalized_license_number,
                normalized_submitted_name=normalized_name,
                matched_name=matched_name,
                matched_license_number=matched_license_number,
                matched_license_type=str(record.get("license_type", "")).strip(),
                matched_business_name=str(record.get("business_name", "")).strip(),
                matched_business_city=str(record.get("business_city", "")).strip(),
                matched_business_state="NY",
                matched_expiration_date=expiration_date,
                provider=self.provider_name,
                raw_payload=record,
                reason="The matched license is expired.",
                requires_manual_review=True,
            )

        if not names_confidently_match(full_name, matched_name):
            return VerificationResult(
                success=False,
                status=VerificationStatus.NAME_MISMATCH,
                state=state,
                submitted_full_name=full_name,
                submitted_license_number=normalized_license_number,
                normalized_submitted_name=normalized_name,
                matched_name=matched_name,
                matched_license_number=matched_license_number,
                matched_license_type=str(record.get("license_type", "")).strip(),
                matched_business_name=str(record.get("business_name", "")).strip(),
                matched_business_city=str(record.get("business_city", "")).strip(),
                matched_business_state="NY",
                matched_expiration_date=expiration_date,
                provider=self.provider_name,
                raw_payload=record,
                reason="The submitted name did not confidently match the license record.",
                requires_manual_review=True,
            )

        return VerificationResult(
            success=True,
            status=VerificationStatus.VERIFIED,
            state=state,
            submitted_full_name=full_name,
            submitted_license_number=normalized_license_number,
            normalized_submitted_name=normalized_name,
            matched_name=matched_name,
            matched_license_number=matched_license_number,
            matched_license_type=str(record.get("license_type", "")).strip(),
            matched_business_name=str(record.get("business_name", "")).strip(),
            matched_business_city=str(record.get("business_city", "")).strip(),
            matched_business_state="NY",
            matched_expiration_date=expiration_date,
            provider=self.provider_name,
            raw_payload=record,
            reason="Verified against the NY licensing dataset.",
            requires_manual_review=False,
        )
