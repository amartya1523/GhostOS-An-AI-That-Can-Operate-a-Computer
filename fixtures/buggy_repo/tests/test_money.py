from decimal import Decimal

import pytest

from invoicing.money import format_amount, parse_amount


def test_parse_plain():
    assert parse_amount("420") == Decimal("420")


def test_parse_thousands_separator():
    assert parse_amount("$1,200.50") == Decimal("1200.50")


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_amount("twelve")


def test_format():
    assert format_amount(Decimal("1200.5")) == "$1,200.50"
