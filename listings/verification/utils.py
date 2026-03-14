import re
from datetime import date
from typing import List, Optional


def normalize_state_code(state: str) -> str:
    return state.strip().upper()


def normalize_license_number(license_number: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", license_number.upper())


def normalize_name_for_comparison(name: str) -> str:
    tokens = tokenize_name(name)
    return " ".join(tokens)


def tokenize_name(name: str) -> List[str]:
    cleaned = re.sub(r"[^a-z0-9\s]", " ", name.lower())
    return [token for token in cleaned.split() if token]


def names_confidently_match(submitted_name: str, provider_name: str) -> bool:
    submitted_tokens = tokenize_name(submitted_name)
    provider_tokens = tokenize_name(provider_name)
    if len(submitted_tokens) < 2 or len(provider_tokens) < 2:
        return False

    submitted_first = submitted_tokens[0]
    submitted_last = submitted_tokens[-1]
    provider_first = provider_tokens[0]
    provider_last = provider_tokens[-1]
    provider_token_set = set(provider_tokens)

    direct_match = submitted_first in provider_token_set and submitted_last in provider_token_set
    reversed_match = (
        provider_first == submitted_last and
        provider_last == submitted_first
    )
    return direct_match or reversed_match


def parse_provider_date(raw_value: str) -> Optional[date]:
    if not raw_value:
        return None
    try:
        return date.fromisoformat(raw_value.split("T", 1)[0])
    except ValueError:
        return None
