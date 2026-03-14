import importlib
import pkgutil
from typing import Dict, Type

from listings.verification.base import BaseVerificationProvider
from listings.verification.providers.manual import ManualProvider
from listings.verification.schemas import VerificationResult, VerificationStatus
from listings.verification.utils import normalize_license_number, normalize_name_for_comparison, normalize_state_code


class VerificationService:
    def __init__(self):
        self._providers = self._load_providers()

    def _load_providers(self) -> Dict[str, Type[BaseVerificationProvider]]:
        providers = {}
        package_name = "listings.verification.providers"
        package = importlib.import_module(package_name)
        for module_info in pkgutil.iter_modules(package.__path__):
            if module_info.name == "manual":
                continue
            module = importlib.import_module(f"{package_name}.{module_info.name}")
            for value in module.__dict__.values():
                if (
                    isinstance(value, type)
                    and issubclass(value, BaseVerificationProvider)
                    and value is not BaseVerificationProvider
                ):
                    for state_code in value.supported_states:
                        providers[normalize_state_code(state_code)] = value
        return providers

    def get_provider(self, state: str) -> BaseVerificationProvider:
        provider_class = self._providers.get(normalize_state_code(state), ManualProvider)
        return provider_class()

    def verify_license(self, *, full_name: str, state: str, license_number: str) -> VerificationResult:
        provider = self.get_provider(state)
        normalized_state = normalize_state_code(state)
        normalized_license_number = normalize_license_number(license_number)
        try:
            return provider.verify(
                full_name=full_name,
                state=normalized_state,
                license_number=normalized_license_number,
            )
        except Exception as exc:
            return VerificationResult(
                success=False,
                status=VerificationStatus.PROVIDER_ERROR,
                state=normalized_state,
                submitted_full_name=full_name,
                submitted_license_number=normalized_license_number,
                normalized_submitted_name=normalize_name_for_comparison(full_name),
                provider=getattr(provider, "provider_name", "unknown"),
                raw_payload={},
                reason=str(exc),
                requires_manual_review=True,
            )
