"""Fiscal years are the main reason two true numbers disagree."""

from datetime import date

import pytest

from factlayer.extract.periods import find_periods, shift_back


def label(text):
    found = find_periods(text)
    assert found, f"no period in {text!r}"
    return found[0].label


@pytest.mark.parametrize("text,expected", [
    ("for FY24", "FY2024"),
    ("in FY25", "FY2025"),
    ("in FY2024/25", "FY2025"),          # IMF convention
    ("during 2024-25", "FY2025"),        # RBI convention
    ("growth for 2025-26", "FY2026"),
    ("in fiscal 2024", "FY2024"),
    ("Q4 FY24 revenue", "Q4 FY2024"),
    ("in 2025Q2", "2025Q2"),
    ("in H1 FY25", "H1 FY2025"),
    ("the first half of FY25", "H1 FY2025"),
    ("as of March 31, 2024", "2024-03-31"),
    ("for the Financial Year ended March 31, 2024", "2024-03-31"),
    ("in September 2025", "2025-09"),
    ("CY2024", "CY2024"),
])
def test_canonical_labels(text, expected):
    assert label(text) == expected


def test_the_three_conventions_agree():
    """FY24/2023-24/FY2023-24 name one window; this is what makes them link."""
    assert label("FY24") == label("during 2023-24") == label("FY2023/24")


def test_bare_date_is_not_read_as_a_two_digit_year():
    assert label("March 31, 2024 March 31, 2023") == "2024-03-31"


def test_fiscal_year_bounds():
    period = find_periods("for FY24")[0]
    assert (period.start, period.end) == (date(2023, 4, 1), date(2024, 3, 31))


def test_quarter_bounds_follow_the_fiscal_year():
    period = find_periods("Q4 FY24")[0]
    assert (period.start, period.end) == (date(2024, 1, 1), date(2024, 3, 31))


def test_containment():
    year = find_periods("FY24")[0]
    quarter = find_periods("Q4 FY24")[0]
    assert year.contains(quarter) and not quarter.contains(year)


@pytest.mark.parametrize("text,expected", [
    ("FY25", "FY2024"), ("Q4 FY24", "Q4 FY2023"),
    ("CY2024", "CY2023"), ("September 2025", "2024-09"),
])
def test_shift_back_for_relative_references(text, expected):
    assert shift_back(find_periods(text)[0]).label == expected
