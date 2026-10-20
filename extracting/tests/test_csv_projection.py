from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from coating_kg.pipeline.kg_export import build_kg_export
from coating_kg.pipeline.kg_projection import fact_records


def _load_csv_builder():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_coatings_csv.py"
    spec = importlib.util.spec_from_file_location("build_coatings_csv", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


csv_builder = _load_csv_builder()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_minimal_kg(root: Path) -> Path:
    kg_dir = root / "data" / "kg"
    kg_dir.mkdir(parents=True)
    (kg_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "kg_projection_v1",
                "counts": {
                    "facts": 2,
                    "example_contexts": 1,
                    "patents": 1,
                    "canonical_entities": 5,
                    "evidence_units": 1,
                    "edges": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    _write_jsonl(
        kg_dir / "patents.jsonl",
        [{"doc_id": "DOC1", "node_id": "PAT_DOC1", "title": "Coating patent", "applicant": "ACME"}],
    )
    _write_jsonl(
        kg_dir / "canonical_entities.jsonl",
        [
            {
                "canonical_id": "APP_test_coating",
                "node_id": "APP_test_coating",
                "node_type": "APP",
                "sub_type": "Specialty",
                "status": "approved",
            },
            {
                "canonical_id": "PROP_cross_cut_adhesion",
                "node_id": "PROP_cross_cut_adhesion",
                "node_type": "PROP",
                "sub_type": "Mechanical",
                "status": "approved",
            },
            {
                "canonical_id": "PROP_formulation_component_mass",
                "node_id": "PROP_formulation_component_mass",
                "node_type": "PROP",
                "sub_type": "Formulation",
                "status": "approved",
            },
            {
                "canonical_id": "MAT_test_resin",
                "node_id": "MAT_test_resin",
                "node_type": "MAT",
                "sub_type": "Resin / binder",
                "status": "proposed",
            },
            {
                "canonical_id": "PROC_thermal_cure",
                "node_id": "PROC_thermal_cure",
                "node_type": "PROC",
                "sub_type": "Cure",
                "status": "approved",
            },
        ],
    )
    _write_jsonl(
        kg_dir / "facts.jsonl",
        [
            {
                "fact_id": "F_DOC1_001",
                "node_id": "FACT_F_DOC1_001",
                "doc_id": "DOC1",
                "context_id": "CTX_DOC1_E1",
                "evidence_id": "EVD_U_DOC1_p1_b1",
                "property": "PROP_cross_cut_adhesion",
                "result_value_text": "5B",
                "test_method": "TEST_cross_cut_adhesion",
                "test_condition": {"standard_id": "ISO 2409"},
                "evidence_pointer": {"page": 1, "region_id": "Table 1", "column": "Adhesion"},
                "polarity_hint": "positive",
                "extraction_confidence": 0.91,
                "resolution_status": "resolved",
                "sample_binding_confidence": 0.92,
                "context_source_summary": "example_id:sample_map[LEDGER_U_DOC1_p1_b1_0001]",
            },
            {
                "fact_id": "F_DOC1_002",
                "node_id": "FACT_F_DOC1_002",
                "doc_id": "DOC1",
                "context_id": "CTX_DOC1_E1",
                "evidence_id": "EVD_U_DOC1_p1_b1",
                "property": "PROP_formulation_component_mass",
                "resin_system": "MAT_test_resin",
                "result_value": 12.5,
                "evidence_pointer": {"page": 1, "region_id": "Table 1", "column": "Resin"},
                "polarity_hint": "positive",
                "extraction_confidence": 0.86,
                "resolution_status": "resolved",
            },
        ],
    )
    _write_jsonl(
        kg_dir / "example_contexts.jsonl",
        [
            {
                "context_id": "CTX_DOC1_E1",
                "node_id": "CTX_DOC1_E1",
                "doc_id": "DOC1",
                "example_id": "E1",
                "fact_ids": ["F_DOC1_001", "F_DOC1_002"],
                "evidence_ids": ["EVD_U_DOC1_p1_b1"],
                "primary_context": {"application": "APP_test_coating", "substrate": "SUB_metal"},
                "use_context": {
                    "applications": ["APP_test_coating"],
                    "substrates": ["SUB_metal"],
                    "processes": ["PROC_thermal_cure"],
                    "test_methods": ["TEST_cross_cut_adhesion"],
                    "test_standards": ["ISO 2409"],
                },
                "result_context": {"dominant_polarity": "positive"},
                "resolution_status": "resolved",
                "coverage_summary": {"min_coverage_pct": 100.0, "coverage_basis_set": ["semantic_result_cells"]},
                "quality": {"min_sample_binding_confidence": 0.92},
                "comparison_context": {
                    "comparison_signal_status": "missing",
                    "comparison_resolution_status": "missing",
                    "trend_eligible": False,
                },
                "qa_flags": {"missing_resin_system": False, "unresolved_comparison": False},
                "source_trace": {
                    "table_units": [{"page": 1, "region_id": "Table 1", "unit_id": "U_DOC1_p1_b1"}]
                },
            }
        ],
    )
    return kg_dir


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_csv_builds_from_kg_without_units(tmp_path: Path) -> None:
    kg_dir = _write_minimal_kg(tmp_path)

    rows, manifest = csv_builder.build_rows_from_kg(kg_dir)

    assert manifest["schema_version"] == "kg_projection_v1"
    assert len(rows) == 1
    row = rows[0]
    assert row["context_id"] == "CTX_DOC1_E1"
    assert row["Material:Resin / binder"] == "test_resin:12.5"
    assert row["Property:Mechanical"] == "cross_cut_adhesion@Adhesion:5B"
    assert row["Application"] == "APP_test_coating"
    assert row["Process"] == "thermal_cure"
    assert row["TestStandard"] == "ISO 2409"
    assert row["fact_ids"] == "F_DOC1_001,F_DOC1_002"
    assert row["proposed_canonical_count"] == "1"
    assert row["resolution_status"] == "resolved"
    assert row["sample_binding_confidence"] == "0.92"
    assert row["context_source_summary"] == "example_id:sample_map[LEDGER_U_DOC1_p1_b1_0001]"


def test_csv_prefers_hyperedge_projection_when_available(tmp_path: Path) -> None:
    kg_dir = _write_minimal_kg(tmp_path)
    _write_jsonl(
        kg_dir / "hyperedges.jsonl",
        [
            {
                "hyperedge_id": "HEDGE_DOC1_FORMULATION",
                "patent_id": "DOC1",
                "doc_id": "DOC1",
                "context_id": "CTX_DOC1_E1",
                "application": {"canonical_id": "APP_test_coating", "value": "test coating"},
                "substrate": {"tested": "SUB_metal", "tested_text": "aluminum"},
                "resin": [
                    {
                        "canonical_id": "MAT_test_resin",
                        "name": "test resin",
                        "role": "Resin / binder",
                        "amount": {"value_text": "20", "unit": "wt%"},
                    }
                ],
                "additives": [],
                "process": [{"canonical_id": "PROC_spray_apply", "condition": "spray_apply"}],
                "property": {
                    "canonical_id": "PROP_formulation_quantity",
                    "name": "formulation quantity",
                    "sub_type": "Formulation",
                },
                "test_method": {"canonical_id": "", "standard_id": ""},
                "test_condition": {},
                "result": {"value_text": "20", "unit": "wt%", "kind": "formulation_amount"},
                "evidence": {
                    "page": 2,
                    "table": "Table H",
                    "row": "Resin",
                    "unit_id": "U_DOC1_p1_b1",
                    "evidence_id": "EVD_U_DOC1_p1_b1",
                },
                "polarity": "positive",
                "confidence": 0.77,
                "unresolved_fields": [],
                "qa_flags": [],
                "context_source_summary": "hyperedge_fixture",
            },
            {
                "hyperedge_id": "HEDGE_DOC1_ADHESION",
                "patent_id": "DOC1",
                "doc_id": "DOC1",
                "context_id": "CTX_DOC1_E1",
                "application": {"canonical_id": "APP_test_coating", "value": "test coating"},
                "substrate": {"tested": "SUB_metal", "tested_text": "aluminum"},
                "resin": [],
                "additives": [],
                "process": [{"canonical_id": "PROC_spray_apply", "condition": "spray_apply"}],
                "property": {
                    "canonical_id": "PROP_cross_cut_adhesion",
                    "name": "cross-cut adhesion",
                    "sub_type": "Mechanical",
                },
                "test_method": {"canonical_id": "TEST_ASTM_D3359", "standard_id": "ASTM D3359"},
                "test_condition": {"standard_id": "ASTM D3359"},
                "result": {"value_text": "4B", "unit": "", "kind": "performance"},
                "evidence": {
                    "page": 2,
                    "table": "Table H",
                    "row": "Adhesion",
                    "unit_id": "U_DOC1_p1_b1",
                    "evidence_id": "EVD_U_DOC1_p1_b1",
                },
                "polarity": "positive",
                "confidence": 0.75,
                "unresolved_fields": [],
                "qa_flags": [],
                "context_source_summary": "hyperedge_fixture",
            },
        ],
    )

    rows, _ = csv_builder.build_rows_from_kg(kg_dir)

    assert len(rows) == 1
    row = rows[0]
    assert row["Material:Resin / binder"] == "test_resin:20 wt%"
    assert row["Property:Mechanical"] == "cross_cut_adhesion@Adhesion:4B"
    assert row["Substrate"] == "aluminum"
    assert row["Process"] == "spray_apply"
    assert row["TestStandard"] == "ASTM D3359"
    assert row["fact_ids"] == "HEDGE_DOC1_FORMULATION,HEDGE_DOC1_ADHESION"
    assert row["kg_fact_count"] == "2"
    assert row["context_source_summary"] == "hyperedge_fixture"


def test_visually_confirmed_water_borne_binder_is_not_hidden_by_name_heuristic() -> None:
    binder = {
        "name": "water-borne vinyl acetate copolymer dispersion",
        "role": "Resin / binder",
        "display_grade": True,
    }

    assert csv_builder.is_display_grade_hyperedge_material(binder, "Resin / binder")
    assert not csv_builder.is_display_grade_hyperedge_material(
        {"name": "water", "role": "Resin / binder"}, "Resin / binder"
    )


def test_hyperedge_projection_supports_explicit_external_slot_fields() -> None:
    hyperedge = {
        "curing_agent": [
            {"canonical_id": "MAT_isocyanate", "material": "isocyanate", "amount": {"value": 5, "unit": "wt%"}}
        ],
        "pigments": [{"canonical_id": "MAT_tio2", "material": "titanium dioxide", "amount": {"value": 10, "unit": "wt%"}}],
        "fillers": [{"canonical_id": "MAT_talc", "material": "talc", "amount": {"value": 12, "unit": "wt%"}}],
        "solvents": [{"canonical_id": "MAT_butyl_acetate", "material": "butyl acetate", "amount": {"value": 20, "unit": "g"}}],
        "property": "peak molecular weight",
        "property_canonical_id": "PROP_peak_molecular_weight",
        "result": [{"metric": "peak molecular weight", "value": 6116, "unit": "g/mol"}],
        "process": [{"step": "repair", "param": "sand, clean, and respray"}],
        "substrate": {"label": "zinc phosphate steel"},
    }

    materials = {
        material["role"]: csv_builder.hyperedge_material_cell(material)
        for material in csv_builder.hyperedge_materials(hyperedge)
    }

    assert materials == {
        "Crosslinker / Curing agent": "isocyanate:5 wt%",
        "Pigment": "tio2:10 wt%",
        "Filler": "talc:12 wt%",
        "Solvent": "butyl_acetate:20 g",
    }
    assert csv_builder.hyperedge_property_cell(hyperedge) == "peak_molecular_weight:6116 g/mol"
    assert csv_builder.hyperedge_process_labels(hyperedge) == ["sand, clean, and respray"]
    assert csv_builder.hyperedge_substrate_label(hyperedge) == "zinc phosphate steel"


def test_csv_buckets_mek_d5402_as_chemical_resistance_even_when_proposed_subtype_is_wrong(
    tmp_path: Path,
) -> None:
    kg_dir = _write_minimal_kg(tmp_path)
    with (kg_dir / "canonical_entities.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "canonical_id": "PROP_cure_performance_score",
                    "node_id": "PROP_cure_performance_score",
                    "node_type": "PROP",
                    "canonical_name": "MEK double rub solvent resistance score",
                    "sub_type": "Mechanical",
                    "status": "proposed",
                }
            )
            + "\n"
        )
    _write_jsonl(
        kg_dir / "facts.jsonl",
        [
            {
                "fact_id": "F_DOC1_MEK",
                "node_id": "FACT_F_DOC1_MEK",
                "doc_id": "DOC1",
                "context_id": "CTX_DOC1_E1",
                "evidence_id": "EVD_U_DOC1_p1_b1",
                "property": "PROP_cure_performance_score",
                "result_value_text": ">100",
                "test_method": "TEST_MEK_rub",
                "test_condition": {"standard_id": "ASTM D5402-15"},
                "evidence_pointer": {"page": 1, "region_id": "Table 1", "column": "168C"},
                "polarity_hint": "positive",
                "extraction_confidence": 0.91,
                "resolution_status": "resolved",
            }
        ],
    )

    rows, _ = csv_builder.build_rows_from_kg(kg_dir)

    assert rows[0]["Property:Chemical resistance"] == "cure_performance_score@168C:>100"
    assert rows[0]["Property:Mechanical"] == ""


def test_csv_ignores_poisoned_unit_artifacts(tmp_path: Path) -> None:
    kg_dir = _write_minimal_kg(tmp_path)
    poison = tmp_path / "data" / "units" / "U_DOC1_p1_b1"
    poison.mkdir(parents=True)
    (poison / "facts.json").write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "fact_id": "F_POISON",
                        "doc_id": "DOC1",
                        "example_id": "C99",
                        "property": "PROP_fake",
                        "application": "APP_poison",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rows, _ = csv_builder.build_rows_from_kg(kg_dir)

    assert len(rows) == 1
    assert rows[0]["example_id"] == "E1"
    assert "poison" not in json.dumps(rows[0]).casefold()


def test_csv_does_not_import_or_call_example_resolver(tmp_path: Path) -> None:
    kg_dir = _write_minimal_kg(tmp_path)

    assert not hasattr(csv_builder, "kg_example_id_for_fact")
    assert not hasattr(csv_builder, "example_id_for_fact")
    assert not hasattr(fact_records, "example_id_for_fact")
    assert not hasattr(fact_records, "build_neighbor_column_examples")
    rows, _ = csv_builder.build_rows_from_kg(kg_dir)

    assert rows[0]["example_id"] == "E1"


def test_csv_requires_kg_manifest(tmp_path: Path) -> None:
    kg_dir = _write_minimal_kg(tmp_path)
    (kg_dir / "manifest.json").unlink()

    try:
        csv_builder.build_rows_from_kg(kg_dir)
    except FileNotFoundError as exc:
        assert "manifest" in str(exc)
    else:
        raise AssertionError("CSV projection should require KG manifest")


def test_csv_and_kg_keep_unresolved_examples_scoped_unknown(tmp_path: Path) -> None:
    unit_dir = tmp_path / "data" / "units" / "U_DOC1_p1_b1"
    unit_dir.mkdir(parents=True)
    raw_facts = [
        {
            "fact_id": "F_DOC1_001",
            "doc_id": "DOC1",
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
            "extraction_confidence": 0.9,
            "human_validated": False,
        },
        {
            "fact_id": "F_DOC1_002",
            "doc_id": "DOC1",
            "property": "PROP_ret_breakthrough_time",
            "source_section_type": "TABLE",
            "polarity_hint": "positive",
            "evidence_pointer": {
                "doc_id": "DOC1",
                "page": 1,
                "region_type": "TABLE",
                "region_id": "Table 1",
                "unit_id": "U_DOC1_p1_b1",
                "row": "End of incubation period",
                "column": "Model Coating 2",
                "cell": "pass",
            },
            "ontology_version": "v0",
            "extraction_confidence": 0.8,
            "human_validated": False,
        },
        {
            "fact_id": "F_DOC1_003",
            "doc_id": "DOC1",
            "property": "PROP_cross_cut_adhesion",
            "source_section_type": "TABLE",
            "polarity_hint": "positive",
            "evidence_pointer": {
                "doc_id": "DOC1",
                "page": 1,
                "region_type": "TABLE",
                "region_id": "Table 1",
                "unit_id": "U_DOC1_p1_b1",
                "row": "unbound sample",
                "column": "Adhesion",
                "cell": "5B",
            },
            "ontology_version": "v0",
            "extraction_confidence": 0.8,
            "human_validated": False,
        },
    ]
    (unit_dir / "facts.json").write_text(json.dumps({"facts": raw_facts}), encoding="utf-8")
    normalized_facts = [dict(row) for row in raw_facts]
    normalized_facts[0]["example_id"] = "E1"
    normalized_facts[0]["context_provenance"] = {
        "example_id": {
            "source": "stage7_5_table_ocr",
            "confidence_cap": 0.86,
        }
    }
    (unit_dir / "normalized_facts.json").write_text(
        json.dumps({"schema_version": "normalized_facts_v1", "facts": normalized_facts}),
        encoding="utf-8",
    )

    build_kg_export(tmp_path)
    kg_facts = {row["fact_id"]: row for row in _read_jsonl(tmp_path / "data" / "kg" / "facts.jsonl")}

    assert kg_facts["F_DOC1_001"]["context_id"] == "CTX_DOC1_E1"
    assert kg_facts["F_DOC1_001"]["source_path"].endswith("normalized_facts.json")
    assert kg_facts["F_DOC1_002"]["example_id"] == "unknown"
    assert kg_facts["F_DOC1_002"]["resolution_status"] == "unresolved_sample"
    assert kg_facts["F_DOC1_002"]["context_id"].startswith("CTX_DOC1_UNRESOLVED_")
    assert kg_facts["F_DOC1_003"]["context_id"].startswith("CTX_DOC1_UNRESOLVED_")
    assert kg_facts["F_DOC1_002"]["context_id"] != kg_facts["F_DOC1_003"]["context_id"]


def test_kg_export_fails_when_normalized_facts_missing(tmp_path: Path) -> None:
    unit_dir = tmp_path / "data" / "units" / "U_DOC1_p1_b1"
    unit_dir.mkdir(parents=True)
    (unit_dir / "facts.json").write_text(
        json.dumps({"facts": [{"fact_id": "F_DOC1_001", "doc_id": "DOC1"}]}),
        encoding="utf-8",
    )

    try:
        build_kg_export(tmp_path)
    except FileNotFoundError as exc:
        assert "normalized_facts.json" in str(exc)
    else:
        raise AssertionError("KG export should require Stage 7.5 normalized facts")


def test_kg_export_doc_filter_ignores_old_cache_outside_requested_docs(tmp_path: Path) -> None:
    patents_dir = tmp_path / "data" / "patents"
    patents_dir.mkdir(parents=True)
    for doc_id in ("DOC1", "DOC2"):
        (patents_dir / f"{doc_id}__patent_meta.json").write_text(
            json.dumps({"doc_id": doc_id, "title": f"{doc_id} patent"}),
            encoding="utf-8",
        )

    doc1_unit = tmp_path / "data" / "units" / "U_DOC1_p1_b1"
    doc1_unit.mkdir(parents=True)
    (doc1_unit / "meta.json").write_text(
        json.dumps({"unit_id": "U_DOC1_p1_b1", "doc_id": "DOC1", "unit_type": "table", "page": 1}),
        encoding="utf-8",
    )
    fact = {
        "fact_id": "F_DOC1_001",
        "doc_id": "DOC1",
        "example_id": "E1",
        "property": "PROP_cross_cut_adhesion",
        "source_section_type": "TABLE",
        "polarity_hint": "positive",
        "evidence_pointer": {"doc_id": "DOC1", "unit_id": "U_DOC1_p1_b1", "page": 1},
        "ontology_version": "v0",
        "extraction_confidence": 0.8,
        "human_validated": False,
    }
    (doc1_unit / "facts.json").write_text(json.dumps({"facts": [fact]}), encoding="utf-8")
    (doc1_unit / "normalized_facts.json").write_text(
        json.dumps({"schema_version": "normalized_facts_v1", "facts": [fact]}),
        encoding="utf-8",
    )

    stale_doc2_unit = tmp_path / "data" / "units" / "U_DOC2_p1_b1"
    stale_doc2_unit.mkdir(parents=True)
    (stale_doc2_unit / "meta.json").write_text(
        json.dumps({"unit_id": "U_DOC2_p1_b1", "doc_id": "DOC2", "unit_type": "table", "page": 1}),
        encoding="utf-8",
    )
    (stale_doc2_unit / "facts.json").write_text(
        json.dumps({"facts": [{"fact_id": "F_DOC2_STALE", "doc_id": "DOC2"}]}),
        encoding="utf-8",
    )

    summary = build_kg_export(tmp_path, doc_ids={"DOC1"})

    assert summary["doc_ids"] == ["DOC1"]
    assert summary["counts"]["patents"] == 1
    assert summary["counts"]["evidence_units"] == 1
    assert summary["counts"]["facts"] == 1
    patents = _read_jsonl(tmp_path / "data" / "kg" / "patents.jsonl")
    evidence = _read_jsonl(tmp_path / "data" / "kg" / "evidence_units.jsonl")
    facts = _read_jsonl(tmp_path / "data" / "kg" / "facts.jsonl")
    assert {row["doc_id"] for row in patents} == {"DOC1"}
    assert {row["doc_id"] for row in evidence} == {"DOC1"}
    assert {row["doc_id"] for row in facts} == {"DOC1"}
