"""Fact assembly: the metric, the context and the evidence must line up."""

import pytest

from factlayer.extract.metrics import choose
from factlayer.extract.periods import find_periods
from factlayer.extract.qualifiers import as_context, find_qualifiers
from factlayer.extract.quantities import UnitContext, find_quantities
from factlayer.extract.rules import ExtractionContext, extract_from_unit
from factlayer.ingest.segment import Unit

CONTEXT = ExtractionContext("doc", "India", "india")


def facts_for(text):
    return extract_from_unit(Unit(text, 0, len(text), "sentence"), 1, CONTEXT, UnitContext())


def test_metric_phrase_survives_qualifiers_and_periods():
    text = ("y The revenue from operations on standalone basis for FY24 stood at "
            "₹ 74,540.82 million as against ₹66,586.61 million for FY23.")
    facts = facts_for(text)
    assert [f.metric for f in facts] == ["revenue from operations"] * 2
    assert [f.period_label for f in facts] == ["FY2024", "FY2023"]
    assert all(f.context["consolidation"] == "standalone" for f in facts)


def test_period_binds_to_its_own_value_not_the_nearest_one():
    """'X for FY24 was A against B for FY23' must not give both FY24."""
    text = "Revenue for FY24 stood at ₹100 crore as against ₹80 crore for FY23."
    assert [f.period_label for f in facts_for(text)] == ["FY2024", "FY2023"]


def test_relative_reference_shifts_the_period_back():
    text = ("Headline inflation moderated to an average of 4.6 per cent during "
            "2024-25 from 5.4 per cent in the previous year.")
    assert [f.period_label for f in facts_for(text)] == ["FY2025", "FY2024"]


def test_parenthetical_period_stays_with_its_own_value():
    text = "Core inflation increased to 4.6 percent (from 3.5 percent FY2024/25 average)."
    facts = facts_for(text)
    assert facts[0].period_label is None          # the 4.6 has no stated period
    assert facts[1].period_label == "FY2025"


def test_growth_verb_versus_level_verb():
    assert facts_for("India's real GDP grew by 6.5 percent in FY2024/25.")[0].metric \
        == "GDP growth"
    assert facts_for("The CA deficit declined to 0.6 percent of GDP in FY2024/25.")[0].metric \
        == "CA deficit as % of GDP"


def test_bare_derivative_noun_reaches_its_subject():
    text = "Overall, merchandise exports registered a modest growth of 1.6 per cent."
    assert facts_for(text)[0].metric == "merchandise exports growth"


def test_possessive_becomes_the_subject_not_the_metric():
    fact = facts_for("India's real GDP grew by 6.5 percent in FY2024/25.")[0]
    assert fact.subject == "India" and fact.subject_source == "sentence"
    assert "india" not in fact.metric.lower()


def test_real_and_nominal_are_context_not_metric():
    real = facts_for("India's real GDP grew by 6.5 percent in FY2024/25.")[0]
    nominal = facts_for("Nominal GDP growth moderated to 8.8 percent in FY2024/25.")[0]
    assert real.metric_key == nominal.metric_key
    assert real.context["price_basis"] == "real"
    assert nominal.context["price_basis"] == "nominal"


def test_more_specific_qualifier_wins():
    text = ("As per the first advance estimates, India's real GDP is estimated to "
            "grow by 6.4 per cent in FY25.")
    assert facts_for(text)[0].context["vintage"] == "advance_estimate"


def test_qualifier_does_not_reach_across_clauses():
    text = ("Headline inflation moderated to an average of 4.6 per cent during "
            "2024-25, largely driven by a moderation in core (CPI excluding food "
            "and fuel) inflation to 3.5 per cent.")
    assert "adjustment" not in facts_for(text)[0].context


def test_evidence_offsets_point_at_the_value():
    text = "Revenue for FY24 stood at ₹100 crore."
    fact = facts_for(text)[0]
    assert text[fact.value_start:fact.value_end] == "₹100 crore"
    assert text[fact.char_start:fact.char_end] == fact.evidence


def test_facts_in_one_sentence_get_distinct_identities():
    text = "Revenue for FY24 stood at ₹100 crore as against ₹80 crore for FY23."
    facts = facts_for(text)
    assert len({f.fact_id for f in facts}) == len(facts)


def test_boilerplate_metrics_are_discarded():
    assert facts_for("Total 1,234 5,678") == []
