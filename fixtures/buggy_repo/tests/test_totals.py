from decimal import Decimal

import pytest

from invoicing.totals import apply_discount, invoice_total


def test_discount_ten_percent():
    assert apply_discount(Decimal("200.00"), Decimal("10")) == Decimal("180.00")


def test_discount_zero():
    assert apply_discount(Decimal("99.99"), Decimal("0")) == Decimal("99.99")


def test_discount_out_of_range():
    with pytest.raises(ValueError):
        apply_discount(Decimal("10"), Decimal("150"))


def test_invoice_total_with_tax():
    lines = [(Decimal("10.00"), 3), (Decimal("2.50"), 2)]
    assert invoice_total(lines, Decimal("0.18")) == Decimal("41.30")
