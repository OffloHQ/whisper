from typing import Tuple

from listings.verification.base import BaseVerificationProvider
from listings.verification.schemas import VerificationResult, VerificationStatus
from listings.verification.utils import normalize_name_for_comparison


class ManualProvider(BaseVerificationProvider):
    provider_name = "manual"
    supported_states: Tuple[str, ...] = ()

    def verify(self, *, full_name: str, state: str, license_number: str) -> VerificationResult:
        return VerificationResult(
            success=False,
            status=VerificationStatus.UNSUPPORTED_STATE,
            state=state,
            submitted_full_name=full_name,
            submitted_license_number=license_number,
            normalized_submitted_name=normalize_name_for_comparison(full_name),
            provider=self.provider_name,
            raw_payload={},
            reason=f"Automated verification is not available for {state or 'this state'}.",
            requires_manual_review=True,
        )
