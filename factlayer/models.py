"""The records the whole system passes around.

A fact is only useful here if it can be *compared*, so every field exists to
answer one of: what is measured, of what, when, on what basis, how much -- and
where exactly in which document that claim appears.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date


@dataclass
class SourceDocument:
    doc_id: str
    filename: str
    title: str
    sha256: str
    n_pages: int
    collection: str = "default"
    subject: str | None = None
    subject_key: str | None = None
    published_on: date | None = None


@dataclass
class Fact:
    """One grounded claim, normalised enough to compare with another."""

    doc_id: str
    page_no: int
    char_start: int                  # evidence span: the whole sentence or row
    char_end: int
    evidence: str                    # verbatim text of that span
    value_start: int                 # the number's own span, inside the evidence
    value_end: int

    kind: str                        # "quantity" | "state"
    subject: str
    subject_key: str
    # "sentence" when the text named the entity ("India's real GDP"), "document"
    # when it fell back to the document-level guess.  Only sentence-level
    # subjects are trusted enough to block a comparison.
    subject_source: str
    metric: str                      # surface phrase, as written
    metric_key: str                  # normalised tokens, for concept induction

    value: float | None = None       # normalised into ``unit``
    unit: str = "unknown"
    display: str = ""                # value as written, e.g. "₹ 74,540.82 million"
    precision: float = 0.0           # size of the last reported digit, in ``unit``

    period_label: str | None = None
    period_kind: str | None = None
    period_start: date | None = None
    period_end: date | None = None

    # When the layout separated the number from its caption, the caption's own
    # span on the same page.  Kept separate so evidence is never spliced.
    label_text: str | None = None
    label_start: int | None = None
    label_end: int | None = None

    context: dict[str, str] = field(default_factory=dict)   # qualifier dimensions
    state: str | None = None                                # for kind == "state"
    effective_on: date | None = None

    confidence: float = 0.5
    extractor: str = "rules"
    concept_id: str | None = None    # assigned by concept induction
    fact_id: str = ""

    def __post_init__(self) -> None:
        if not self.fact_id:
            self.fact_id = self.make_id(
                self.doc_id, self.page_no, self.value_start, self.value_end, self.metric)

    @staticmethod
    def make_id(doc_id: str, page_no: int, start: int, end: int, metric: str) -> str:
        # Keyed on the *value's* span, not the sentence's: one sentence often
        # states several figures for the same metric, and they are distinct facts.
        raw = f"{doc_id}|{page_no}|{start}|{end}|{metric}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    @property
    def is_comparable(self) -> bool:
        return self.value is not None and self.unit not in ("unknown", "count")


@dataclass
class Relation:
    """A judgement about two facts, with the reasoning that produced it."""

    left_id: str
    right_id: str
    relation: str          # corroborates | contradicts | reconciled_by_context |
                           # consistent_across_context | part_of | superseded_by
    confidence: float
    explanation: str
    dimensions: list[str] = field(default_factory=list)   # what differs
    basis: str = "concept"                                # how the pair was found
    relation_id: str = ""

    def __post_init__(self) -> None:
        if not self.relation_id:
            pair = "|".join(sorted((self.left_id, self.right_id)))
            self.relation_id = hashlib.sha1(
                f"{pair}|{self.relation}|{self.basis}".encode()).hexdigest()[:16]
