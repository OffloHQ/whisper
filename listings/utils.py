from decimal import Decimal, InvalidOperation


WESTCHESTER_TOWNS = [
    "Ardsley",
    "Bronxville",
    "Chappaqua",
    "Dobbs Ferry",
    "Eastchester",
    "Edgemont",
    "Harrison",
    "Irvington",
    "Larchmont",
    "Mamaroneck",
    "Mount Kisco",
    "New Rochelle",
    "Pelham",
    "Pleasantville",
    "Purchase",
    "Rye",
    "Rye Brook",
    "Scarsdale",
    "Tarrytown",
    "White Plains",
]


def get_town_area_choices(include_blank=False):
    choices = [(town, town) for town in WESTCHESTER_TOWNS]
    if include_blank:
        return [("", "Any town / area"), *choices]
    return choices

PRICE_INPUT_ERROR = "Enter a valid price like 1000000, 1.2, 1.2M, or 850K."


def parse_price_input(raw_value):
    if raw_value is None:
        raise ValueError(PRICE_INPUT_ERROR)

    normalized_value = str(raw_value).replace(",", "").strip()
    if not normalized_value:
        raise ValueError(PRICE_INPUT_ERROR)

    suffix = normalized_value[-1].lower()

    try:
        if suffix == "m":
            amount = Decimal(normalized_value[:-1]) * Decimal("1000000")
        elif suffix == "k":
            amount = Decimal(normalized_value[:-1]) * Decimal("1000")
        else:
            amount = Decimal(normalized_value)
            if "." in normalized_value:
                amount *= Decimal("1000000")
    except (InvalidOperation, IndexError):
        raise ValueError(PRICE_INPUT_ERROR)

    if amount != amount.to_integral_value() or amount <= 0:
        raise ValueError(PRICE_INPUT_ERROR)

    return int(amount)


def format_listing_price(price: int) -> str:
    if price >= 1_000_000:
        value = price / 1_000_000
        if value.is_integer():
            return f"${int(value)}M"
        return f"${value:.1f}M"

    if price >= 1_000:
        value = price / 1_000
        if value.is_integer():
            return f"${int(value)}K"
        return f"${value:.1f}K"

    return f"${price}"
