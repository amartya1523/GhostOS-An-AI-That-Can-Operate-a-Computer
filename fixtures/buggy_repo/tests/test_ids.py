import pytest

from invoicing.ids import next_invoice_id


def test_next_id_keeps_padding():
    assert next_invoice_id("INV-0041") == "INV-0042"


def test_next_id_rolls_over_width():
    assert next_invoice_id("INV-0999") == "INV-1000"


def test_next_id_unpadded():
    assert next_invoice_id("INV-1041") == "INV-1042"


def test_bad_id():
    with pytest.raises(ValueError):
        next_invoice_id("invoice 7")
