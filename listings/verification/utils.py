import re
from datetime import date
from typing import List, Optional


STATE_CODE_ALIASES = {
    "al": "AL",
    "alabama": "AL",
    "ak": "AK",
    "alaska": "AK",
    "az": "AZ",
    "arizona": "AZ",
    "ar": "AR",
    "arkansas": "AR",
    "ca": "CA",
    "california": "CA",
    "co": "CO",
    "colorado": "CO",
    "ct": "CT",
    "connecticut": "CT",
    "de": "DE",
    "delaware": "DE",
    "fl": "FL",
    "florida": "FL",
    "ga": "GA",
    "georgia": "GA",
    "hi": "HI",
    "hawaii": "HI",
    "id": "ID",
    "idaho": "ID",
    "il": "IL",
    "illinois": "IL",
    "in": "IN",
    "indiana": "IN",
    "ia": "IA",
    "iowa": "IA",
    "ks": "KS",
    "kansas": "KS",
    "ky": "KY",
    "kentucky": "KY",
    "la": "LA",
    "louisiana": "LA",
    "me": "ME",
    "maine": "ME",
    "md": "MD",
    "maryland": "MD",
    "ma": "MA",
    "massachusetts": "MA",
    "mi": "MI",
    "michigan": "MI",
    "mn": "MN",
    "minnesota": "MN",
    "ms": "MS",
    "mississippi": "MS",
    "mo": "MO",
    "missouri": "MO",
    "mt": "MT",
    "montana": "MT",
    "ne": "NE",
    "nebraska": "NE",
    "nv": "NV",
    "nevada": "NV",
    "nh": "NH",
    "new hampshire": "NH",
    "nj": "NJ",
    "new jersey": "NJ",
    "nm": "NM",
    "new mexico": "NM",
    "ny": "NY",
    "new york": "NY",
    "nc": "NC",
    "north carolina": "NC",
    "nd": "ND",
    "north dakota": "ND",
    "oh": "OH",
    "ohio": "OH",
    "ok": "OK",
    "oklahoma": "OK",
    "or": "OR",
    "oregon": "OR",
    "pa": "PA",
    "pennsylvania": "PA",
    "ri": "RI",
    "rhode island": "RI",
    "sc": "SC",
    "south carolina": "SC",
    "sd": "SD",
    "south dakota": "SD",
    "tn": "TN",
    "tennessee": "TN",
    "tx": "TX",
    "texas": "TX",
    "ut": "UT",
    "utah": "UT",
    "vt": "VT",
    "vermont": "VT",
    "va": "VA",
    "virginia": "VA",
    "wa": "WA",
    "washington": "WA",
    "wv": "WV",
    "west virginia": "WV",
    "wi": "WI",
    "wisconsin": "WI",
    "wy": "WY",
    "wyoming": "WY",
    "dc": "DC",
    "district of columbia": "DC",
}

STATE_NAME_BY_CODE = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}


def normalize_state_code(state: Optional[str]) -> str:
    if not state:
        return ""
    normalized_state = " ".join(state.strip().lower().split())
    if not normalized_state:
        return ""
    return STATE_CODE_ALIASES.get(normalized_state, normalized_state.upper())


def get_state_display_name(state: Optional[str]) -> str:
    normalized_state = normalize_state_code(state)
    if not normalized_state:
        return ""
    return STATE_NAME_BY_CODE.get(normalized_state, normalized_state.title())


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
