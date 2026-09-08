"""Optional Claude pass over the facts the rules were least sure about.

The whole system runs without this. It exists because the rule extractor's
weakest step is naming the metric: a syntactic walk recovers "revenue from
operations" reliably and "net inflows" only sometimes, because the noun the
sentence is really about can sit in an earlier clause.

The division of labour is deliberate -- **the model proposes, the rules
verify**:

* the model only ever rewrites the *description* of a fact (metric, subject,
  period, context), never its value;
* every returned quote must appear verbatim in the page text, or the whole
  refinement for that fact is dropped, so a hallucinated sentence cannot become
  evidence;
* the period string is re-parsed by this project's own parser, so canonical
  labels stay consistent with everything extracted by rules;
* context dimensions are checked against the qualifier lexicon and unknown ones
  are discarded.

Enable with ``FACTLAYER_LLM=anthropic`` and credentials in the environment.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from ..extract.periods import find_periods
from ..extract.qualifiers import dimensions as lexicon_dimensions
from ..extract.rules import metric_key
from ..extract.subjects import normalise_subject
from ..ingest.text import collapse_ws
from ..models import Fact

DEFAULT_MODEL = "claude-opus-5"
BATCH_SIZE = 25
# Only facts the rules were unsure about are worth spending a token on.
CONFIDENCE_CEILING = 0.85

SYSTEM = """You repair the metadata of facts extracted from financial and \
statistical PDFs. For each candidate you are given the exact sentence or table \
row it came from and the value that was parsed out of it.

For each candidate return:
- metric: the measure the value quantifies, as a short noun phrase (2-6 words), \
using the document's own wording. Exclude the value, the unit, the time period \
and any basis such as "consolidated" or "seasonally adjusted".
- subject: the entity the measure is about, or "" if the sentence does not name one.
- period: the time period the value is stated for, copied verbatim from the \
sentence (for example "FY24", "Q4 FY24", "2024-25", "March 31, 2024"), or "" if \
the sentence does not state one.
- context: basis qualifiers the sentence attaches to this value, as \
dimension/value pairs drawn only from these dimensions: {dimensions}.
- evidence_quote: a contiguous span copied character for character from the \
sentence you were given, containing the value and the words that name the metric.
- usable: false if the text is a fragment, a page header, a chart axis or is \
otherwise too broken to yield a meaningful fact.

Never invent a figure and never quote text you were not given."""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "metric": {"type": "string"},
                    "subject": {"type": "string"},
                    "period": {"type": "string"},
                    "context": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"dimension": {"type": "string"},
                                           "value": {"type": "string"}},
                            "required": ["dimension", "value"],
                            "additionalProperties": False,
                        },
                    },
                    "evidence_quote": {"type": "string"},
                    "usable": {"type": "boolean"},
                },
                "required": ["id", "metric", "subject", "period", "context",
                             "evidence_quote", "usable"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def enabled() -> bool:
    return os.environ.get("FACTLAYER_LLM", "").lower() == "anthropic"


@dataclass
class RefinementStats:
    considered: int = 0
    proposed: int = 0
    applied: int = 0
    rejected_ungrounded: int = 0
    dropped_unusable: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class AnthropicRefiner:
    """Callable that fits the ``refiner`` hook in :func:`factlayer.pipeline.ingest`."""

    def __init__(self, client=None, model: str = DEFAULT_MODEL,
                 max_facts: int = 200, batch_size: int = BATCH_SIZE) -> None:
        self.model = model
        self.max_facts = max_facts
        self.batch_size = batch_size
        self.stats = RefinementStats()
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import anthropic          # imported lazily; not a hard dependency
            self._client = anthropic.Anthropic()
        return self._client

    def __call__(self, facts: list[Fact], pages: dict[int, str]) -> list[Fact]:
        candidates = self._candidates(facts)
        self.stats.considered = len(candidates)
        if not candidates:
            return facts

        by_id = {f.fact_id: f for f in facts}
        drop: set[str] = set()
        for start in range(0, len(candidates), self.batch_size):
            batch = candidates[start:start + self.batch_size]
            for item in self._ask(batch):
                fact = by_id.get(item.get("id", ""))
                if fact is None:
                    continue
                self.stats.proposed += 1
                if not item.get("usable", True):
                    drop.add(fact.fact_id)
                    self.stats.dropped_unusable += 1
                    continue
                if not self._grounded(item.get("evidence_quote", ""),
                                      pages.get(fact.page_no, "")):
                    self.stats.rejected_ungrounded += 1
                    continue
                self._apply(fact, item)
                self.stats.applied += 1
        return [f for f in facts if f.fact_id not in drop]

    # -- selection -------------------------------------------------------
    def _candidates(self, facts: list[Fact]) -> list[Fact]:
        """The facts whose *description* the rules are least sure of."""
        weak = [f for f in facts
                if f.confidence < CONFIDENCE_CEILING
                or len(f.metric_key.split()) < 2
                or f.period_label is None]
        weak.sort(key=lambda f: (len(f.metric_key.split()), f.confidence))
        return weak[:self.max_facts]

    # -- model call ------------------------------------------------------
    def _ask(self, batch: list[Fact]) -> list[dict]:
        payload = [{
            "id": f.fact_id,
            "value_as_written": f.display,
            "unit": f.unit,
            "text": f.evidence,
            "rule_guess_metric": f.metric,
        } for f in batch]
        system = SYSTEM.format(dimensions=", ".join(lexicon_dimensions()))
        response = self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA},
                           "effort": "low"},
        )
        # Safety classifiers can decline; content is not meaningful then.
        if getattr(response, "stop_reason", None) == "refusal":
            return []
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text).get("items", [])
        except (json.JSONDecodeError, AttributeError):
            return []

    # -- verification ----------------------------------------------------
    @staticmethod
    def _grounded(quote: str, page_text: str) -> bool:
        """Reject anything the page does not literally say."""
        quote = collapse_ws(quote)
        return bool(quote) and quote in collapse_ws(page_text)

    @staticmethod
    def _apply(fact: Fact, item: dict) -> None:
        metric = " ".join(str(item.get("metric", "")).split())
        if metric:
            fact.metric = metric
            fact.metric_key = metric_key(metric)
        subject = " ".join(str(item.get("subject", "")).split())
        if subject:
            fact.subject = subject
            fact.subject_key = normalise_subject(subject)
            fact.subject_source = "sentence"
        # Re-parse the period with this project's parser so labels stay canonical.
        periods = find_periods(str(item.get("period", "")))
        if periods:
            period = periods[0]
            fact.period_label, fact.period_kind = period.label, period.kind
            fact.period_start, fact.period_end = period.start, period.end
        allowed = set(lexicon_dimensions())
        for pair in item.get("context") or []:
            dimension, value = pair.get("dimension"), pair.get("value")
            if dimension in allowed and value:
                fact.context[dimension] = value
        fact.extractor = "rules+llm"


def build_refiner(client=None) -> AnthropicRefiner | None:
    """A refiner when the environment asks for one, otherwise None."""
    if client is None and not enabled():
        return None
    return AnthropicRefiner(client=client,
                            model=os.environ.get("FACTLAYER_LLM_MODEL", DEFAULT_MODEL))
