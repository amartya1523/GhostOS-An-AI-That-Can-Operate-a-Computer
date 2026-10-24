import re

_ID = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<num>\d+)$")


def next_invoice_id(last_id: str) -> str:
    """INV-0041 -> INV-0042, keeping the zero padding width."""
    match = _ID.match(last_id)
    if not match:
        raise ValueError(f"bad invoice id: {last_id!r}")
    number = int(match["num"]) + 1
    return f"{match['prefix']}-{number}"
