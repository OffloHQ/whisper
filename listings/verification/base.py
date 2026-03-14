from typing import Tuple

from .schemas import VerificationResult


class BaseVerificationProvider:
    provider_name = ""
    supported_states: Tuple[str, ...] = ()

    def verify(self, *, full_name: str, state: str, license_number: str) -> VerificationResult:
        raise NotImplementedError
