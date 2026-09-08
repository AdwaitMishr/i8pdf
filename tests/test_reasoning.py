"""Context-first comparison: rule out every explanation before calling a conflict."""

from datetime import date

import pytest

from factlayer.knowledge.concepts import ConceptIndex
from factlayer.knowledge.linking import link
from factlayer.knowledge.relations import (CONSISTENT, CONTRADICTS, CORROBORATES,
                                           PART_OF, RECONCILED, DocumentInfo,
                                           classify, values_agree)
from factlayer.extract.periods import find_periods
from factlayer.models import Fact

LEFT = DocumentInfo("a", "Annual report", "c", date(2024, 8, 1))
RIGHT = DocumentInfo("b", "Results deck", "c", date(2024, 5, 17))


def make(doc="a", value=100.0, unit="INR", period="FY2024", precision=1.0,
         metric="revenue from operations", page=1, context=None, start=0):
    bounds = find_periods(period or "")
    return Fact(doc_id=doc, page_no=page, char_start=start, char_end=start + 40,
                evidence="evidence text", value_start=start, value_end=start + 5,
                kind="quantity", subject="Delhivery", subject_key="delhivery",
                subject_source="document", metric=metric,
                metric_key=metric.replace("from ", "").replace("operations", "operation"),
                value=value, unit=unit, display=str(value), precision=precision,
                period_label=period, context=context or {}, confidence=0.9,
                period_start=bounds[0].start if bounds else None,
                period_end=bounds[0].end if bounds else None)


def judge(a, b):
    return classify(a, b, LEFT, RIGHT, "revenue")


def test_agreement_uses_the_coarser_stated_precision():
    """₹8,142 Cr stands for [8,141.5, 8,142.5] crore, which contains 81,415.38 mn."""
    deck = make(value=8.142e10, precision=1e7)
    report = make(doc="b", value=8.141538e10, precision=1e4)
    assert values_agree(deck, report)[0]


def test_adjacent_rounding_buckets_stay_distinct():
    """6.4% and 6.5% are two claims, not one number rounded two ways."""
    a = make(value=6.4, unit="percent", precision=0.1)
    b = make(doc="b", value=6.5, unit="percent", precision=0.1)
    assert not values_agree(a, b)[0]


def test_same_context_same_value_corroborates():
    assert judge(make(), make(doc="b")).relation == CORROBORATES


def test_same_context_different_value_contradicts():
    relation = judge(make(value=100.0), make(doc="b", value=180.0))
    assert relation.relation == CONTRADICTS
    assert "No difference in period" in relation.explanation


def test_consolidation_explains_the_gap():
    relation = judge(make(value=745.4, context={"consolidation": "standalone"}),
                     make(doc="b", value=814.2, context={"consolidation": "consolidated"}))
    assert relation.relation == RECONCILED
    assert "consolidation" in relation.dimensions
    assert "standalone" in relation.explanation and "consolidated" in relation.explanation


def test_period_difference_explains_the_gap():
    relation = judge(make(period="FY2024", value=100.0),
                     make(doc="b", period="FY2023", value=80.0))
    assert relation.relation == RECONCILED and "period" in relation.dimensions


def test_vintage_explains_the_gap():
    relation = judge(make(value=6.4, unit="percent", precision=0.1,
                          context={"vintage": "advance_estimate"}),
                     make(doc="b", value=6.5, unit="percent", precision=0.1))
    assert relation.relation == RECONCILED and "vintage" in relation.dimensions


def test_agreement_across_different_context_is_only_consistency():
    relation = judge(make(context={"consolidation": "standalone"}),
                     make(doc="b", context={"consolidation": "consolidated"}))
    assert relation.relation == CONSISTENT


def test_a_quarter_inside_a_year_is_a_component():
    relation = judge(make(period="FY2024", value=8142.0),
                     make(doc="b", period="Q4 FY2024", value=2076.0))
    assert relation.relation == PART_OF
    assert "does not exceed the total" in relation.explanation


def test_a_component_larger_than_its_total_is_flagged():
    relation = judge(make(period="FY2024", value=100.0),
                     make(doc="b", period="Q4 FY2024", value=500.0))
    assert relation.relation == PART_OF and "cannot survive" in relation.explanation


def test_two_undated_figures_are_not_compared():
    assert judge(make(period=None), make(doc="b", period=None, value=999.0)) is None


def test_different_units_are_not_compared():
    assert judge(make(unit="INR"), make(doc="b", unit="USD")) is None


def test_contradiction_needs_the_same_metric():
    a, b = make(value=1.0), make(doc="b", value=99.0)
    assert classify(a, b, LEFT, RIGHT, "revenue", same_metric=False) is None


def test_two_series_on_one_page_are_not_a_contradiction():
    a = make(value=1.0, page=7, start=0)
    b = make(value=99.0, page=7, start=80)
    assert judge(a, b) is None


def test_sentence_level_subjects_must_agree():
    a = make(value=1.0)
    b = make(doc="b", value=1.0)
    a.subject_source = b.subject_source = "sentence"
    b.subject_key = "someone else"
    docs = {"a": LEFT, "b": RIGHT}
    index = ConceptIndex()
    for fact in (a, b):
        fact.concept_id = index.assign(fact.metric_key, fact.metric)
    assert link([a, b], docs, index).relations == []
