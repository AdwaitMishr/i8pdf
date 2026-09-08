"""Find the fact pairs worth comparing, then have them judged.

Two candidate generators, because there are two ways documents talk about the
same measure:

* **concept blocks** -- same induced concept, same unit.  This catches wording
  that is close enough to cluster ("headline inflation" / "headline inflation
  rate").
* **the value bridge** -- different concepts, but the same subject, unit and
  period, and grounded values that agree to within their stated precision.
  This is what connects "revenue from services ₹8,142 Cr" in a results deck to
  "revenue from operations ₹81,415.38 million" in the annual report: nothing in
  the wording says they are the same measure, but the numbers and the context
  do.  A bridge that keeps recurring is promoted to a learned alias, which folds
  the two concepts together for every document ingested afterwards.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from ..models import Fact, Relation
from .concepts import ConceptIndex
from .relations import CORROBORATES, DocumentInfo, classify, values_agree

# Guard against a pathological concept swamping the run.
MAX_BLOCK = 220
# Tokens too generic to justify a value bridge on their own.
_GENERIC = {"total", "net", "gross", "value", "amount", "number", "share", "rate",
            "growth", "income", "year", "change", "level"}
# Suffixes that turn a measure into a derived one without changing what it is of.
_DERIVED = {"growth", "rate", "level", "change", "averaged", "average"}
# How many independent bridges before two wordings are treated as one concept.
ALIAS_EVIDENCE = 2
# A bridge rests on wording alone being uninformative, so both phrases must read
# like metric names.  A long one is a mis-extracted clause, not a metric.
MAX_BRIDGE_TOKENS = 6
# How alike two phrases must be before their disagreement counts as a conflict.
CONTRADICTION_SIMILARITY = 0.85
_BRIDGE_SIGNIFICANT = 3


@dataclass
class LinkResult:
    relations: list[Relation]
    aliases: list[tuple[str, str]]     # metric key pairs promoted to aliases


def _blocks(facts: list[Fact], docs: dict[str, DocumentInfo]
            ) -> dict[tuple, list[Fact]]:
    blocks: dict[tuple, list[Fact]] = defaultdict(list)
    for fact in facts:
        if fact.value is None or fact.concept_id is None:
            continue
        collection = docs[fact.doc_id].collection
        blocks[(collection, fact.concept_id, fact.unit)].append(fact)
    return blocks


def _bridge_key(fact: Fact, docs: dict[str, DocumentInfo]) -> tuple | None:
    """Bucket facts that could be the same measure under different names."""
    if fact.value is None or fact.unit in ("count", "unknown") or not fact.period_label:
        return None
    magnitude = abs(fact.value)
    if magnitude == 0:
        return None
    # Three significant figures, so "₹8,142 Cr" and "₹81,415.38 million" land in
    # the same bucket; the precision test then decides whether they really agree.
    exponent = len(f"{int(magnitude)}") if magnitude >= 1 else 0
    bucket = round(fact.value, _BRIDGE_SIGNIFICANT - exponent)
    subject = fact.subject_key if fact.subject_source == "sentence" else ""
    return (docs[fact.doc_id].collection, subject, fact.unit,
            fact.period_label, bucket)


def _same_subject(a: Fact, b: Fact) -> bool:
    """Are these two facts about the same thing?

    Document-level subject detection is unreliable on excerpted filings that
    have no cover page, so it is not allowed to veto a comparison -- the
    collection a document was ingested into carries that job.  A subject the
    sentence itself named is trusted, and two of those must agree.
    """
    if a.subject_source == "sentence" and b.subject_source == "sentence":
        return a.subject_key == b.subject_key
    return True


def _head(tokens: list[str]) -> str:
    trimmed = [t for t in tokens if t not in _DERIVED] or tokens
    return trimmed[-1]


def _bridgeable(a: Fact, b: Fact) -> bool:
    """Could these two phrases plausibly name the same measure?

    They must share something specific.  But sharing the *head* noun while each
    carries a modifier the other lacks is the opposite signal: "headline
    inflation" and "core inflation" are contrasting members of one family, and
    the fact that both happen to read 4.6% in FY25 is a coincidence, not an
    identity.  That shape is refused.
    """
    left, right = a.metric_key.split(), b.metric_key.split()
    if not left or not right:
        return False
    if not set(left) & set(right) - _GENERIC:
        return False
    # "headline inflation growth" and "headline inflation" have the same head
    # once the derivative noun is set aside.
    if _head(left) == _head(right):
        only_left = (set(left) - set(right)) - _GENERIC
        only_right = (set(right) - set(left)) - _GENERIC
        if only_left and only_right:
            return False
    return True


def link(facts: list[Fact], docs: dict[str, DocumentInfo], concepts: ConceptIndex,
         only_new: set[str] | None = None) -> LinkResult:
    """Compare every candidate pair.

    ``only_new`` restricts output to pairs touching those fact ids, which is how
    an incremental ingest avoids re-deriving relations it already stored.
    """
    relations: list[Relation] = []
    seen: set[str] = set()

    def keep(relation: Relation | None) -> None:
        if relation is None or relation.relation_id in seen:
            return
        if only_new and not (relation.left_id in only_new or relation.right_id in only_new):
            return
        seen.add(relation.relation_id)
        relations.append(relation)

    for (_, concept_id, _), block in _blocks(facts, docs).items():
        if len(block) > MAX_BLOCK:
            block = sorted(block, key=lambda f: -f.confidence)[:MAX_BLOCK]
        label = concepts.concepts[concept_id].label if concept_id in concepts.concepts \
            else block[0].metric
        for i, left in enumerate(block):
            for right in block[i + 1:]:
                if not _same_subject(left, right):
                    continue
                same_metric = (left.metric_key == right.metric_key
                               or concepts.similarity(left.metric_key, right.metric_key)
                               >= CONTRADICTION_SIMILARITY)
                keep(classify(left, right, docs[left.doc_id], docs[right.doc_id],
                              label, same_metric))

    # The value bridge.
    buckets: dict[tuple, list[Fact]] = defaultdict(list)
    for fact in facts:
        key = _bridge_key(fact, docs)
        if key:
            buckets[key].append(fact)

    alias_votes: dict[tuple[str, str], int] = defaultdict(int)
    for bucket in buckets.values():
        for i, left in enumerate(bucket):
            for right in bucket[i + 1:]:
                if left.concept_id == right.concept_id or left.doc_id == right.doc_id:
                    continue
                if not _same_subject(left, right):
                    continue
                if not _bridgeable(left, right):
                    continue
                if max(len(left.metric_key.split()),
                       len(right.metric_key.split())) > MAX_BRIDGE_TOKENS:
                    continue
                agree, gap, tolerance = values_agree(left, right)
                if not agree:
                    continue
                left_doc, right_doc = docs[left.doc_id], docs[right.doc_id]
                keep(Relation(
                    left_id=left.fact_id, right_id=right.fact_id,
                    relation=CORROBORATES,
                    confidence=round(min(left.confidence, right.confidence) * 0.9, 3),
                    explanation=(
                        f"{left_doc.label} p.{left.page_no} reports \"{left.metric}\" as "
                        f"{left.display} and {right_doc.label} p.{right.page_no} reports "
                        f"\"{right.metric}\" as {right.display}. The wording differs, but "
                        f"both are stated for {left.subject} in {left.period_label} in the "
                        f"same unit and agree to within the precision each states, so they "
                        f"are the same measure reported two ways."),
                    dimensions=["metric wording"],
                    basis="value_bridge",
                ))
                alias_votes[tuple(sorted((left.metric_key, right.metric_key)))] += 1

    aliases = [pair for pair, votes in alias_votes.items() if votes >= ALIAS_EVIDENCE]
    return LinkResult(relations, aliases)
