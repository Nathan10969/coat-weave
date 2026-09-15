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
from coating_kg.pipeline.kg_export import build_kg_export
from coating_kg.pipeline.patent_metadata_extractor import _load_front_matter_text, find_content_list
from coating_kg.pipeline.structure_resolver import resolve_structures_for_doc
from coating_kg.pipeline.unit_router import (
    RouteDecision,
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

    out = write_route_decision(tmp_path, unit.unit_id, "DOC", decision, {"table_subject": "performance comparison"})
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
    assert fact.substrate == {"tested": "SUB_metal", "claimed": []}
    assert fact.context_provenance is not None
    assert fact.extraction_confidence <= 0.88


def test_context_inheritance_accepts_list_process_candidate() -> None:
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
    profile = {
        "cure_mechanism": {
            "label": "isocyanate crosslinking",
            "canonical_id": "PROC_isocyanate_crosslinking",
            "evidence": "isocyanate crosslinking",
        }
    }

    apply_context_inheritance([fact], unit=unit, matched_paragraphs=[], doc_profile=profile)

    assert fact.process == [{"canonical_id": "PROC_isocyanate_crosslinking", "source": "doc_profile"}]


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
                    }
                ]
            }
        ),
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

    assert summary["counts"]["patents"] == 1
    assert summary["counts"]["patent_profiles"] == 1
    assert summary["counts"]["evidence_units"] == 1
    assert summary["counts"]["facts"] == 2
    assert summary["counts"]["example_contexts"] == 2

    facts = _read_jsonl(tmp_path / "data" / "kg" / "facts.jsonl")
    assert facts[0]["evidence_id"] == "EVD_U_DOC1_p1_b1"
    assert facts[0]["context_id"] == "CTX_DOC1_E1"

    contexts = _read_jsonl(tmp_path / "data" / "kg" / "example_contexts.jsonl")
    contexts_by_id = {row["context_id"]: row for row in contexts}
    context = contexts_by_id["CTX_DOC1_E1"]
    baseline = contexts_by_id["CTX_DOC1_C1"]
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
    assert context["qa_flags"]["missing_application"] is False
    assert context["qa_flags"]["coverage_missing"] is True
    assert context["coverage_summary"]["coverage_missing_count"] == 1
    assert context["comparison_context"]["comparison_signal_status"] == "resolved_pairs"
    assert context["comparison_context"]["comparison_resolution_status"] == "resolved_unique_baseline"
    assert context["comparison_context"]["baseline_context_ids"] == ["CTX_DOC1_C1"]
    assert baseline["comparison_context"]["compares_to_context_ids"] == ["CTX_DOC1_E1"]
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


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
