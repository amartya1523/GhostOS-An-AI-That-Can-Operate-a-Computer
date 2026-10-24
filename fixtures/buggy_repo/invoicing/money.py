from decimal import Decimal, InvalidOperation


def parse_amount(text: str) -> Decimal:
    """Parse a user-entered amount like "$1,200.50" into a Decimal."""
    cleaned = text.strip().lstrip("$")
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {text!r}") from exc


def format_amount(value: Decimal) -> str:
    return f"${value:,.2f}"
