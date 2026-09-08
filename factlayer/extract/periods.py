"""Recognise the time window a statement is about.

Period is the single most common reason two true numbers disagree, so it is
modelled explicitly rather than left inside the metric string.  Indian fiscal
years are canonicalised by the calendar year they *end* in, which is what makes
"FY24" (Delhivery), "2023-24" (RBI) and "FY2023/24" (IMF) resolve to one label.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))

# Fiscal years in these documents start in April and are named by their end year.
FISCAL_START_MONTH = 4


@dataclass(frozen=True)
class Period:
    label: str                 # canonical: "FY2024", "Q4 FY2024", "CY2024", "2024-03-31"
    kind: str                  # fy | quarter | cy | month | asof | range
    start: date | None
    end: date | None
    raw: str
    span: tuple[int, int]

    def overlaps(self, other: "Period") -> bool:
        if not (self.start and self.end and other.start and other.end):
            return False
        return self.start <= other.end and other.start <= self.end

    def contains(self, other: "Period") -> bool:
        if not (self.start and self.end and other.start and other.end):
            return False
        return self.start <= other.start and other.end <= self.end


def _fy_bounds(end_year: int) -> tuple[date, date]:
    return date(end_year - 1, FISCAL_START_MONTH, 1), date(end_year, FISCAL_START_MONTH - 1, 31)


def _fy_end_year(first: str, second: str | None) -> int:
    """Resolve the calendar year an Indian fiscal year ends in.

    ``FY24`` -> 2024.  ``2023-24`` and ``FY2023/24`` -> 2024 (the later year).
    ``FY2024`` on its own -> 2024.
    """
    a = int(first)
    if a < 100:
        a += 2000 if a < 70 else 1900
    if second is None:
        return a
    b = int(second)
    if b < 100:                       # "2023-24" / "24-25"
        b += (a // 100) * 100
        if b < a:
            b += 100
    return b


def _quarter_bounds(q: int, fy_end: int) -> tuple[date, date]:
    """Q1 of FY2024 is Apr-Jun 2023; Q4 is Jan-Mar 2024."""
    start_month = FISCAL_START_MONTH + 3 * (q - 1)
    year = fy_end - 1 + (start_month - 1) // 12
    start_month = (start_month - 1) % 12 + 1
    end_month = start_month + 2
    end_year = year + (end_month - 1) // 12
    end_month = (end_month - 1) % 12 + 1
    last = [31, 29 if end_year % 4 == 0 else 28, 31, 30, 31, 30,
            31, 31, 30, 31, 30, 31][end_month - 1]
    return date(year, start_month, 1), date(end_year, end_month, last)


_PATTERNS: list[tuple[str, str]] = [
    # H1 FY25 / first half of FY25 -- a very common source of apparent conflict
    # with the full-year figure for the same fiscal year.
    ("fh", r"\b(?:H([12])\s?(?:of\s+)?FY\s?(\d{2,4})|(first|second)\s+half\s+of\s+FY\s?(\d{2,4}))\b"),
    # Q4 FY24 / Q4FY2024 / fourth quarter of FY24
    ("fq", r"\bQ([1-4])\s?(?:of\s+)?FY\s?(\d{2,4})(?:\s?[-/]\s?(\d{2,4}))?\b"),
    # 2025Q2 / 2025:Q2
    ("cq", r"\b(\d{4})\s?[:\s]?Q([1-4])\b"),
    # Q2 2025
    ("cq2", r"\bQ([1-4])\s+(\d{4})\b"),
    # FY24, FY 2024, FY2023-24, FY2024/25
    ("fy", r"\bFY\s?(\d{2,4})(?:\s?[-/]\s?(\d{2,4}))?\b"),
    # fiscal 2024 / financial year 2023-24 / fiscal year ended 2024
    ("fy2", r"\b(?:fiscal|financial)\s+(?:year\s+)?(?:ended\s+)?(\d{4})(?:\s?[-/]\s?(\d{2,4}))?\b"),
    # A bare "March 31, 2024" -- matched before the month pattern, which would
    # otherwise read the day as a two-digit year and land in 2031.
    ("date", rf"\b({_MONTH_RE})[a-z]*\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b"),
    # year ended / as at / as of March 31, 2024
    ("asof", rf"\b(?:as\s+(?:at|of|on)|year\s+ended|period\s+ended|ended)\s+"
             rf"(?:the\s+)?(\d{{1,2}})?\s*({_MONTH_RE})[a-z]*\.?,?\s*(\d{{1,2}})?,?\s*(\d{{4}})\b"),
    # a bare Indian fiscal year range: 2024-25, 2023-24
    ("fyr", r"(?<![\d./-])(\d{4})\s?-\s?(\d{2})(?![\d-])"),
    # calendar year 2024 / CY2024
    ("cy", r"\b(?:CY|calendar\s+year)\s?(\d{4})\b"),
    # March 2025 / Mar '24
    ("month", rf"\b({_MONTH_RE})[a-z]*\.?\s*'?(\d{{2,4}})\b"),
]
_COMPILED = [(kind, re.compile(rx, re.IGNORECASE)) for kind, rx in _PATTERNS]


def _month_end(year: int, month: int) -> date:
    last = [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30,
            31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, last)


def _build(kind: str, m: re.Match) -> Period | None:
    raw, span = m.group(0), m.span()
    if kind == "fh":
        half = int(m.group(1)) if m.group(1) else (1 if m.group(3).lower() == "first" else 2)
        fy = _fy_end_year(m.group(2) or m.group(4), None)
        if not 1990 <= fy <= 2100:
            return None
        first, _ = _quarter_bounds(1 if half == 1 else 3, fy)
        _, last = _quarter_bounds(2 if half == 1 else 4, fy)
        return Period(f"H{half} FY{fy}", "half", first, last, raw, span)
    if kind == "fq":
        q, fy = int(m.group(1)), _fy_end_year(m.group(2), m.group(3))
        s, e = _quarter_bounds(q, fy)
        return Period(f"Q{q} FY{fy}", "quarter", s, e, raw, span)
    if kind in ("cq", "cq2"):
        year, q = (int(m.group(1)), int(m.group(2))) if kind == "cq" else (int(m.group(2)), int(m.group(1)))
        start = date(year, 3 * (q - 1) + 1, 1)
        end = _month_end(year, 3 * q)
        return Period(f"{year}Q{q}", "quarter", start, end, raw, span)
    if kind in ("fy", "fy2"):
        fy = _fy_end_year(m.group(1), m.group(2))
        if not 1990 <= fy <= 2100:
            return None
        s, e = _fy_bounds(fy)
        return Period(f"FY{fy}", "fy", s, e, raw, span)
    if kind == "fyr":
        fy = _fy_end_year(m.group(1), m.group(2))
        if not 1990 <= fy <= 2100:
            return None
        s, e = _fy_bounds(fy)
        return Period(f"FY{fy}", "fy", s, e, raw, span)
    if kind == "cy":
        year = int(m.group(1))
        return Period(f"CY{year}", "cy", date(year, 1, 1), date(year, 12, 31), raw, span)
    if kind == "date":
        month = _MONTHS[m.group(1).lower()]
        day, year = int(m.group(2)), int(m.group(3))
        if not (1 <= day <= 31 and 1990 <= year <= 2100):
            return None
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        return Period(d.isoformat(), "asof", d, d, raw, span)
    if kind == "asof":
        day = m.group(1) or m.group(3)
        month = _MONTHS[m.group(2).lower()[:4].rstrip(".")] if m.group(2).lower()[:4].rstrip(".") in _MONTHS \
            else _MONTHS[m.group(2).lower()]
        year = int(m.group(4))
        d = date(year, month, int(day)) if day else _month_end(year, month)
        return Period(d.isoformat(), "asof", d, d, raw, span)
    if kind == "month":
        month = _MONTHS[m.group(1).lower()]
        year = int(m.group(2))
        year += 2000 if year < 70 else 1900 if year < 100 else 0
        if not 1990 <= year <= 2100:
            return None
        return Period(f"{year}-{month:02d}", "month",
                      date(year, month, 1), _month_end(year, month), raw, span)
    return None


def find_periods(text: str) -> list[Period]:
    """All period mentions in ``text``, longest-match-wins, left to right."""
    found: list[Period] = []
    taken: list[tuple[int, int]] = []
    for kind, rx in _COMPILED:
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            period = _build(kind, m)
            if period is None:
                continue
            found.append(period)
            taken.append((s, e))
    return sorted(found, key=lambda p: p.span)


def shift_back(period: Period, years: int = 1) -> Period:
    """The same window one year earlier, for "in the previous year" references."""
    def back(value: date | None) -> date | None:
        if value is None:
            return None
        try:
            return value.replace(year=value.year - years)
        except ValueError:                      # 29 February
            return value.replace(year=value.year - years, day=28)

    label = period.label
    if period.kind in ("fy", "quarter", "half") and "FY" in label:
        head, _, year = label.rpartition("FY")
        label = f"{head}FY{int(year) - years}"
    elif period.kind == "cy":
        label = f"CY{int(label[2:]) - years}"
    elif period.kind == "quarter" and label[:4].isdigit():
        label = f"{int(label[:4]) - years}{label[4:]}"
    elif period.kind == "month":
        year, _, month = label.partition("-")
        label = f"{int(year) - years}-{month}"
    elif period.kind == "asof":
        shifted = back(period.start)
        label = shifted.isoformat() if shifted else label
    return Period(label, period.kind, back(period.start), back(period.end),
                  period.raw, period.span)
