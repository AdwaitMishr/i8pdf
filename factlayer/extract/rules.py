"""Assemble grounded facts from a page of text.

Each parser owns one dimension -- period, qualifier, quantity, metric phrase --
and this module joins them into ``Fact`` records whose offsets still index the
page text exactly, so every claim can be shown in its original words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import Fact
from ..ingest.pdf import Cell
from ..ingest.segment import Unit, iter_units
from .layout import cell_at, column_headers_for, label_for
from . import metrics as metric_mod
from .periods import Period, find_periods, shift_back
from .qualifiers import Qualifier, find_qualifiers
from .quantities import Quantity, UnitContext, find_quantities, find_unit_context
from .subjects import normalise_subject

# How far back on the page to look for a table's declared unit, e.g. "(₹ in Million)".
_UNIT_LOOKBACK = 400
_MIN_METRIC_CHARS = 3
# Phrases that name a table column or a period rather than a measure.  A fact
# whose metric is one of these cannot be compared with anything meaningfully.
_METRIC_STOPLIST = {
    "year", "year ended", "period", "period ended", "total", "particular",
    "note", "amount", "figure", "item", "date", "as at", "quarter", "month",
    "balance", "opening", "closing", "sub-total", "grand total",
}
# "... 7.8 percent in the first quarter of FY2025/26": a temporal preposition
# right after the value binds the period to it, even when another period is nearer.
# "as per cent of GDP", "0.6 percent of GDP": the denominator is part of what is
# measured, not part of the value.  Without this, every ratio-to-GDP in a report
# collapses into a single "GDP" metric and starts contradicting itself.
_DENOMINATOR = re.compile(
    r"(?:\bas\s+(?:a\s+)?)?(?:per\s?cent|percent|%|share|proportion)\s+of\s+"
    r"((?:the\s+)?[A-Za-z][\w-]*"
    r"(?:\s+(?!in\b|for\b|from\b|to\b|during\b|as\b|and\b|or\b|at\b|by\b|"
    r"was\b|is\b|declined\b|rose\b|fell\b)[A-Za-z][\w-]*){0,2})", re.IGNORECASE)
# The quantity parser already ate the "per cent", so a trailing denominator
# reaches this module as a bare "of GDP".
_NOUN = (r"((?:the\s+)?[A-Za-z][\w-]*"
         r"(?:\s+(?!in\b|for\b|from\b|to\b|during\b|as\b|and\b|or\b|at\b|by\b|"
         r"was\b|is\b|declined\b|rose\b|fell\b)[A-Za-z][\w-]*){0,2})")
_OF_DENOMINATOR = re.compile(r"\s*of\s+" + _NOUN, re.IGNORECASE)
# How far back a preceding "as per cent of X" may sit and still govern the value.
_DENOMINATOR_LOOKBACK = 90
# Up to two words may stand between the value and the preposition, as in
# "740 million parcels for FY24".
_ATTACHES_PERIOD = re.compile(
    r"^[\s,]*(?:\w+\s+){0,2}(?:in|for|during|of|over|through|to|as\s+(?:of|at|on))\b"
    r"[^.;:]{0,40}$",
    re.IGNORECASE)
# "5.4 per cent in the previous year" is a claim about the year before the one
# the sentence has been discussing, not about that year.
_RELATIVE_BACK = re.compile(
    r"^[\s,]*(?:in|from|over|during)?\s*(?:the\s+)?"
    r"(?:previous|preceding|prior|last)\s+(?:year|fiscal|financial\s+year)"
    r"|^[\s,]*a\s+year\s+(?:ago|earlier)", re.IGNORECASE)
# A qualifier this far from the value, or across this many clause breaks, is
# describing something else in the sentence.
_QUALIFIER_RANGE = 100
_QUALIFIER_COMMAS = 1
# A column header is a label, not a paragraph.
_MAX_HEADER_WORDS = 10
# A parenthetical that carries its own number is an aside about that number:
# in "increased to 4.6 percent (from 3.5 percent FY2024/25 average)" the period
# belongs to the 3.5, and the 4.6 is left with no period at all -- which is the
# honest answer.
_PARENTHETICAL_PENALTY = 4.0


@dataclass
class ExtractionContext:
    doc_id: str
    subject: str
    subject_key: str


def _paren_depth(text: str, index: int) -> int:
    return text.count("(", 0, index) - text.count(")", 0, index)


def _enclosing_paren(text: str, index: int) -> tuple[int, int] | None:
    """Span of the innermost parenthetical containing ``index``, if any."""
    depth, start = 0, None
    for i in range(index - 1, -1, -1):
        if text[i] == ")":
            depth += 1
        elif text[i] == "(":
            if depth == 0:
                start = i
                break
            depth -= 1
    if start is None:
        return None
    depth = 0
    for i in range(index, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            if depth == 0:
                return start, i + 1
            depth -= 1
    return start, len(text)


def _owned_by_other_value(text: str, span: tuple[int, int], q: Quantity,
                          others: list[Quantity]) -> bool:
    """Does ``span`` sit in a parenthetical belonging to a different value?"""
    if _paren_depth(text, span[0]) <= _paren_depth(text, q.start):
        return False
    bounds = _enclosing_paren(text, span[0])
    if bounds is None:
        return False
    return any(bounds[0] <= other.start < bounds[1]
               for other in others if other is not q)


def _nearest(text: str, period_or_qual, quantity: Quantity) -> float:
    """Distance from a span to a quantity, preferring what follows it closely."""
    s, e = period_or_qual.span
    if e <= quantity.start:
        distance = float(quantity.start - e)
    elif s >= quantity.end:
        distance = (s - quantity.end) * 1.1   # a following mention is weaker
    else:
        return 0.0
    if _paren_depth(text, s) != _paren_depth(text, quantity.start):
        distance *= _PARENTHETICAL_PENALTY
    return distance


def _period_for(text: str, periods: list[Period], q: Quantity,
                others: list[Quantity]) -> Period | None:
    """The period this value is stated for.

    Proximity alone mis-attaches "X for FY24 was A against B for FY23", so a
    period introduced by a temporal preposition immediately after the value wins
    -- unless another value sits between them, which means the preposition
    belongs to that other value.
    """
    if not periods:
        return None

    # If every period in the sentence belongs to some other value, this value
    # simply has no stated period.  Saying so beats guessing.
    candidates = [p for p in periods
                  if not _owned_by_other_value(text, p.span, q, others)]
    if not candidates:
        return None

    def attaches(p: Period) -> bool:
        """Is this period bound to the value by a temporal preposition after it?"""
        if p.span[0] < q.end:
            return False
        blocked = any(q.end <= o.start < p.span[0] for o in others if o is not q)
        return not blocked and bool(_ATTACHES_PERIOD.match(text[q.end:p.span[0]]))

    # English binds "for FY23" to the value in front of it, however close a
    # different period happens to sit on the other side.
    bound = [p for p in candidates if attaches(p)]
    chosen = min(bound or candidates, key=lambda p: _nearest(text, p, q))
    if _RELATIVE_BACK.match(text[q.end:q.end + 40]):
        return shift_back(chosen)
    return chosen


def _context_for(text: str, qualifiers: list[Qualifier], q: Quantity,
                 quantities: list[Quantity]) -> dict[str, str]:
    """Nearest qualifier per dimension, so one sentence can carry two bases.

    Ties are broken towards the more specific phrase, so "first advance
    estimates" outranks a bare "estimated" later in the same sentence.
    """
    best: dict[str, tuple[float, str]] = {}
    for qual in qualifiers:
        distance = _nearest(text, qual, q)
        if distance > _QUALIFIER_RANGE:
            continue
        lo, hi = sorted((qual.span[1], q.start))
        if text[lo:hi].count(",") > _QUALIFIER_COMMAS:
            continue
        if _owned_by_other_value(text, qual.span, q, quantities):
            continue
        specificity = 10 * (len(qual.raw.split()) - 1)
        score = distance - specificity
        if qual.dimension not in best or score < best[qual.dimension][0]:
            best[qual.dimension] = (score, qual.value)
    return {dim: value for dim, (_, value) in best.items()}


def metric_key(phrase: str) -> str:
    """Normalised token form used to induce concepts across documents."""
    words = [w.strip(".,;:()[]'\"") for w in phrase.lower().split()]
    words = [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
             for w in words]
    words = [w for w in words if w and w not in metric_mod.INTERNAL]
    return " ".join(words)


def _denominator_for(text: str, q: Quantity) -> tuple[str, tuple[int, int]] | None:
    """The base a percentage is expressed against, if the sentence names one."""
    trailing = _OF_DENOMINATOR.match(text, q.end) or _DENOMINATOR.match(text, q.end)
    if trailing:
        return trailing.group(1).strip(), trailing.span()
    preceding = [m for m in _DENOMINATOR.finditer(text[:q.start])
                 if q.start - m.end() <= _DENOMINATOR_LOOKBACK]
    if preceding:
        best = preceding[-1]
        return best.group(1).strip(), best.span()
    return None


def _content_words(phrase: str) -> list[str]:
    return [w for w in phrase.split() if len(w) >= 3 and w.isalpha()]


def _is_usable(phrase: str) -> bool:
    """Does the phrase name anything at all?"""
    return bool(_content_words(phrase))


def _prefer_label(phrase: str) -> bool:
    """Thin or punctuation-riddled phrases are worth checking the layout for.

    "+ + Tons" and a bare "Tons" both come from a slide where the real caption
    sits on another line, so they are a signal to go looking, not an answer.
    """
    words = phrase.split()
    if any(not any(c.isalnum() for c in w) for w in words):
        return True
    return len(_content_words(phrase)) < 2


def _header_context(cells: list[Cell], value_cell: Cell
                    ) -> tuple[Period | None, dict[str, str]]:
    """Period and basis inherited from the column headers above a table cell."""
    period: Period | None = None
    context: dict[str, str] = {}
    for header in column_headers_for(cells, value_cell):
        if len(header.text.split()) > _MAX_HEADER_WORDS:
            continue
        if period is None:
            found = find_periods(header.text)
            if found:
                period = found[0]
        for qualifier in find_qualifiers(header.text):
            context.setdefault(qualifier.dimension, qualifier.value)
        if period and context:
            break
    return period, context


def _metric_from_label(label_text: str) -> tuple[str, Period | None, dict[str, str]]:
    """Read a caption as a metric, peeling off any period or qualifier it carries."""
    periods = find_periods(label_text)
    qualifiers = find_qualifiers(label_text)
    masked = sorted([p.span for p in periods] + [q.span for q in qualifiers])
    kept, cursor = [], 0
    for s, e in masked:
        kept.append(label_text[cursor:s])
        cursor = e
    kept.append(label_text[cursor:])
    words = " ".join("".join(kept).split()).strip(" -:,.;/")
    words = [w for w in words.split() if not w.strip("()").isdigit()]
    words = metric_mod._clean(words)
    context = {q.dimension: q.value for q in qualifiers}
    return words, (periods[0] if periods else None), context


def _display(q: Quantity) -> str:
    if q.raw:
        return q.raw
    tail = f" {q.display_scale}" if q.display_scale else ""
    return f"{q.display_value:,g}{tail}"


def extract_from_unit(unit: Unit, page_no: int, ctx: ExtractionContext,
                      unit_context: UnitContext, cells: list[Cell] | None = None
                      ) -> list[Fact]:
    text = unit.text
    periods = find_periods(text)
    qualifiers = find_qualifiers(text)
    declared = find_unit_context(text)
    if declared == UnitContext():
        declared = unit_context
    quantities = find_quantities(text, [p.span for p in periods], declared)
    if not quantities:
        return []

    # Every "of GDP" on the line is a denominator, not a metric: mask them all so
    # one value's denominator cannot be read as another value's metric.
    denominators = {q: _denominator_for(text, q) for q in quantities
                    if q.unit == "percent"}
    claimed = ([p.span for p in periods] + [q.span for q in qualifiers]
               + [(q.start, q.end) for q in quantities]
               + [d[1] for d in denominators.values() if d])
    facts: list[Fact] = []
    topic: str | None = None

    for q in quantities:
        denominator = denominators.get(q)
        barriers = [(o.start, o.end) for o in quantities if o is not q]
        phrase = metric_mod.choose(text, q, claimed, topic, barriers)
        metric = phrase.text
        period = _period_for(text, periods, q, quantities)
        context = _context_for(text, qualifiers, q, quantities)
        label = None
        value_cell = (cell_at(cells, unit.start + q.start, unit.start + q.end)
                      if cells else None)

        # A bare number in a table gets its period and basis from the headers
        # standing above its column.  Restricted to table rows: on a page of
        # prose the "cell above" is simply the previous paragraph, and letting
        # that act as a header hands every value its neighbour's context.
        if (value_cell is not None and unit.kind == "row"
                and (period is None or not context)):
            header_period, header_context = _header_context(cells, value_cell)
            period = period or header_period
            context = {**header_context, **context}

        # Slides and tables put the caption in a different cell; if the words
        # around the number named nothing, go and find the caption by geometry.
        # Only rows and tiles hide their caption in another cell; in running
        # prose the words around the number are the metric, however thin.
        if value_cell is not None and unit.kind == "row" and _prefer_label(metric):
            found = label_for(cells, value_cell)
            if found:
                from_label, label_period, label_context = _metric_from_label(found.text)
                if (_is_usable(from_label)
                        and len(_content_words(from_label)) > len(_content_words(metric))):
                    label, metric = found, from_label
                    period = period or label_period
                    context = {**label_context, **context}

        metric = " ".join(w for w in metric.split()
                          if any(c.isalpha() for c in w) and not w.strip("()").isdigit())
        while metric.split() and metric.split()[0].lower() in metric_mod.BRIDGE:
            metric = metric.split(" ", 1)[1] if " " in metric else ""
        if len(metric) < _MIN_METRIC_CHARS or not _is_usable(metric):
            continue
        if metric_key(metric) in _METRIC_STOPLIST:
            continue
        if denominator:
            base = metric[:-7] if metric.endswith(" growth") else metric
            metric = f"{base} as % of {denominator[0]}"
        if topic is None:
            topic = metric
        subject = phrase.subject_hint or ctx.subject
        confidence = round(min(0.99, q.confidence * (1.0 if period else 0.85)
                               * (1.0 if unit.kind == "sentence" else 0.9)), 3)
        facts.append(Fact(
            doc_id=ctx.doc_id, page_no=page_no,
            char_start=unit.start, char_end=unit.end, evidence=text,
            value_start=unit.start + q.start, value_end=unit.start + q.end,
            kind="quantity",
            subject=subject, subject_key=normalise_subject(subject),
            subject_source="sentence" if phrase.subject_hint else "document",
            metric=metric, metric_key=metric_key(metric),
            label_text=label.text if label else None,
            label_start=label.start if label else None,
            label_end=label.end if label else None,
            value=q.value, unit=q.unit, display=_display(q), precision=q.precision,
            period_label=period.label if period else None,
            period_kind=period.kind if period else None,
            period_start=period.start if period else None,
            period_end=period.end if period else None,
            context=context,
            confidence=confidence,
        ))
    return facts


def extract_page(page_text: str, page_no: int, ctx: ExtractionContext,
                 cells: list[Cell] | None = None) -> list[Fact]:
    """Every quantity fact on one page, anchored to that page's character offsets."""
    facts: list[Fact] = []
    for unit in iter_units(page_text):
        inherited = UnitContext()
        if unit.kind == "row":
            # A table declares its unit in a header above the rows, not in them.
            window = page_text[max(0, unit.start - _UNIT_LOOKBACK):unit.start]
            inherited = find_unit_context(window)
        facts.extend(extract_from_unit(unit, page_no, ctx, inherited, cells))
    return facts
