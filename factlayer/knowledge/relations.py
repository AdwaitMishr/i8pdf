"""Decide how two facts relate, and say why.

The governing idea: two figures for the same measure are only in conflict once
you have ruled out every context that would make them both true.  So the
comparison is always context-first -- period, consolidation basis, vintage, real
versus nominal -- and only figures that survive all of it are allowed to
contradict each other.

Agreement is judged against the precision each document actually states, not a
fixed tolerance: "₹8,142 Cr" claims the nearest crore, "₹81,415.38 million" the
nearest ten thousand, and the question is whether those two intervals overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import Fact, Relation

CORROBORATES = "corroborates"
CONTRADICTS = "contradicts"
RECONCILED = "reconciled_by_context"
CONSISTENT = "consistent_across_context"
PART_OF = "part_of"

# Floor on relative agreement, to absorb representation noise.
_RELATIVE_FLOOR = 0.001
# Vintage only explains a gap when at least one side is not a settled number.
_UNSETTLED = {"projection", "estimate", "advance_estimate", "provisional",
              "revised", "target"}
_MONTHS_MATTER = 3

_READABLE = {
    "consolidation": {"standalone": "a standalone basis", "consolidated": "a consolidated basis"},
    "price_basis": {"real": "real terms", "nominal": "nominal terms"},
    "vintage": {
        "advance_estimate": "an advance estimate", "provisional": "a provisional figure",
        "revised": "a revised estimate", "projection": "a projection",
        "estimate": "an estimate", "target": "a target", "actual": "an actual outturn",
    },
    "adjustment": {"adjusted": "an adjusted figure", "restated": "a restated figure",
                   "pro_forma": "a pro forma figure", "excluding": "an exclusive basis",
                   "including": "an inclusive basis"},
    "aggregation": {"average": "an average", "median": "a median",
                    "annualised": "an annualised figure", "per_capita": "a per-capita figure",
                    "cumulative": "a cumulative total",
                    "seasonally_adjusted": "a seasonally adjusted figure"},
    "comparison": {"yoy": "a year-on-year comparison", "qoq": "a sequential comparison",
                   "cagr": "a compound annual rate"},
}


@dataclass(frozen=True)
class Difference:
    dimension: str
    left: str | None
    right: str | None

    def describe(self, left_doc: str, right_doc: str) -> str:
        table = _READABLE.get(self.dimension, {})
        left = table.get(self.left or "", self.left or "no stated basis")
        right = table.get(self.right or "", self.right or "no stated basis")
        # Both figures often come from one document; naming it twice reads as a
        # mistake, so say which statement is which instead.
        first, second = ((left_doc, right_doc) if left_doc != right_doc
                         else ("the first statement", "the second"))
        verb = "covers" if self.dimension == "period" else "reports"
        if self.dimension == "period":
            left, right = self.left, self.right
        return f"{first} {verb} {left} while {second} {verb} {right}"


@dataclass(frozen=True)
class DocumentInfo:
    doc_id: str
    label: str
    collection: str
    published_on: date | None = None


def _period_relation(a: Fact, b: Fact) -> str:
    if a.period_label and a.period_label == b.period_label:
        return "same"
    if not a.period_label or not b.period_label:
        return "unknown"
    if not (a.period_start and a.period_end and b.period_start and b.period_end):
        return "different"
    if a.period_start <= b.period_start and b.period_end <= a.period_end:
        return "contains"
    if b.period_start <= a.period_start and a.period_end <= b.period_end:
        return "within"
    if a.period_start <= b.period_end and b.period_start <= a.period_end:
        return "overlaps"
    return "disjoint"


def values_agree(a: Fact, b: Fact) -> tuple[bool, float, float]:
    """Does the coarser figure's rounding interval contain the finer one?

    "₹8,142 Cr" claims the nearest crore, so it stands for anything in
    [8,141.5, 8,142.5] crore -- and ₹81,415.38 million falls inside that. Two
    figures both written to one decimal, 6.4% and 6.5%, do not: the tolerance
    comes from the *coarser* of the two, not the sum, so adjacent rounding
    buckets stay distinct claims.
    """
    gap = abs((a.value or 0.0) - (b.value or 0.0))
    magnitude = max(abs(a.value or 0.0), abs(b.value or 0.0), 1e-12)
    tolerance = max(0.5 * max(a.precision, b.precision), _RELATIVE_FLOOR * magnitude)
    return gap <= tolerance, gap, tolerance


def _months_between(a: date, b: date) -> int:
    return abs((a.year - b.year) * 12 + (a.month - b.month))


def context_differences(a: Fact, b: Fact, left: DocumentInfo,
                        right: DocumentInfo) -> list[Difference]:
    """Every recorded reason the two figures might legitimately differ."""
    out: list[Difference] = []
    period = _period_relation(a, b)
    if period not in ("same", "contains", "within"):
        out.append(Difference("period", a.period_label or "an unstated period",
                              b.period_label or "an unstated period"))
    for dimension in sorted(set(a.context) | set(b.context)):
        left_value, right_value = a.context.get(dimension), b.context.get(dimension)
        if left_value != right_value:
            out.append(Difference(dimension, left_value, right_value))

    # Two forecasts made months apart are different claims about the future, not
    # a disagreement about the past.
    unsettled = {a.context.get("vintage"), b.context.get("vintage")} & _UNSETTLED
    if (unsettled and left.published_on and right.published_on
            and _months_between(left.published_on, right.published_on) >= _MONTHS_MATTER
            and not any(d.dimension == "vintage" for d in out)):
        out.append(Difference("publication", left.published_on.isoformat(),
                              right.published_on.isoformat()))
    return out


def _format_gap(a: Fact, b: Fact, gap: float) -> str:
    magnitude = max(abs(a.value or 0.0), abs(b.value or 0.0), 1e-12)
    share = 100.0 * gap / magnitude
    if a.unit in ("percent", "percentage_point"):
        return f"{gap:.2f} percentage points"
    return f"{share:.2f}%"


def _cite(fact: Fact, doc: DocumentInfo) -> str:
    return f"{doc.label} p.{fact.page_no}"


def classify(a: Fact, b: Fact, left: DocumentInfo, right: DocumentInfo,
             concept_label: str, same_metric: bool = True) -> Relation | None:
    """Judge one pair of facts, or return None when they are not comparable.

    ``same_metric`` says whether the two phrases really name the same measure,
    not merely one that clustered nearby.  Asserting a contradiction is the most
    consequential thing this function does, so it is withheld unless that holds
    and the two figures come from different pages -- two numbers under one label
    on a single chart are separate series, not a disagreement.
    """
    if a.unit != b.unit or a.value is None or b.value is None:
        return None
    if a.fact_id == b.fact_id:
        return None
    # Two undated figures are not rival claims about anything in particular.
    if not a.period_label and not b.period_label:
        return None

    agree, gap, tolerance = values_agree(a, b)
    differences = context_differences(a, b, left, right)
    period = _period_relation(a, b)
    subject = a.subject if a.subject_key == b.subject_key else f"{a.subject}/{b.subject}"
    la, lb = _cite(a, left), _cite(b, right)

    # A quarter inside a year is a component, not a rival claim.
    if period in ("contains", "within") and not any(
            d.dimension not in ("period", "publication") for d in differences):
        whole, part = (a, b) if period == "contains" else (b, a)
        whole_cite, part_cite = (la, lb) if period == "contains" else (lb, la)
        consistent = abs(part.value) <= abs(whole.value) * (1 + _RELATIVE_FLOOR)
        note = ("The component does not exceed the total, as expected."
                if consistent else
                "The component exceeds the total it sits inside, which one of the "
                "two figures cannot survive.")
        return Relation(
            left_id=a.fact_id, right_id=b.fact_id,
            relation=PART_OF,
            confidence=round(min(a.confidence, b.confidence) * (0.9 if consistent else 0.7), 3),
            explanation=(
                f"{part.period_label} sits inside {whole.period_label}, so "
                f"{part.display} ({part_cite}) is a component of {whole.display} "
                f"({whole_cite}) for {concept_label} of {subject}. {note}"),
            dimensions=["period"],
        )

    if not differences:
        if agree:
            return Relation(
                left_id=a.fact_id, right_id=b.fact_id,
                relation=CORROBORATES,
                confidence=round(min(a.confidence, b.confidence), 3),
                explanation=(
                    f"{la} and {lb} both report {concept_label} for {subject}"
                    f"{_period_clause(a)}: {a.display} and {b.display}. They differ by "
                    f"{_format_gap(a, b, gap)}, inside the tolerance implied by the "
                    f"precision each document states."),
            )
        if not same_metric or (a.doc_id == b.doc_id and a.page_no == b.page_no):
            return None
        return Relation(
            left_id=a.fact_id, right_id=b.fact_id,
            relation=CONTRADICTS,
            confidence=round(min(a.confidence, b.confidence)
                             * (0.9 if period == "same" else 0.6), 3),
            explanation=(
                f"{concept_label} for {subject}{_period_clause(a)} is reported as "
                f"{a.display} by {la} and {b.display} by {lb}. No difference in "
                f"period, basis, vintage or adjustment was found between the two "
                f"statements, and the gap of {_format_gap(a, b, gap)} is larger than "
                f"their stated precision allows."),
        )

    reasons = "; ".join(d.describe(left.label, right.label) for d in differences)
    dimensions = [d.dimension for d in differences]
    if agree:
        return Relation(
            left_id=a.fact_id, right_id=b.fact_id,
            relation=CONSISTENT,
            confidence=round(min(a.confidence, b.confidence) * 0.8, 3),
            explanation=(
                f"{a.display} ({la}) and {b.display} ({lb}) agree on {concept_label} "
                f"for {subject} even though they are stated differently: {reasons}."),
            dimensions=dimensions,
        )
    return Relation(
        left_id=a.fact_id, right_id=b.fact_id,
        relation=RECONCILED,
        confidence=round(min(a.confidence, b.confidence) * 0.85, 3),
        explanation=(
            f"{a.display} ({la}) and {b.display} ({lb}) both report {concept_label} "
            f"for {subject} and differ by {_format_gap(a, b, gap)}, but they are not "
            f"in conflict: {reasons}."),
        dimensions=dimensions,
    )


def _period_clause(fact: Fact) -> str:
    return f" in {fact.period_label}" if fact.period_label else ""
