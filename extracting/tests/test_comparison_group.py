"""Unit tests for ``pipeline.comparison_group.resolve_comparison_group``."""

from __future__ import annotations

from coating_kg.db.models import (
    EvidencePointer,
    FactHyperedge,
    Polarity,
    SourceSectionType,
)
from coating_kg.pipeline.comparison_group import resolve_comparison_group


def _make_fact(
    fact_id: str,
    polarity: str,
    prop: str = "PROP_gloss_20deg",
    region_id: str = "Table 5",
    row: str = "Example E4",
) -> FactHyperedge:
    return FactHyperedge(
        fact_id=fact_id,
        application="APP_automotive_oem_clearcoat",
        property=prop,
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity(polarity),
        evidence_pointer=EvidencePointer(
            doc_id="WO_TEST",
            page=22,
            region_type="TABLE",
            region_id=region_id,
            row=row,
        ),
        ontology_version="v0.1",
        extraction_confidence=0.9,
        human_validated=False,
        doc_id="WO_TEST",
    )


def test_unique_negative_is_bound() -> None:
    pos = _make_fact("F1", "positive")
    neg = _make_fact("F2", "negative", row="Comp Example C1")
    other = _make_fact("F3", "positive", row="Example E5")
    table = [pos, neg, other]

    assert resolve_comparison_group(pos, table) == "F2"


def test_no_negative_returns_none() -> None:
    pos = _make_fact("F1", "positive")
    other = _make_fact("F3", "positive", row="Example E5")
    table = [pos, other]

    assert resolve_comparison_group(pos, table) is None


def test_multiple_negatives_returns_none() -> None:
    pos = _make_fact("F1", "positive")
    neg1 = _make_fact("F2", "negative", row="Comp Example C1")
    neg2 = _make_fact("F3", "negative", row="Comp Example C2")
    table = [pos, neg1, neg2]

    # ambiguous → manual review queue → None
    assert resolve_comparison_group(pos, table) is None


def test_negative_for_different_property_is_ignored() -> None:
    pos = _make_fact("F1", "positive", prop="PROP_gloss_20deg")
    other_neg = _make_fact("F2", "negative", prop="PROP_water_absorption", row="Comp Example C1")
    table = [pos, other_neg]

    # the only negative is for a different property → no binding
    assert resolve_comparison_group(pos, table) is None


def test_negative_input_returns_none() -> None:
    neg = _make_fact("F2", "negative", row="Comp Example C1")
    pos = _make_fact("F1", "positive")
    # function is only ever called for positive facts; if called on a negative
    # it must return None (no comparison_group for baselines).
    assert resolve_comparison_group(neg, [pos, neg]) is None


def test_self_is_excluded_from_negatives_pool() -> None:
    # A fact must never bind to itself as its own baseline.
    f = _make_fact("F1", "positive")
    assert resolve_comparison_group(f, [f]) is None
