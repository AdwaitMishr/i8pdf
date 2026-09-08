"""The optional Claude pass: the model proposes, these rules verify."""

import json

import pytest

from factlayer.extract.quantities import UnitContext
from factlayer.extract.rules import ExtractionContext, extract_from_unit
from factlayer.ingest.segment import Unit
from factlayer.llm import AnthropicRefiner, build_refiner, enabled

# A sentence the rules read poorly: the noun the growth belongs to sits in the
# clause before, so the rule extractor is left holding a bare "growth".
SENTENCE = ("Remittances from Indians working overseas, posted a y-o-y growth of "
            "16.2 per cent during H1 of FY25.")


class Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class Response:
    stop_reason = "end_turn"

    def __init__(self, payload):
        self.content = [Block(json.dumps(payload))]


class StubClient:
    """Stands in for anthropic.Anthropic; records what it was asked."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return Response(self.payload)


def a_fact():
    facts = extract_from_unit(Unit(SENTENCE, 0, len(SENTENCE), "sentence"), 1,
                              ExtractionContext("doc", "India", "india"), UnitContext())
    return facts[0]


def refine(payload, fact=None, page=None):
    fact = fact or a_fact()
    client = StubClient({"items": [{**payload, "id": fact.fact_id}]})
    refiner = AnthropicRefiner(client=client)
    facts = refiner([fact], {1: page if page is not None else SENTENCE})
    return refiner, facts


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("FACTLAYER_LLM", raising=False)
    assert not enabled() and build_refiner() is None


def test_a_grounded_proposal_is_applied():
    refiner, facts = refine({
        "metric": "remittances growth", "subject": "India",
        "period": "H1 FY25",
        "context": [{"dimension": "vintage", "value": "actual"}],
        "evidence_quote": "posted a y-o-y growth of 16.2 per cent",
        "usable": True,
    })
    fact = facts[0]
    assert fact.metric == "remittances growth"
    assert fact.metric_key == "remittance growth"
    assert fact.period_label == "H1 FY2025"      # re-parsed, not trusted verbatim
    assert fact.context["vintage"] == "actual"
    assert fact.subject_source == "sentence"
    assert fact.extractor == "rules+llm"
    assert refiner.stats.applied == 1


def test_an_ungrounded_quote_is_refused():
    """A sentence the page does not contain cannot become evidence."""
    refiner, facts = refine({
        "metric": "completely invented measure", "subject": "India",
        "period": "FY25", "context": [],
        "evidence_quote": "remittances collapsed to zero", "usable": True,
    })
    assert facts[0].metric != "completely invented measure"
    assert facts[0].extractor == "rules"
    assert refiner.stats.rejected_ungrounded == 1


def test_grounding_ignores_whitespace_differences():
    refiner, _ = refine({
        "metric": "remittances growth", "subject": "India", "period": "H1 FY25",
        "context": [], "usable": True,
        "evidence_quote": "posted a  y-o-y growth\nof 16.2 per cent",
    })
    assert refiner.stats.applied == 1


def test_unknown_context_dimensions_are_dropped():
    _, facts = refine({
        "metric": "remittances growth", "subject": "India", "period": "H1 FY25",
        "context": [{"dimension": "vibes", "value": "positive"}],
        "evidence_quote": "posted a y-o-y growth of 16.2 per cent",
        "usable": True,
    })
    assert "vibes" not in facts[0].context


def test_the_value_is_never_rewritten():
    fact = a_fact()
    before = (fact.value, fact.unit, fact.display, fact.value_start)
    _, facts = refine({
        "metric": "remittances growth", "subject": "India", "period": "H1 FY25",
        "context": [], "evidence_quote": "posted a y-o-y growth of 16.2 per cent",
        "usable": True,
    }, fact=fact)
    assert (facts[0].value, facts[0].unit, facts[0].display, facts[0].value_start) == before


def test_a_fact_marked_unusable_is_dropped():
    refiner, facts = refine({
        "metric": "", "subject": "", "period": "", "context": [],
        "evidence_quote": "posted a y-o-y growth", "usable": False,
    })
    assert facts == [] and refiner.stats.dropped_unusable == 1


def test_a_refusal_leaves_the_facts_untouched():
    fact = a_fact()

    class Refusing(StubClient):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            response.stop_reason = "refusal"
            return response

    refiner = AnthropicRefiner(client=Refusing({"items": []}))
    assert refiner([fact], {1: SENTENCE}) == [fact]
    assert refiner.stats.applied == 0


def test_only_uncertain_facts_are_sent():
    strong = a_fact()
    strong.confidence = 0.99
    strong.metric_key = "revenue from operation"
    strong.period_label = "FY2024"
    assert AnthropicRefiner(client=StubClient({"items": []}))._candidates([strong]) == []


def test_the_request_uses_a_json_schema_and_the_configured_model():
    fact = a_fact()
    client = StubClient({"items": []})
    AnthropicRefiner(client=client, model="claude-opus-5")([fact], {1: SENTENCE})
    call = client.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "value_as_written" in call["messages"][0]["content"]
