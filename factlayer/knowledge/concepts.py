"""Group metric phrases that name the same measure.

There is no taxonomy of metrics here on purpose -- the documents are supposed to
decide what counts as a fact.  Instead, concepts are induced from the corpus:
rare words carry more weight than common ones (a corpus of financial metrics
makes "revenue" cheap and "tonnage" expensive), and phrases whose weighted token
sets are close enough become one concept.

Assignment is incremental.  A new document's phrases are matched against the
concepts already known and only create a new concept when nothing fits, so
ingesting a seventh PDF does not re-cluster the first six.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field

SIMILARITY_THRESHOLD = 0.62
# A shared head noun is weak evidence on its own: "capital expenditure" and
# "revenue expenditure" share one and are different measures.
_HEAD_BONUS = 0.05
_MIN_IDF_DOCS = 4


@dataclass
class Concept:
    concept_id: str
    label: str                                    # how this concept is named
    canonical_key: str = ""                       # its most-used normalised key
    keys: set[str] = field(default_factory=set)   # every key that joined it
    # Surfaces are counted per key, not pooled: a concept that has absorbed
    # "revenue from operations" and a stray "foreign operations" must be named
    # after the phrase that dominates it, not after whichever surface happens to
    # be most frequent across all of them.
    surfaces: dict[str, Counter] = field(default_factory=dict)

    def observe(self, metric_key: str, surface: str) -> None:
        self.keys.add(metric_key)
        self.surfaces.setdefault(metric_key, Counter())[surface] += 1

    def uses(self, metric_key: str) -> int:
        return sum(self.surfaces.get(metric_key, Counter()).values())

    def relabel(self) -> None:
        if not self.surfaces:
            return
        self.canonical_key = max(self.surfaces, key=self.uses)
        self.label = self.surfaces[self.canonical_key].most_common(1)[0][0]


def _new_id(key: str) -> str:
    return "c" + hashlib.sha1(key.encode()).hexdigest()[:10]


class ConceptIndex:
    """Induces and stores metric concepts, with an editable alias layer."""

    def __init__(self) -> None:
        self.concepts: dict[str, Concept] = {}
        self.of_key: dict[str, str] = {}
        self.document_frequency: Counter = Counter()
        self.n_keys = 0
        self._seen: set[str] = set()
        self._by_token: dict[str, set[str]] = defaultdict(set)   # token -> concept ids

    # -- statistics ------------------------------------------------------
    def observe(self, metric_key: str) -> None:
        """Record a phrase for the inverse-document-frequency statistics."""
        if not metric_key or metric_key in self._seen:
            return
        self._seen.add(metric_key)
        for token in set(metric_key.split()):
            self.document_frequency[token] += 1
        self.n_keys += 1

    def idf(self, token: str) -> float:
        total = max(self.n_keys, _MIN_IDF_DOCS)
        return math.log(total / (1 + self.document_frequency[token])) + 1.0

    # -- similarity ------------------------------------------------------
    def similarity(self, left: str, right: str) -> float:
        """Cosine over IDF-weighted token sets, with a bonus for a shared head."""
        a, b = set(left.split()), set(right.split())
        if not a or not b:
            return 0.0
        shared = a & b
        if not shared:
            return 0.0
        dot = sum(self.idf(t) ** 2 for t in shared)
        norm = math.sqrt(sum(self.idf(t) ** 2 for t in a)
                         * sum(self.idf(t) ** 2 for t in b))
        score = dot / norm if norm else 0.0
        if left.split()[-1] == right.split()[-1]:
            score += _HEAD_BONUS
        return min(score, 1.0)

    # -- assignment ------------------------------------------------------
    def assign(self, metric_key: str, surface: str) -> str:
        """Concept for ``metric_key``, joining an existing one where close enough."""
        if not metric_key:
            metric_key = surface.lower()
        known = self.of_key.get(metric_key)
        if known:
            concept = self.concepts[known]
            concept.observe(metric_key, surface)
            concept.relabel()
            return known

        self.observe(metric_key)
        # Compare against each concept's canonical phrase, not against any member.
        # Matching any member chains "capital expenditure" to "revenue
        # expenditure" through a shared neighbour; matching the centre does not.
        best_id, best_score = None, 0.0
        for concept_id in self._candidates(metric_key):
            score = self.similarity(metric_key, self.concepts[concept_id].canonical_key)
            if score > best_score:
                best_id, best_score = concept_id, score

        if best_id is None or best_score < SIMILARITY_THRESHOLD:
            best_id = _new_id(metric_key)
            self.concepts[best_id] = Concept(best_id, surface, metric_key)
        concept = self.concepts[best_id]
        concept.observe(metric_key, surface)
        concept.relabel()
        self.of_key[metric_key] = best_id
        for token in set(concept.canonical_key.split()):
            self._by_token[token].add(best_id)
        return best_id

    def _candidates(self, metric_key: str) -> list[str]:
        """Only concepts sharing a token can clear the threshold.

        Returned sorted: set iteration order varies with Python's per-process
        string hash seed, and without an order two equally close concepts would
        win on different runs, making the whole layer irreproducible.
        """
        out: set[str] = set()
        for token in set(metric_key.split()):
            out |= self._by_token[token]
        return sorted(out)

    # -- aliases ---------------------------------------------------------
    def merge(self, left_key: str, right_key: str) -> str | None:
        """Fuse two concepts that evidence showed to be the same measure.

        Used by the learned-alias path: when two differently worded metrics keep
        resolving to the same grounded value in the same context, the schema
        should learn that they are one concept rather than two.
        """
        a, b = self.of_key.get(left_key), self.of_key.get(right_key)
        if not a or not b or a == b:
            return a or b
        keeper, absorbed = self.concepts[a], self.concepts.pop(b)
        keeper.keys |= absorbed.keys
        for key, counter in absorbed.surfaces.items():
            keeper.surfaces.setdefault(key, Counter()).update(counter)
        keeper.relabel()
        for key in absorbed.keys:
            self.of_key[key] = keeper.concept_id
        for token in set(keeper.canonical_key.split()):
            self._by_token[token].add(keeper.concept_id)
        for bucket in self._by_token.values():
            bucket.discard(b)
        return keeper.concept_id
