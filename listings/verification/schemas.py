from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional, Union


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    MANUAL_REVIEW = "manual_review"
    UNSUPPORTED_STATE = "unsupported_state"
    PROVIDER_ERROR = "provider_error"
    NO_MATCH = "no_match"
    EXPIRED = "expired"
    NAME_MISMATCH = "name_mismatch"


@dataclass
class VerificationResult:
    success: bool
    status: VerificationStatus
    state: str
    submitted_full_name: str
    submitted_license_number: str
    normalized_submitted_name: str
    matched_name: str = ""
    matched_license_number: str = ""
    matched_license_type: str = ""
    matched_business_name: str = ""
    matched_business_city: str = ""
    matched_business_state: str = ""
    matched_expiration_date: Optional[date] = None
    provider: str = ""
    raw_payload: Optional[Union[dict, list]] = field(default_factory=dict)
    reason: str = ""
    requires_manual_review: bool = True
