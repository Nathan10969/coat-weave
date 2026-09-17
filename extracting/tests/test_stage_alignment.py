from __future__ import annotations

import json
from pathlib import Path

from coating_kg.db.models import (
    EvidencePointer,
    FactHyperedge,
    FigureTableUnit,
    Polarity,
    SourceSectionType,
)
from coating_kg.pipeline.context_inheritance import apply_context_inheritance
from coating_kg.pipeline.canonical_merge_queue import build_merge_queue
from coating_kg.pipeline.doc_profile import extract_doc_profile
from coating_kg.pipeline.fact_extractor import (
    FactExtractor,
    _normalise_fact_for_schema,
    is_stage7_validation_publishable,
    validate_stage7_result,
)
from coating_kg.pipeline.kg_export import build_kg_export
from coating_kg.pipeline.patent_metadata_extractor import _load_front_matter_text, find_content_list
from coating_kg.pipeline.structure_resolver import resolve_structures_for_doc
from coating_kg.pipeline.unit_router import (
    classify_unit_route,
    normalize_table_subject,
    write_route_decision,
)


def test_find_content_list_supports_v2_uuid_prefix(tmp_path: Path) -> None:
    path = tmp_path / "DOC1" / "auto" / "abc123_content_list_v2.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")

    assert find_content_list(tmp_path, "DOC1") == path


def test_front_matter_starts_at_first_text_page(tmp_path: Path) -> None:
    content = [
        {"type": "text", "page_idx": 0, "text": "  "},
        {"type": "text", "page_idx": 1, "text": "Title: COATING COMPOSITION"},
        {"type": "text", "page_idx": 2, "text": "IPC C09D 5/16"},
    ]
    path = tmp_path / "content_list.json"
    path.write_text(json.dumps(content), encoding="utf-8")

    text = _load_front_matter_text(path)
    assert "COATING COMPOSITION" in text
    assert "IPC C09D" in text


def test_doc_profile_rules_mark_adjacent_foam_not_coating() -> None:
    layout = {
        "data": [
            {
                "type": "text",
                "text": "Rigid polyisocyanurate foam sandwich element for insulation.",
                "page_idx": 0,
            }
        ]
    }
    profile = extract_doc_profile(
        layout,
        "DOC_FOAM",
        {"title": "RIGID POLYISOCYANURATE FOAM", "abstract": "sandwich element"},
        api_key="",
    )
    assert profile["coating_relevance"] == "adjacent"


def test_unit_router_normalizes_english_and_legacy_subjects(tmp_path: Path) -> None:
    assert normalize_table_subject("performance comparison") == "performance_comparison"
    assert normalize_table_subject("structure/reaction") == "structure_or_scheme"

    unit = FigureTableUnit(unit_id="U_DOC_p1_b2", unit_type="table", doc_id="DOC", page=1)
    decision = classify_unit_route(
        unit,
        {
            "description": "Table reports Surface (film) ratings 1-6.",
            "identified_entities": [],
            "table_subject": "performance comparison",
        },
    )
    assert decision.route == "extract_facts"
    assert decision.normalized_subject == "performance_comparison"

    out = write_route_decision(
        tmp_path, unit.unit_id, "DOC", decision, {"table_subject": "performance comparison"}
    )
    assert json.loads(out.read_text(encoding="utf-8"))["normalized_subject"] == "performance_comparison"


def test_context_inheritance_fills_only_unknown_fields() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Wind turbine blade rain erosion test on coated metal panel.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="unknown",
        property="PROP_ret_breakthrough_time",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )
    profile = {
        "application_family": {
            "label": "wind turbine blade coating",
            "canonical_id": "APP_wind_blade_coating",
            "evidence": "wind turbine blade",
        },
        "substrate_scope": {"label": "metal", "canonical_id": "SUB_metal", "evidence": "metal panel"},
    }

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile=profile)

    assert fact.application == "APP_wind_blade_coating"
    assert fact.substrate is None
    assert fact.context_provenance is not None
    assert fact.extraction_confidence <= 0.88


def test_context_inheritance_accepts_process_context_assertion() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Coating composition for a wind turbine blade.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="unknown",
        property="PROP_ret_breakthrough_time",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
        process=[],
    )
    apply_context_inheritance(
        [fact],
        unit=unit,
        matched_paragraphs=[],
        doc_profile={
            "cure_mechanism": {
                "label": "isocyanate crosslinking",
                "canonical_id": "PROC_isocyanate_crosslinking",
                "evidence": "isocyanate crosslinking",
            }
        },
        context_assertions=[
            {
                "assertion_id": "CTX_PROC",
                "field": "process",
                "value": "PROC_isocyanate_crosslinking",
                "source_ledger_ids": ["LEDGER_U_DOC_p1_b1_020"],
                "confidence": 0.87,
            }
        ],
    )

    assert fact.process == [{"canonical_id": "PROC_isocyanate_crosslinking", "source": "context_assertions"}]
    assert fact.context_provenance is not None
    assert fact.context_provenance["process"]["source"] == "context_assertions"


def test_context_inheritance_fills_missing_resin_from_doc_profile() -> None:
    unit = FigureTableUnit(unit_id="U_DOC_p1_b1", unit_type="table", doc_id="DOC", page=1)
    profile = {
        "doc_id": "DOC",
        "binder_family": {
            "canonical_id": "MAT_acrylic_resin",
            "label": "acrylic binder",
            "confidence": 0.82,
            "evidence": "The coating composition uses an acrylic binder.",
        },
    }
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_cross_cut_adhesion",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile=profile)

    assert fact.resin_system == "MAT_acrylic_resin"
    assert fact.extraction_confidence == 0.72
    assert fact.context_provenance is not None
    assert fact.context_provenance["resin_system"]["source_type"] == "doc_profile"


def test_context_inheritance_does_not_fill_resin_on_formulation_quantity_fact() -> None:
    unit = FigureTableUnit(unit_id="U_DOC_p1_b1", unit_type="table", doc_id="DOC", page=1)
    profile = {
        "doc_id": "DOC",
        "binder_family": {
            "canonical_id": "MAT_acrylic_resin",
            "label": "acrylic binder",
            "confidence": 0.82,
            "evidence": "The coating composition uses an acrylic binder.",
        },
    }
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_formulation_component_mass",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile=profile)

    assert fact.resin_system is None
    assert fact.context_provenance is None


def test_context_inheritance_keeps_resin_on_weight_loss_performance_fact() -> None:
    unit = FigureTableUnit(unit_id="U_DOC_p1_b1", unit_type="table", doc_id="DOC", page=1)
    profile = {
        "doc_id": "DOC",
        "binder_family": {
            "canonical_id": "MAT_acrylic_resin",
            "label": "acrylic binder",
            "confidence": 0.82,
            "evidence": "The coating composition uses an acrylic binder.",
        },
    }
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_weight_loss",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile=profile)

    assert fact.resin_system == "MAT_acrylic_resin"


def test_context_inheritance_merges_missing_test_condition_keys() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Adhesion measured according to ISO 2409.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_cross_cut_adhesion",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
        test_condition={"film_thickness": "50 um"},
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile={})

    assert fact.test_condition == {"film_thickness": "50 um", "standard_id": "ISO 2409"}
    assert fact.context_provenance is not None
    assert fact.context_provenance["test_condition"]["source_type"] == "unit_context"


def test_context_inheritance_does_not_apply_incompatible_standard() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Adhesion measured according to ISO 2409.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile={})

    assert fact.test_condition is None


def test_context_inheritance_extracts_generic_test_standard() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Corrosion resistance was evaluated by ASTM B117 salt spray.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_scribe_creep",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile={})

    assert fact.test_condition == {"standard_id": "ASTM B117"}


def test_context_inheritance_allows_din_55654_for_adhesion_fact() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Adhesion was measured per DIN 55654.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_cross_cut_adhesion",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile={})

    assert fact.test_condition == {"standard_id": "DIN 55654"}


def test_context_inheritance_fills_missing_example_id_from_caption() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Neutralization ladder Example 12.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(
            doc_id="DOC",
            page=1,
            region_type="TABLE",
            region_id="Table 1",
            row="Step 1",
            column="Gloss",
        ),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile={})

    assert fact.example_id == "E12"
    assert fact.context_provenance is not None
    assert fact.context_provenance["example_id"]["source_type"] == "unit_context"


def test_context_inheritance_does_not_overwrite_explicit_example_id() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Example 12.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
        example_id="I1",
    )

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile={})

    assert fact.example_id == "I1"


def test_context_inheritance_fills_example_id_from_vlm_sample_headers() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Performance table.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(
            doc_id="DOC",
            page=1,
            region_type="TABLE",
            region_id="Table 1",
            row="Gloss",
            column="Col 2",
        ),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance(
        [fact],
        unit=unit,
        matched_paragraphs=[],
        doc_profile={},
        vlm_description_json={
            "description": "Examples table.",
            "sample_headers": ["Example 24", "Example 25"],
        },
    )

    assert fact.example_id == "E25"


def test_context_inheritance_fills_example_id_from_table_ocr_sample_headers() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        caption_footnote_text="Performance table.",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(
            doc_id="DOC",
            page=1,
            region_type="TABLE",
            region_id="Table 1",
            row="Gloss",
            column="Col 2",
        ),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance(
        [fact],
        unit=unit,
        matched_paragraphs=[],
        doc_profile={},
        vlm_description_json={"description": "Examples table."},
        table_ocr={"metadata": {"sample_headers": ["Example 24", "Example 25"]}},
    )

    assert fact.example_id == "E25"


def test_context_assertions_fill_missing_fields_with_ledger_provenance() -> None:
    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        region_id="Table 1",
    )
    fact = FactHyperedge(
        fact_id="F1",
        application="unknown",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.unknown,
        evidence_pointer=EvidencePointer(
            doc_id="DOC",
            page=1,
            region_type="TABLE",
            region_id="Table 1",
            row="Example 1",
            column="Gloss",
            cell="92",
        ),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance(
        [fact],
        unit=unit,
        matched_paragraphs=[],
        doc_profile={
            "substrate_scope": {
                "canonical_id": "SUB_broad_claimed",
                "confidence": 0.95,
                "evidence": "coatings may be applied to many substrates",
            },
        },
        sample_map=[
            {
                "sample_key": "E1",
                "example_id": "E1",
                "aliases": ["Example 1", "E1"],
                "polarity_hint": "positive",
                "source_ledger_ids": ["LEDGER_U_DOC_p1_b1_001"],
                "confidence": 0.93,
            }
        ],
        context_assertions=[
            {
                "assertion_id": "CTXA1",
                "target_scope": "sample",
                "target_sample": "E1",
                "field": "test_standard",
                "value": "ASTM D523",
                "source_ledger_ids": ["LEDGER_U_DOC_p1_b1_010"],
                "confidence": 0.91,
            },
            {
                "assertion_id": "CTXA2",
                "target_scope": "sample",
                "target_sample": "E1",
                "field": "substrate",
                "value": "SUB_aged_polyurethane_panel",
                "source_ledger_ids": ["LEDGER_U_DOC_p1_b1_011"],
                "confidence": 0.88,
            },
        ],
    )

    assert fact.example_id == "E1"
    assert fact.polarity_hint == Polarity.positive
    assert fact.substrate == {"tested": "SUB_aged_polyurethane_panel", "claimed": []}
    assert fact.test_condition == {"standard_id": "ASTM D523"}
    assert fact.sample_binding_confidence == 0.93
    assert fact.resolution_status == "resolved"
    assert fact.context_provenance is not None
    assert fact.context_provenance["test_condition"]["source"] == "context_assertions"
    assert fact.context_provenance["test_condition"]["ledger_ids"] == ["LEDGER_U_DOC_p1_b1_010"]
    assert fact.context_provenance["substrate"]["source"] == "context_assertions"


def test_context_inheritance_does_not_fill_tested_substrate_from_doc_profile() -> None:
    unit = FigureTableUnit(unit_id="U_DOC_p1_b1", unit_type="table", doc_id="DOC", page=1)
    fact = FactHyperedge(
        fact_id="F1",
        application="unknown",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance(
        [fact],
        unit=unit,
        matched_paragraphs=[],
        doc_profile={
            "application_family": {
                "canonical_id": "APP_automotive_oem_clearcoat",
                "confidence": 0.9,
                "evidence": "automotive coating",
            },
            "substrate_scope": {
                "canonical_id": "SUB_metal",
                "confidence": 0.9,
                "evidence": "may be used on metal",
            },
            "cure_mechanism": {
                "canonical_id": "PROC_spray_apply",
                "confidence": 0.9,
                "evidence": "may be sprayed",
            },
        },
    )

    assert fact.application == "APP_automotive_oem_clearcoat"
    assert fact.substrate is None
    assert fact.process is None
    assert fact.context_provenance is not None
    assert "substrate" not in fact.context_provenance
    assert "process" not in fact.context_provenance


def test_context_inheritance_does_not_guess_process_or_substrate_from_matched_text() -> None:
    unit = FigureTableUnit(unit_id="U_DOC_p1_b1", unit_type="table", doc_id="DOC", page=1)
    fact = FactHyperedge(
        fact_id="F1",
        application="unknown",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(doc_id="DOC", page=1, region_type="TABLE", region_id="Table 1"),
        ontology_version="v0",
        extraction_confidence=0.95,
        human_validated=False,
        doc_id="DOC",
    )

    apply_context_inheritance(
        [fact],
        unit=unit,
        matched_paragraphs=[
            {
                "paragraph_id": "P1",
                "text": (
                    "The coating was sprayed on steel panels and thermally cured at 120 C. "
                    "This prose is retrieval context only unless Stage 7 emits a context assertion."
                ),
            }
        ],
        doc_profile={},
    )

    assert fact.substrate is None
    assert fact.process is None
    assert not fact.context_provenance


def test_stage7_prompt_keeps_formulation_quantities_as_l2_facts() -> None:
    prompt = (Path(__file__).resolve().parents[1] / "prompts" / "fact_extract.txt").read_text(
        encoding="utf-8"
    )

    assert "extraction_plan" in prompt
    assert "Stage 7A -> 7B -> 7C -> 7D contract" in prompt
    assert "FORMULATION_QUANTITY" in prompt
    assert "Emit facts for RESULT_PROPERTY and FORMULATION_QUANTITY cells" in prompt
    assert "ingredient amounts are the L2" in prompt


def test_stage7_prompt_requires_ledger_context_and_mek_override() -> None:
    prompt = (Path(__file__).resolve().parents[1] / "prompts" / "fact_extract.txt").read_text(
        encoding="utf-8"
    )

    assert "row/column/caption/ledger identifies a tested substrate" in prompt
    assert "per-fact substrate" in prompt
    assert "printing/jetting/NIR" in prompt
    assert "ASTM D5402" in prompt
    assert "solvent resistance" in prompt
    assert "not mar/scratch/mechanical" in prompt


def test_stage7_validator_blocks_zero_extraction_when_expected_cells_exist() -> None:
    validation = validate_stage7_result(
        unit_id="U_WO2024227041A1_p41_b366",
        facts=[],
        context_assertions=[],
        sample_map=[],
        coverage={
            "expected_data_cells": 24,
            "extracted_facts": 0,
            "coverage_pct": 0.0,
            "coverage_basis": "semantic_result_cells",
        },
        coverage_audit={},
        evidence_ledger={
            "entries": [
                {
                    "ledger_id": "LEDGER_U_WO2024227041A1_p41_b366_0001",
                    "source_type": "TABLE_CELL",
                    "text": "SB1",
                    "row": "Binder",
                    "column": "SB1",
                }
            ]
        },
        schema_rejects=[],
    )

    assert validation["final_status"] == "blocked_zero_extraction"
    assert validation["retryable"] is True
    assert validation["issue_counts"]["error"] >= 1
    assert validation["issues"][0]["code"] == "zero_extraction"


def test_stage7_validator_requires_context_assertion_when_ledger_has_substrate_signal() -> None:
    fact = FactHyperedge(
        fact_id="F1",
        application="APP_test_coating",
        property="PROP_gloss_60deg",
        source_section_type=SourceSectionType.TABLE,
        polarity_hint=Polarity.positive,
        evidence_pointer=EvidencePointer(
            doc_id="WO2022161194A1",
            page=7,
            region_type="TABLE",
            region_id="Table 1",
            unit_id="U_WO2022161194A1_p7_b1",
            row="Example 1",
            column="60 degree gloss",
            cell="80",
        ),
        ontology_version="v0",
        extraction_confidence=0.9,
        human_validated=False,
        doc_id="WO2022161194A1",
    )

    validation = validate_stage7_result(
        unit_id="U_WO2022161194A1_p7_b1",
        facts=[fact],
        context_assertions=[],
        sample_map=[],
        coverage={"expected_data_cells": 1, "extracted_facts": 1, "coverage_pct": 100.0},
        coverage_audit={},
        evidence_ledger={
            "entries": [
                {
                    "ledger_id": "LEDGER_U_WO2022161194A1_p7_b1_0042",
                    "source_type": "DOCUMENT_PARAGRAPH",
                    "text": "The coating was applied to an aged polyurethane panel.",
                }
            ]
        },
        schema_rejects=[],
    )

    assert validation["final_status"] == "needs_repair"
    assert any(issue["code"] == "missing_context_assertion:substrate" for issue in validation["issues"])


def test_fact_normalizer_preserves_standard_and_process_contract() -> None:
    fact = {
        "application": None,
        "test_standard": "ISO 2409",
        "process": [{"step": "PROC_thermal_cure", "condition": "120 C"}],
    }

    _normalise_fact_for_schema(fact)

    assert fact["application"] == "unknown"
    assert fact["test_condition"] == {"standard_id": "ISO 2409"}
    assert fact["process"] == [{"canonical_id": "PROC_thermal_cure", "condition": "120 C"}]
    assert "test_standard" not in fact


def test_fact_normalizer_coerces_resin_system_list_without_dropping_fact() -> None:
    fact = {
        "application": "APP_test_coating",
        "resin_system": ["MAT_polyurethane_resin", "MAT_acrylic_resin"],
        "additives": ["MAT_defoamer"],
    }

    _normalise_fact_for_schema(fact)

    assert fact["resin_system"] == "MAT_polyurethane_resin"
    assert fact["additives"] == ["MAT_defoamer", "MAT_acrylic_resin"]
    assert fact["schema_coercions"] == [
        {
            "field": "resin_system",
            "action": "list_first_item_to_resin_system_rest_to_additives",
        }
    ]


def test_fact_normalizer_coerces_substrate_and_process_shapes() -> None:
    fact = {
        "application": "APP_test_coating",
        "substrate": "SUB_aged_polyurethane_panel",
        "process": "PROC_spray_apply",
    }

    _normalise_fact_for_schema(fact)

    assert fact["substrate"] == {"tested": "SUB_aged_polyurethane_panel", "claimed": []}
    assert fact["process"] == [{"canonical_id": "PROC_spray_apply"}]
    assert {
        "field": "substrate",
        "action": "string_to_tested_substrate",
    } in fact["schema_coercions"]
    assert {
        "field": "process",
        "action": "scalar_or_dict_to_process_list",
    } in fact["schema_coercions"]


def test_fact_normalizer_moves_standard_only_method_to_condition() -> None:
    fact = {"application": "APP_test_coating", "test_method": "TEST_DIN_55654"}

    _normalise_fact_for_schema(fact)

    assert "test_method" not in fact
    assert fact["test_condition"] == {"standard_id": "DIN 55654"}


def test_stage7_validation_blocks_non_publishable_statuses() -> None:
    assert is_stage7_validation_publishable({"final_status": "ok"}) is True
    assert is_stage7_validation_publishable({"final_status": "warning"}) is True
    assert is_stage7_validation_publishable({"final_status": "needs_repair"}) is False
    assert is_stage7_validation_publishable({"final_status": "blocked_zero_extraction"}) is False
    assert is_stage7_validation_publishable({"final_status": "parse_failed"}) is False


def test_fact_extractor_preserves_ledger_sidecars_in_stage7_result() -> None:
    class StubExtractor(FactExtractor):
        def __init__(self) -> None:
            super().__init__(api_key="stub")
            self._client = object()
            self.prompt = ""

        def _call_qwen(self, prompt: str) -> str:
            self.prompt = prompt
            return json.dumps(
                {
                    "unit_context": {},
                    "sample_map": [
                        {
                            "sample_key": "E1",
                            "example_id": "E1",
                            "aliases": ["Example 1"],
                            "source_ledger_ids": ["LEDGER_U_DOC_p1_b1_0001"],
                            "confidence": 0.92,
                        }
                    ],
                    "context_assertions": [
                        {
                            "assertion_id": "CTX1",
                            "target_sample": "E1",
                            "field": "test_standard",
                            "value": "ASTM D523",
                            "source_ledger_ids": ["LEDGER_U_DOC_p1_b1_0002"],
                            "confidence": 0.9,
                        }
                    ],
                    "coverage_audit": {"ledger_ids_seen": ["LEDGER_U_DOC_p1_b1_0001"]},
                    "facts": [],
                    "coverage": {},
                }
            )

    unit = FigureTableUnit(
        unit_id="U_DOC_p1_b1",
        unit_type="table",
        doc_id="DOC",
        page=1,
        region_id="Table 1",
    )
    extractor = StubExtractor()

    result = extractor.extract(
        unit,
        evidence_ledger={
            "entries": [
                {
                    "ledger_id": "LEDGER_U_DOC_p1_b1_0001",
                    "source_type": "DOCUMENT_PARAGRAPH",
                    "page": 2,
                    "text": "Example 1 is coated and tested.",
                }
            ]
        },
    )

    assert "LEDGER_U_DOC_p1_b1_0001" in extractor.prompt
    assert result.sample_map[0]["example_id"] == "E1"
    assert result.context_assertions[0]["field"] == "test_standard"
    assert result.coverage_audit["ledger_ids_seen"] == ["LEDGER_U_DOC_p1_b1_0001"]


def test_structure_resolver_writes_sidecar_and_keeps_total_formulation(tmp_path: Path) -> None:
    pending = tmp_path / "data" / "layer1" / "DOC" / "pending_figures.jsonl"
    pending.parent.mkdir(parents=True)
    pending.write_text(
        json.dumps(
            {
                "figure_id": "FIG_U_DOC_p1_b1",
                "unit_id": "U_DOC_p1_b1",
                "doc_id": "DOC",
                "page": 1,
                "subtype": "structure",
                "caption": "Formulation table includes TOTAL 100 and compound S18.",
                "chemical_candidates": [
                    {
                        "label": "Compound S18",
                        "candidate_type": "molecule",
                        "formula": "C10H12O2",
                        "smiles": "CCOC(=O)c1ccccc1",
                        "functional_groups": ["ester"],
                        "confidence": 0.9,
                        "needs_validation": False,
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    facts = resolve_structures_for_doc(tmp_path, "DOC", enable_pubchem=False)

    assert len(facts) == 1
    out = tmp_path / "data" / "extract_facts" / "DOC" / "structure_facts.jsonl"
    assert out.exists()
    assert "Compound S18" in out.read_text(encoding="utf-8")


def test_canonical_merge_queue_collects_proposals(tmp_path: Path) -> None:
    prop = tmp_path / "U_DOC_p1_b1" / "proposed_canonicals.json"
    prop.parent.mkdir(parents=True)
    prop.write_text(
        json.dumps(
            {
                "proposed_canonicals": [
                    {
                        "proposed_id": "APP_custom_roof_coating",
                        "kind": "APP",
                        "canonical_name": "custom roof coating",
                        "sub_type": "application_family",
                        "confidence": 0.7,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    queue = build_merge_queue(tmp_path)

    assert queue["schema_version"] == "canonical_merge_queue_v1"
    assert queue["items"][0]["proposed_id"] == "APP_custom_roof_coating"


def test_kg_export_materializes_nodes_and_edges(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    (db_dir / "seed_canonical_starter.sql").write_text(
        """
        INSERT INTO nodes (canonical_id, node_type, canonical_name) VALUES
          ('APP_test_coating', 'APP', 'test coating'),
          ('PROP_gloss_60deg', 'PROP', 'gloss 60 degree'),
          ('MAT_test_resin', 'MAT', 'test resin'),
          ('SUB_metal', 'SUB', 'metal'),
          ('PROC_thermal_cure', 'PROC', 'thermal cure'),
          ('TEST_gloss_meter', 'TEST', 'gloss meter');
        """,
        encoding="utf-8",
    )
    (db_dir / "seed_property_directionality.sql").write_text("", encoding="utf-8")
    (db_dir / "seed_must_merge_starter.sql").write_text(
        "INSERT INTO must_merge (alias_text, canonical_id) VALUES ('test resin alias', 'MAT_test_resin');",
        encoding="utf-8",
    )
    (db_dir / "seed_forbidden_merge_starter.sql").write_text(
        "INSERT INTO forbidden_merge (entity_a, entity_b, reason_type, explanation, risk_severity) "
        "VALUES ('MAT_test_resin', 'MAT_wrong_resin', 'chemistry_diff', 'test guard', 'high');",
        encoding="utf-8",
    )

    patents_dir = tmp_path / "data" / "patents"
    patents_dir.mkdir(parents=True)
    (patents_dir / "DOC1__patent_meta.json").write_text(
        json.dumps({"doc_id": "DOC1", "title": "Test coating patent"}),
        encoding="utf-8",
    )
    (patents_dir / "DOC1__coating_profile.json").write_text(
        json.dumps({"doc_id": "DOC1", "coating_relevance": "coating"}),
        encoding="utf-8",
    )

    unit_dir = tmp_path / "data" / "units" / "U_DOC1_p1_b1"
    unit_dir.mkdir(parents=True)
    (unit_dir / "meta.json").write_text(
        json.dumps(
            {
                "unit_id": "U_DOC1_p1_b1",
                "unit_type": "table",
                "doc_id": "DOC1",
                "page": 1,
                "region_id": "Table 1",
                "bbox": [1, 2, 3, 4],
            }
        ),
        encoding="utf-8",
    )
    (unit_dir / "caption.txt").write_text("Table 1: gloss results", encoding="utf-8")
    (unit_dir / "vlm_description.json").write_text(
        json.dumps(
            {
                "description": "Gloss performance table.",
                "identified_entities": ["PROP_gloss_60deg"],
                "table_subject": "performance_comparison",
            }
        ),
        encoding="utf-8",
    )
    (unit_dir / "matched_paragraphs.json").write_text(
        json.dumps(
            {
                "matches": [
                    {
                        "para_id": 11,
                        "page": 1,
                        "score": 0.94,
                        "reason": "Names the E1 test context.",
                        "text": "Example E1 was tested as a baked metal coating.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (unit_dir / "facts.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "fact_id": "F_U_DOC1_p1_b1_001",
                        "application": "APP_test_coating",
                        "property": "PROP_gloss_60deg",
                        "source_section_type": "TABLE",
                        "polarity_hint": "positive",
                        "evidence_pointer": {
                            "doc_id": "DOC1",
                            "page": 1,
                            "region_type": "TABLE",
                            "region_id": "Table 1",
                            "unit_id": "U_DOC1_p1_b1",
                            "row": "E1",
                            "column": "Gloss",
                            "cell": "92",
                        },
                        "ontology_version": "v0",
                        "extraction_confidence": 0.91,
                        "human_validated": False,
                        "doc_id": "DOC1",
                        "example_id": "E1",
                        "result_value": 92,
                        "resin_system": "MAT_test_resin",
                        "substrate": {"tested": "SUB_metal", "claimed": []},
                        "process": [{"step": "PROC_thermal_cure"}],
                        "test_method": "TEST_gloss_meter",
                        "test_condition": {"standard_id": "ISO 2409"},
                        "context_provenance": {
                            "test_condition": {
                                "source": "matched_paragraphs",
                                "confidence_cap": 0.86,
                            }
                        },
                        "comparison_group": "F_C1",
                    },
                    {
                        "fact_id": "F_U_DOC1_p1_b1_002",
                        "application": "APP_test_coating",
                        "property": "PROP_gloss_60deg",
                        "source_section_type": "TABLE",
                        "polarity_hint": "negative",
                        "evidence_pointer": {
                            "doc_id": "DOC1",
                            "page": 1,
                            "region_type": "TABLE",
                            "region_id": "Table 1",
                            "unit_id": "U_DOC1_p1_b1",
                            "row": "C1",
                            "column": "Gloss",
                            "cell": "72",
                        },
                        "ontology_version": "v0",
                        "extraction_confidence": 0.89,
                        "human_validated": False,
                        "doc_id": "DOC1",
                        "example_id": "C1",
                        "result_value": 72,
                        "resin_system": "MAT_test_resin",
                        "substrate": {"tested": "SUB_metal", "claimed": []},
                        "process": [{"step": "PROC_thermal_cure"}],
                        "test_method": "TEST_gloss_meter",
                    },
                    {
                        "fact_id": "F_U_DOC1_p1_b1_003",
                        "application": "APP_test_coating",
                        "property": "PROP_gloss_60deg",
                        "source_section_type": "TABLE",
                        "polarity_hint": "unknown",
                        "evidence_pointer": {
                            "doc_id": "DOC1",
                            "page": 1,
                            "region_type": "TABLE",
                            "region_id": "Table 1",
                            "unit_id": "U_DOC1_p1_b1",
                            "row": "S1",
                            "column": "Gloss",
                            "cell": "80",
                        },
                        "ontology_version": "v0",
                        "extraction_confidence": 0.82,
                        "human_validated": False,
                        "doc_id": "DOC1",
                        "example_id": "S1",
                        "result_value": 80,
                        "test_condition": {"standard_id": "ISO 2409"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (unit_dir / "normalized_facts.json").write_text(
        (unit_dir / "facts.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (unit_dir / "proposed_canonicals.json").write_text(
        json.dumps(
            {
                "proposed_canonicals": [
                    {
                        "kind": "PROP",
                        "proposed_id": "PROP_new_special_rating",
                        "canonical_name": "new special rating",
                        "confidence": "low",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = build_kg_export(tmp_path)

    kg_dir = tmp_path / "data" / "kg"
    for filename in (
        "patents.jsonl",
        "patent_profiles.jsonl",
        "example_contexts.jsonl",
        "evidence_units.jsonl",
        "facts.jsonl",
        "canonical_entities.jsonl",
        "canonical_relations.jsonl",
        "edges.jsonl",
        "manifest.json",
    ):
        assert (kg_dir / filename).exists()

    assert summary["schema_version"] == "kg_projection_v1"
    assert summary["counts"]["patents"] == 1
    assert summary["counts"]["patent_profiles"] == 1
    assert summary["counts"]["evidence_units"] == 1
    assert summary["counts"]["facts"] == 3
    assert summary["counts"]["canonical_relations"] >= 2
    assert summary["counts"]["example_contexts"] == 3
    assert summary["record_counts"]["fact_records"] == 3
    assert summary["record_counts"]["canonical_relation_records"] >= 2
    assert summary["qa"]["dangling_edges"]["count"] == 0
    assert summary["qa"]["missing_evidence"]["fact_count"] == 0
    assert summary["qa"]["missing_context"]["fact_count"] == 0
    assert summary["qa"]["canonical"]["proposed_rate"] > 0
    assert summary["qa"]["canonical"]["must_merge_target_missing_count"] == 0
    assert summary["qa"]["canonical"]["forbidden_conflict_count"] == 0

    facts = _read_jsonl(tmp_path / "data" / "kg" / "facts.jsonl")
    assert facts[0]["evidence_id"] == "EVD_U_DOC1_p1_b1"
    assert facts[0]["context_id"] == "CTX_DOC1_E1"
    assert facts[0]["test_condition"]["standard_id"] == "ISO 2409"
    assert facts[0]["record_type"] == "FactRecord"

    evidence_records = _read_jsonl(tmp_path / "data" / "kg" / "evidence_units.jsonl")
    assert evidence_records[0]["record_type"] == "EvidenceRecord"
    assert evidence_records[0]["context_only"] is False

    contexts = _read_jsonl(tmp_path / "data" / "kg" / "example_contexts.jsonl")
    contexts_by_id = {row["context_id"]: row for row in contexts}
    context = contexts_by_id["CTX_DOC1_E1"]
    baseline = contexts_by_id["CTX_DOC1_C1"]
    assert context["record_type"] == "ExampleContextRecord"
    assert context["derived_from"] == "FactRecord"
    assert context["primary_context"]["application"] == "APP_test_coating"
    assert context["primary_context"]["substrate"] == "SUB_metal"
    assert context["example_kind"] == "inventive"
    assert context["example_kind_conflict"] is False
    assert context["example_kind_evidence"][0]["kind"] == "inventive"
    assert context["raw_example_ids"] == ["E1"]
    assert context["source_trace"]["primary_evidence_id"] == "EVD_U_DOC1_p1_b1"
    assert context["source_trace"]["source_pages"] == [1]
    assert context["source_trace"]["table_units"][0]["unit_id"] == "U_DOC1_p1_b1"
    assert context["performance_fact_ids"] == ["F_U_DOC1_p1_b1_001"]
    assert context["formulation_context"]["resin_systems"] == ["MAT_test_resin"]
    assert context["formulation_context"]["processes"] == []
    assert context["use_context"]["processes"] == ["PROC_thermal_cure"]
    assert context["use_context"]["test_methods"] == ["TEST_gloss_meter"]
    assert context["use_context"]["test_standards"] == ["ISO 2409"]
    assert context["context_fields"]["test_standard"][0]["sources"] == ["matched_paragraphs"]
    assert context["qa_flags"]["missing_application"] is False
    assert context["qa_flags"]["coverage_missing"] is True
    assert context["coverage_summary"]["coverage_missing_count"] == 1
    assert context["comparison_context"]["comparison_signal_status"] == "resolved_pairs"
    assert context["comparison_context"]["comparison_resolution_status"] == "resolved_unique_baseline"
    assert context["comparison_context"]["baseline_context_ids"] == ["CTX_DOC1_C1"]
    assert baseline["comparison_context"]["compares_to_context_ids"] == ["CTX_DOC1_E1"]
    standard_only = contexts_by_id["CTX_DOC1_S1"]
    assert standard_only["use_context"]["test_standards"] == ["ISO 2409"]
    assert standard_only["qa_flags"]["missing_test_method"] is True
    assert standard_only["qa_flags"]["missing_test_standard"] is False
    assert context["context_fields"]["resin_system"][0]["sources"] == ["fact_explicit"]
    assert context["context_fields"]["resin_system"][0]["source_refs"][0]["fact_id"] == "F_U_DOC1_p1_b1_001"
    assert context["quality"]["fact_count"] == 1
    assert any(row["source"] == "matched_paragraph" for row in context["source_evidence"])

    canonicals = _read_jsonl(tmp_path / "data" / "kg" / "canonical_entities.jsonl")
    assert {row["canonical_id"] for row in canonicals} >= {
        "APP_test_coating",
        "PROP_gloss_60deg",
        "PROP_new_special_rating",
    }
    canonical_relations = _read_jsonl(tmp_path / "data" / "kg" / "canonical_relations.jsonl")
    relation_types = {row["relation_type"] for row in canonical_relations}
    assert {"alias", "must_merge", "forbidden_merge"} <= relation_types

    edges = _read_jsonl(tmp_path / "data" / "kg" / "edges.jsonl")
    triples = {(row["src"], row["edge_type"], row["dst"]) for row in edges}
    assert ("PAT_DOC1", "HAS_EVIDENCE", "EVD_U_DOC1_p1_b1") in triples
    assert ("FACT_F_U_DOC1_p1_b1_001", "SUPPORTED_BY", "EVD_U_DOC1_p1_b1") in triples
    assert ("FACT_F_U_DOC1_p1_b1_001", "HAS_PROPERTY", "PROP_gloss_60deg") in triples
    assert ("CTX_DOC1_E1", "COMPARES_TO_BASELINE", "CTX_DOC1_C1") in triples
    assert ("CTX_DOC1_C1", "BASELINE_FOR", "CTX_DOC1_E1") in triples
    assert (
        "FACT_F_U_DOC1_p1_b1_001",
        "FACT_COMPARES_TO_BASELINE",
        "FACT_F_U_DOC1_p1_b1_002",
    ) in triples


def test_kg_export_reports_dangling_canonical_edges(tmp_path: Path) -> None:
    patents_dir = tmp_path / "data" / "patents"
    patents_dir.mkdir(parents=True)
    (patents_dir / "DOC1__patent_meta.json").write_text(
        json.dumps({"doc_id": "DOC1", "title": "Test coating patent"}),
        encoding="utf-8",
    )

    unit_dir = tmp_path / "data" / "units" / "U_DOC1_p1_b1"
    unit_dir.mkdir(parents=True)
    (unit_dir / "meta.json").write_text(
        json.dumps({"unit_id": "U_DOC1_p1_b1", "unit_type": "table", "doc_id": "DOC1", "page": 1}),
        encoding="utf-8",
    )
    (unit_dir / "facts.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "fact_id": "F_U_DOC1_p1_b1_001",
                        "doc_id": "DOC1",
                        "property": "PROP_missing_rating",
                        "source_section_type": "TABLE",
                        "polarity_hint": "positive",
                        "evidence_pointer": {
                            "doc_id": "DOC1",
                            "page": 1,
                            "region_type": "TABLE",
                            "region_id": "Table 1",
                            "unit_id": "U_DOC1_p1_b1",
                            "row": "E1",
                            "column": "Rating",
                            "cell": "5B",
                        },
                        "ontology_version": "v0",
                        "extraction_confidence": 0.9,
                        "human_validated": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (unit_dir / "normalized_facts.json").write_text(
        (unit_dir / "facts.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    summary = build_kg_export(tmp_path)

    assert summary["qa"]["dangling_edges"]["count"] >= 1
    assert any(
        row["dst"] == "PROP_missing_rating"
        for row in summary["qa"]["dangling_edges"]["examples"]
    )
    assert summary["qa"]["canonical"]["referenced_missing_examples"] == ["PROP_missing_rating"]


def test_kg_export_reports_canonical_relation_qa(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    db_dir.mkdir()
    (db_dir / "seed_canonical_starter.sql").write_text(
        "INSERT INTO nodes (canonical_id, node_type, canonical_name) "
        "VALUES ('MAT_test_resin', 'MAT', 'test resin');",
        encoding="utf-8",
    )
    (db_dir / "seed_property_directionality.sql").write_text("", encoding="utf-8")
    (db_dir / "seed_must_merge_starter.sql").write_text(
        "INSERT INTO must_merge (alias_text, canonical_id, merge_type, confidence, source_evidence) VALUES "
        "('foo alias', 'MAT_test_resin', 'synonym_of', 'high', 'fixture'), "
        "('orphan alias', 'MAT_missing_resin', 'synonym_of', 'high', 'fixture');",
        encoding="utf-8",
    )
    (db_dir / "seed_forbidden_merge_starter.sql").write_text(
        "INSERT INTO forbidden_merge (entity_a, entity_b, reason_type, explanation, risk_severity) VALUES "
        "('foo alias', 'MAT_test_resin', 'fixture_conflict', 'do not auto merge', 'high');",
        encoding="utf-8",
    )

    summary = build_kg_export(tmp_path)
    relations = _read_jsonl(tmp_path / "data" / "kg" / "canonical_relations.jsonl")

    assert {row["relation_type"] for row in relations} >= {"alias", "must_merge", "forbidden_merge"}
    assert summary["qa"]["canonical"]["must_merge_target_missing_count"] == 1
    assert summary["qa"]["canonical"]["forbidden_conflict_count"] == 1
    assert summary["qa"]["canonical"]["forbidden_conflict_examples"][0]["pair"] == [
        "foo alias",
        "mat_test_resin",
    ]


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
