from decimal import Decimal


def apply_discount(total: Decimal, percent: Decimal) -> Decimal:
    """Return the total after a percentage discount, rounded to cents."""
    if not 0 <= percent <= 100:
        raise ValueError("discount must be between 0 and 100 percent")
    return (total - total * percent).quantize(Decimal("0.01"))


def invoice_total(lines: list[tuple[Decimal, int]], tax_rate: Decimal) -> Decimal:
    """Sum (unit_price, quantity) lines and add tax."""
    subtotal = sum((price * qty for price, qty in lines), Decimal("0"))
    return (subtotal * (1 + tax_rate)).quantize(Decimal("0.01"))
