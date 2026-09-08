"""Parse numeric mentions into comparable quantities.

Two figures can only be compared once they are on the same scale and in the same
unit, so every mention is normalised to a base unit (rupees, percent, tonnes,
count) while keeping the surface form for display and evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCALES = {
    "thousand": 1e3, "k": 1e3, "'000": 1e3,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "million": 1e6, "mn": 1e6, "mm": 1e6,
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "billion": 1e9, "bn": 1e9,
    "trillion": 1e12, "tn": 1e12, "trn": 1e12,
}
CURRENCIES = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "us$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP",
}
# Unit names the base value is expressed in.
INR, PERCENT, PP, TONNE, COUNT, RATIO, DAY = (
    "INR", "percent", "percentage_point", "tonne", "count", "ratio", "day")

# Word boundaries matter: without them "customeRS" supplies a rupee symbol.
_SYMBOL = r"₹|\bRs\.?(?![A-Za-z])|\bINR\b|US\$|\bUSD\b|\$|€|£|\bGBP\b|\bEUR\b"
# Grouped form first (Indian or Western grouping), then a plain run of digits.
_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_SCALE_WORDS = "|".join(sorted((re.escape(k) for k in SCALES), key=len, reverse=True))

# Suffix patterns are applied with ``.match(text, pos)``, which anchors at pos,
# so none of them carry a "^".
_NUMBER = re.compile(
    rf"(?<![\w.,])(?P<pre>{_SYMBOL})?\s*(?P<open>\()?\s*(?P<sym>{_SYMBOL})?"
    rf"\s*(?P<num>{_NUM})(?![\d,])",
    re.IGNORECASE)
_CLOSE = re.compile(r"\s*\)")
_PERCENT = re.compile(r"\s*(?:%|per\s?cent(?:age)?\b(?!\s+point)|pc\b)", re.IGNORECASE)
_PP = re.compile(r"\s*(?:percentage\s+points?|pps?\b|ppt\b)", re.IGNORECASE)
_BPS = re.compile(r"\s*(?:bps\b|basis\s+points?)", re.IGNORECASE)
_SCALE = re.compile(rf"\s*(?P<scale>{_SCALE_WORDS})\b\.?", re.IGNORECASE)
_TRAIL_SYMBOL = re.compile(rf"\s*(?P<sym>{_SYMBOL})", re.IGNORECASE)
_MASS = re.compile(r"\s*(?:tonnes?|tons?|MT)\b", re.IGNORECASE)
_DAYS = re.compile(r"\s*days?\b", re.IGNORECASE)
_RATIO = re.compile(r"\s*(?:x\b|times\b)", re.IGNORECASE)

# Numbers that follow these words are identifiers or cross-references, not data.
_REFERENCE_LEAD = re.compile(
    r"(?:DIN|CIN|ISIN|PAN|LEI|GSTIN|No|Nos|Note|Page|Table|Figure|Chart|Box|Annexure|"
    r"Annex|Appendix|Section|Sub-section|Clause|Regulation|Rule|Para|Paragraph|Chapter|"
    r"Item|Schedule|Act|Order|Circular|Part)\b[\s.:\-]*$",
    re.IGNORECASE)
# Declared unit of a table or chart, e.g. "(₹ in Million)", "₹ Cr", "(per cent)".
_UNIT_CONTEXT = re.compile(
    rf"\(?\s*(?:(?P<sym>{_SYMBOL})\s*(?:in\s+)?(?:(?P<scale>{_SCALE_WORDS})\b)?|"
    rf"(?:in\s+)?(?P<scale2>{_SCALE_WORDS})\b\s*(?:of\s+)?(?P<sym2>{_SYMBOL}))\s*\)?",
    re.IGNORECASE)
_PERCENT_CONTEXT = re.compile(r"\(\s*(?:per\s?cent|%)\s*\)", re.IGNORECASE)


@dataclass(frozen=True)
class Quantity:
    raw: str            # exact source text of the mention
    start: int
    end: int
    value: float        # normalised into ``unit``
    unit: str
    display_value: float   # magnitude as written
    display_scale: str     # scale word as written ("million", "Cr", "")
    currency: str | None
    confidence: float


@dataclass(frozen=True)
class UnitContext:
    """Unit declared for a table or chart, applied to otherwise bare numbers."""
    currency: str | None = None
    scale: float = 1.0
    scale_word: str = ""
    percent: bool = False


def find_unit_context(text: str) -> UnitContext:
    """Read a declared unit such as ``(₹ in Million)`` or ``₹ Cr`` from ``text``."""
    if _PERCENT_CONTEXT.search(text):
        return UnitContext(percent=True)
    m = _UNIT_CONTEXT.search(text)
    if not m:
        return UnitContext()
    sym = m.group("sym") or m.group("sym2")
    word = m.group("scale") or m.group("scale2")
    if not sym and not word:
        return UnitContext()
    return UnitContext(
        currency=CURRENCIES.get((sym or "").lower().rstrip(".")) if sym else None,
        scale=SCALES.get((word or "").lower(), 1.0),
        scale_word=word or "",
    )


def _looks_like_year(num: str, value: float) -> bool:
    return "." not in num and "," not in num and 1900 <= value <= 2100 and len(num) == 4


def find_quantities(text: str, skip: list[tuple[int, int]] | None = None,
                    context: UnitContext | None = None) -> list[Quantity]:
    """Extract every usable numeric mention from ``text``.

    ``skip`` masks spans already consumed by another parser (period mentions),
    and ``context`` supplies the unit a table declared in its header.
    """
    skip = skip or []
    context = context or UnitContext()
    out: list[Quantity] = []

    for m in _NUMBER.finditer(text):
        s, e = m.span("num")
        if any(s < te and ts < e for ts, te in skip):
            continue
        if _REFERENCE_LEAD.search(text[max(0, m.start() - 24):m.start()]):
            continue

        num = m.group("num")
        try:
            magnitude = float(num.replace(",", ""))
        except ValueError:
            continue

        pos = e
        opened = bool(m.group("open"))
        sym = m.group("sym") or m.group("pre")
        closed = False

        def eat(rx: re.Pattern) -> re.Match | None:
            nonlocal pos
            hit = rx.match(text, pos)
            if hit:
                pos = hit.end()
            return hit

        # "₹(452) Cr" closes before the scale word; "(6.3%)" closes after the unit.
        if opened and eat(_CLOSE):
            closed = True

        unit, scale, scale_word, currency, confidence = COUNT, 1.0, "", None, 0.55

        scale_hit = eat(_SCALE)
        if scale_hit:
            scale_word = scale_hit.group("scale")
            scale = SCALES[scale_word.lower()]
            confidence = 0.8
        if not sym:
            sym_hit = eat(_TRAIL_SYMBOL)
            if sym_hit:
                sym = sym_hit.group("sym")

        if eat(_PP):
            unit, scale, scale_word, confidence = PP, 1.0, "", 0.95
        elif eat(_BPS):
            unit, scale, scale_word, confidence = PERCENT, 0.01, "", 0.95
        elif eat(_PERCENT):
            unit, scale, scale_word, confidence = PERCENT, 1.0, "", 0.95
        elif sym:
            currency = CURRENCIES.get(sym.lower().rstrip("."))
            unit, confidence = currency or COUNT, 0.95
        elif eat(_MASS):
            unit, confidence = TONNE, 0.9
        elif eat(_DAYS):
            unit, confidence = DAY, 0.85
        elif eat(_RATIO):
            unit, confidence = RATIO, 0.85
        elif context.percent:
            unit, confidence = PERCENT, 0.7
        elif context.currency:
            currency = context.currency
            unit = currency
            if scale == 1.0:
                scale, scale_word = context.scale, context.scale_word
            confidence = 0.7
        elif _looks_like_year(num, magnitude):
            continue

        if opened and not closed and eat(_CLOSE):
            closed = True

        # A bare small number carries no comparable meaning on its own.
        if unit == COUNT and scale == 1.0 and magnitude < 100:
            continue

        # Accounting convention: a value wrapped in parentheses is negative.
        signed = -magnitude if (opened and closed) else magnitude
        raw_start = m.start() if (opened or sym) else s
        out.append(Quantity(
            raw=text[raw_start:pos].strip(),
            start=raw_start, end=pos,
            value=signed * scale, unit=unit,
            display_value=signed, display_scale=scale_word,
            currency=currency, confidence=confidence,
        ))
    return out
