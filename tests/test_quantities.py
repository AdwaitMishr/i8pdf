"""Numbers only mean something once scale, unit and sign are recovered."""

import pytest

from factlayer.extract.quantities import find_quantities, find_unit_context


def one(text, **kwargs):
    found = find_quantities(text, **kwargs)
    assert found, f"nothing parsed from {text!r}"
    return found[0]


@pytest.mark.parametrize("text,value,unit", [
    ("revenue of ₹ 74,540.82 million", 74_540_820_000.0, "INR"),
    ("revenue of ₹8,142 Cr", 81_420_000_000.0, "INR"),
    ("Rs. 8,142 crore", 81_420_000_000.0, "INR"),
    ("US$ 1.2 billion", 1_200_000_000.0, "USD"),
    ("grew by 6.5 percent", 6.5, "percent"),
    ("grew by 6.5 per cent", 6.5, "percent"),
    ("up 30 bps", 0.30, "percent"),
    ("eased by 1.2 percentage points", 1.2, "percentage_point"),
    ("1.4 Mn Tons of freight", 1_400_000.0, "tonne"),
    ("740 Mn parcels", 740_000_000.0, "count"),
    ("over 33,000 active customers", 33_000.0, "count"),
])
def test_scale_and_unit(text, value, unit):
    quantity = one(text)
    assert quantity.value == pytest.approx(value)
    assert quantity.unit == unit


def test_parentheses_mean_negative():
    assert one("EBITDA of ₹(452) Cr").value == pytest.approx(-4.52e9)
    assert one("margin of (6.3%)").value == pytest.approx(-6.3)


def test_precision_tracks_the_last_stated_digit():
    """₹8,142 Cr claims the nearest crore; ₹81,415.38 million far less."""
    assert one("₹8,142 Cr").precision == pytest.approx(1e7)
    assert one("₹81,415.38 million").precision == pytest.approx(1e4)


def test_years_and_identifiers_are_not_quantities():
    assert find_quantities("for the year 2024 the board met") == []
    assert find_quantities("DIN: 01173669 resigned") == []
    assert find_quantities("see Note 12 for details") == []


def test_footnote_markers_are_skipped():
    assert find_quantities("Revenue from services(2) rose") == []


def test_declared_table_unit_applies_to_bare_numbers():
    context = find_unit_context("Revenue from services (₹ Cr)")
    assert context.currency == "INR"
    quantity = one("Revenue from services (₹ Cr) 8,142", context=context)
    assert quantity.unit == "INR"


def test_a_rupee_in_prose_is_not_a_table_unit():
    """Otherwise one figure re-units every bare number near it."""
    assert find_unit_context("EBITDA was ₹127 Cr and volumes rose").currency is None


def test_currency_needs_a_word_boundary():
    """'customeRS' must not supply a rupee symbol."""
    assert one("over 33,000 active customers").unit == "count"
