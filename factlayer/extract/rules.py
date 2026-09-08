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
from .layout import cell_at, label_for
from . import metrics as metric_mod
from .periods import Period, find_periods
from .qualifiers import Qualifier, find_qualifiers
from .quantities import Quantity, UnitContext, find_quantities, find_unit_context
from .subjects import normalise_subject

# How far back on the page to look for a table's declared unit, e.g. "(₹ in Million)".
_UNIT_LOOKBACK = 400
_MIN_METRIC_CHARS = 3
# "... 7.8 percent in the first quarter of FY2025/26": a temporal preposition
# right after the value binds the period to it, even when another period is nearer.
_ATTACHES_PERIOD = re.compile(
    r"^[\s,]*(?:in|for|during|of|over|through|to|as\s+(?:of|at|on))\b[^.;:]{0,60}$",
    re.IGNORECASE)


@dataclass
class ExtractionContext:
    doc_id: str
    subject: str
    subject_key: str


def _nearest(period_or_qual, quantity: Quantity):
    """Distance from a span to a quantity, preferring what follows it closely."""
    s, e = period_or_qual.span
    if e <= quantity.start:
        return quantity.start - e
    if s >= quantity.end:
        return (s - quantity.end) * 1.1     # a following mention is slightly weaker
    return 0


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

    def score(p: Period) -> float:
        distance = _nearest(p, q)
        if p.span[0] >= q.end:
            between = text[q.end:p.span[0]]
            blocked = any(q.end <= o.start < p.span[0] for o in others if o is not q)
            if not blocked and _ATTACHES_PERIOD.match(between):
                return distance * 0.4
        return distance

    return min(periods, key=score)


def _context_for(qualifiers: list[Qualifier], q: Quantity) -> dict[str, str]:
    """Nearest qualifier per dimension, so one sentence can carry two bases.

    Ties are broken towards the more specific phrase, so "first advance
    estimates" outranks a bare "estimated" later in the same sentence.
    """
    best: dict[str, tuple[float, str]] = {}
    for qual in qualifiers:
        specificity = 10 * (len(qual.raw.split()) - 1)
        score = _nearest(qual, q) - specificity
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

    claimed = ([p.span for p in periods] + [q.span for q in qualifiers]
               + [(q.start, q.end) for q in quantities])
    facts: list[Fact] = []
    topic: str | None = None

    for q in quantities:
        phrase = metric_mod.choose(text, q, claimed, topic)
        metric = phrase.text
        period = _period_for(text, periods, q, quantities)
        context = _context_for(qualifiers, q)
        label = None

        # Slides and tables put the caption in a different cell; if the words
        # around the number named nothing, go and find the caption by geometry.
        if cells and _prefer_label(metric):
            value_cell = cell_at(cells, unit.start + q.start, unit.start + q.end)
            found = label_for(cells, value_cell) if value_cell else None
            if found:
                from_label, label_period, label_context = _metric_from_label(found.text)
                if (_is_usable(from_label)
                        and len(_content_words(from_label)) > len(_content_words(metric))):
                    label, metric = found, from_label
                    period = period or label_period
                    context = {**label_context, **context}

        metric = " ".join(w for w in metric.split() if any(c.isalnum() for c in w))
        if len(metric) < _MIN_METRIC_CHARS or not _is_usable(metric):
            continue
        if topic is None:
            topic = metric
        subject = phrase.subject_hint or ctx.subject
        confidence = round(min(0.99, q.confidence * (1.0 if period else 0.85)
                               * (1.0 if unit.kind == "sentence" else 0.9)), 3)
        facts.append(Fact(
            doc_id=ctx.doc_id, page_no=page_no,
            char_start=unit.start, char_end=unit.end, evidence=text,
            kind="quantity",
            subject=subject, subject_key=normalise_subject(subject),
            metric=metric, metric_key=metric_key(metric),
            label_text=label.text if label else None,
            label_start=label.start if label else None,
            label_end=label.end if label else None,
            value=q.value, unit=q.unit, display=_display(q),
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
