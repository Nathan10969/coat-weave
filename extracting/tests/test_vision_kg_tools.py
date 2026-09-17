from __future__ import annotations

import csv
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = REPO / "scripts" / "vision_kg" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


class VisionKgToolsTest(unittest.TestCase):
    def test_init_batch_selects_first_150_and_creates_patent_dirs(self) -> None:
        init_batch = load_script("init_vision_batch.py")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "pdfs"
            output_dir = root / "test_519"
            input_dir.mkdir()
            for idx in range(1, 152):
                (input_dir / f"{idx:03d}_WO20{idx:05d}A1 - Example patent.pdf").write_bytes(b"%PDF-1.4\n")

            manifest = init_batch.initialize_batch(input_dir, output_dir, limit=150)

            self.assertEqual(len(manifest), 150)
            self.assertEqual(manifest[0]["rank"], "001")
            self.assertEqual(manifest[0]["patent_id"], "WO2000001A1")
            self.assertEqual(manifest[-1]["rank"], "150")
            self.assertTrue((output_dir / "input_manifest.csv").exists())
            self.assertTrue((output_dir / "input_manifest.json").exists())
            self.assertTrue((output_dir / "patents" / manifest[0]["patent_id"] / "pages").is_dir())
            self.assertTrue((output_dir / "patents" / manifest[0]["patent_id"] / "agent_outputs").is_dir())
            self.assertTrue((output_dir / "patents" / manifest[0]["patent_id"] / "kg_pack").is_dir())

    def test_render_script_is_pure_raster_not_mineru_or_parser(self) -> None:
        render_path = REPO / "scripts" / "vision_kg" / "render_pdf_pages.py"
        source = render_path.read_text(encoding="utf-8")

        forbidden = ["mineru", "pdfplumber", "find_tables", "extract_tables", "ocr", "content_list", "middle.json"]
        for token in forbidden:
            self.assertNotIn(token, source.lower())
        self.assertIn("get_pixmap", source)

    def test_validator_checks_counts_evidence_refs_edges_and_csv_projection(self) -> None:
        validator = load_script("validate_vision_pack.py")
        csv_builder_path = REPO / "scripts" / "build_coatings_csv.py"
        spec = importlib.util.spec_from_file_location("build_coatings_csv", csv_builder_path)
        assert spec is not None and spec.loader is not None
        csv_builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(csv_builder)

        with TemporaryDirectory() as tmp:
            kg = Path(tmp) / "kg_pack"
            kg.mkdir()
            counts = {
                "patents": 1,
                "patent_profiles": 1,
                "evidence_units": 1,
                "example_contexts": 1,
                "facts": 1,
                "canonical_entities": 1,
                "canonical_relations": 0,
                "edges": 1,
                "hyperedges": 1,
            }
            (kg / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "agent_native_vision_kg_pack_v1",
                        "counts": counts,
                        "files": {key: f"{key}.jsonl" for key in counts},
                    }
                ),
                encoding="utf-8",
            )
            write_jsonl(kg / "patents.jsonl", [{"node_id": "PAT_DOC", "patent_id": "DOC"}])
            write_jsonl(kg / "patent_profiles.jsonl", [{"node_id": "PROFILE_DOC", "patent_id": "DOC"}])
            write_jsonl(
                kg / "evidence_units.jsonl",
                [{"node_id": "EVD_DOC_1", "evidence_id": "EVD_DOC_1", "patent_id": "DOC"}],
            )
            write_jsonl(
                kg / "example_contexts.jsonl",
                [{"node_id": "CTX_DOC_1", "context_id": "CTX_DOC_1", "sample_id": "Sample 1"}],
            )
            write_jsonl(
                kg / "facts.jsonl",
                [
                    {
                        "node_id": "FACT_DOC_1",
                        "fact_id": "FACT_DOC_1",
                        "evidence_id": "EVD_DOC_1",
                        "context_id": "CTX_DOC_1",
                        "fact_type": "formulation_component",
                        "predicate": "contains_binder",
                    }
                ],
            )
            write_jsonl(kg / "canonical_entities.jsonl", [{"node_id": "MAT_DOC_1", "canonical_id": "MAT_DOC_1"}])
            write_jsonl(kg / "canonical_relations.jsonl", [])
            write_jsonl(kg / "edges.jsonl", [{"edge_id": "EDGE_DOC_1", "src": "FACT_DOC_1", "dst": "EVD_DOC_1"}])
            write_jsonl(
                kg / "hyperedges.jsonl",
                [
                    {
                        "node_id": "HEDGE_DOC_1",
                        "hyperedge_id": "HEDGE_DOC_1",
                        "context_id": "CTX_DOC_1",
                        "sample_id": "Sample 1",
                        "evidence_ids": ["EVD_DOC_1"],
                        "evidence": [{"evidence_id": "EVD_DOC_1", "page": 1, "table": "Table 1"}],
                        "material": [{"name": "epoxy resin", "role": "Resin / binder", "amount_text": "10"}],
                        "property": {"name": "adhesion", "sub_type": "Mechanical"},
                        "result": {"value_text": "pass"},
                        "test_method": "cross-cut adhesion",
                    }
                ],
            )
            with (kg / "projected_same_columns.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=csv_builder.fieldnames())
                writer.writeheader()
                row = {name: "" for name in csv_builder.fieldnames()}
                row.update(
                    {
                        "patent_id": "DOC",
                        "context_id": "CTX_DOC_1",
                        "Material:Resin / binder": "epoxy resin:10",
                        "Property:Mechanical": "adhesion:pass",
                        "TestMethod": "cross-cut adhesion",
                        "Evidence": "p1 Table 1",
                        "evidence_count": "1",
                    }
                )
                writer.writerow(row)

            report = validator.validate_pack(kg)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["counts"]["hyperedges"], 1)
            self.assertEqual(report["fact_evidence_trace_rate"], 1.0)
            self.assertEqual(report["hyperedge_evidence_trace_rate"], 1.0)
            self.assertEqual(report["csv_rows"], 1)
            self.assertEqual(report["csv_projection_fill"]["Material:Resin / binder"], 1)

    def test_validator_rejects_semantically_empty_projection(self) -> None:
        validator = load_script("validate_vision_pack.py")
        csv_builder_path = REPO / "scripts" / "build_coatings_csv.py"
        spec = importlib.util.spec_from_file_location("build_coatings_csv", csv_builder_path)
        assert spec is not None and spec.loader is not None
        csv_builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(csv_builder)

        with TemporaryDirectory() as tmp:
            kg = Path(tmp) / "kg_pack"
            kg.mkdir()
            counts = {
                "patents": 1,
                "patent_profiles": 0,
                "evidence_units": 1,
                "example_contexts": 1,
                "facts": 1,
                "canonical_entities": 0,
                "canonical_relations": 0,
                "edges": 0,
                "hyperedges": 1,
            }
            (kg / "manifest.json").write_text(json.dumps({"counts": counts}), encoding="utf-8")
            write_jsonl(kg / "patents.jsonl", [{"node_id": "PAT_DOC", "patent_id": "DOC"}])
            write_jsonl(kg / "patent_profiles.jsonl", [])
            write_jsonl(kg / "evidence_units.jsonl", [{"evidence_id": "EVD_DOC_1"}])
            write_jsonl(kg / "example_contexts.jsonl", [{"context_id": "CTX_DOC_1"}])
            write_jsonl(
                kg / "facts.jsonl",
                [{"fact_id": "FACT_DOC_1", "context_id": "CTX_DOC_1", "fact_type": "formulation_component", "evidence_id": "EVD_DOC_1"}],
            )
            write_jsonl(kg / "canonical_entities.jsonl", [])
            write_jsonl(kg / "canonical_relations.jsonl", [])
            write_jsonl(kg / "edges.jsonl", [])
            write_jsonl(
                kg / "hyperedges.jsonl",
                [{"hyperedge_id": "HEDGE_DOC_1", "context_id": "CTX_DOC_1", "evidence_ids": ["EVD_DOC_1"], "claim": "generic formulation claim"}],
            )
            with (kg / "projected_same_columns.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=csv_builder.fieldnames())
                writer.writeheader()
                row = {name: "" for name in csv_builder.fieldnames()}
                row.update({"patent_id": "DOC", "context_id": "CTX_DOC_1", "evidence_count": "1"})
                writer.writerow(row)

            report = validator.validate_pack(kg)

            self.assertEqual(report["status"], "error")
            self.assertIn("projection_gap:material_slots_empty", report["errors"])
            self.assertIn("projection_gap:evidence_column_empty", report["errors"])

    def test_projector_requires_hyperedges_and_writes_same_columns_csv(self) -> None:
        projector = load_script("project_vision_csv.py")
        csv_builder_path = REPO / "scripts" / "build_coatings_csv.py"
        spec = importlib.util.spec_from_file_location("build_coatings_csv", csv_builder_path)
        assert spec is not None and spec.loader is not None
        csv_builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(csv_builder)

        with TemporaryDirectory() as tmp:
            kg = Path(tmp) / "kg_pack"
            kg.mkdir()
            for name in [
                "patents.jsonl",
                "patent_profiles.jsonl",
                "evidence_units.jsonl",
                "example_contexts.jsonl",
                "facts.jsonl",
                "canonical_entities.jsonl",
                "canonical_relations.jsonl",
                "edges.jsonl",
            ]:
                write_jsonl(kg / name, [])
            (kg / "manifest.json").write_text(json.dumps({"schema_version": "test"}), encoding="utf-8")
            write_jsonl(kg / "hyperedges.jsonl", [])

            out = kg / "projected_same_columns.csv"
            summary = projector.project_csv(kg, out)

            self.assertEqual(summary["rows"], 0)
            self.assertEqual(summary["columns"], len(csv_builder.fieldnames()))
            with out.open(encoding="utf-8-sig", newline="") as handle:
                header = next(csv.reader(handle))
            self.assertEqual(header, csv_builder.fieldnames())

    def test_merge_vision_packs_concatenates_jsonl_and_csv_mechanically(self) -> None:
        merger = load_script("merge_vision_packs.py")
        with TemporaryDirectory() as tmp:
            batch = Path(tmp) / "batch"
            batch.mkdir()
            patents_root = batch / "patents"
            rows = []
            for idx, patent_id in enumerate(["DOC_A", "DOC_B"], start=1):
                kg = patents_root / patent_id / "kg_pack"
                kg.mkdir(parents=True)
                for key, file_name in merger.JSONL_FILES.items():
                    write_jsonl(
                        kg / file_name,
                        [
                            {
                                "node_id": f"{key}_{patent_id}",
                                "patent_id": patent_id,
                            }
                        ],
                    )
                with (kg / "projected_same_columns.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(["patent_id", "hyperedge_id"])
                    writer.writerow([patent_id, f"HYP_{patent_id}"])
                rows.append({"rank": f"{idx:03d}", "patent_id": patent_id, "kg_pack_dir": str(kg)})

            with (batch / "input_manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["rank", "patent_id", "kg_pack_dir"])
                writer.writeheader()
                writer.writerows(rows)

            manifest = merger.merge_batch(batch)

            self.assertEqual(manifest["patent_count"], 2)
            self.assertEqual(manifest["counts"]["hyperedges"], 2)
            self.assertEqual(manifest["csv_rows"], 2)
            self.assertTrue((batch / "merged_kg_pack" / "manifest.json").exists())
            with (batch / "merged_kg_pack" / "hyperedges.jsonl").open(encoding="utf-8") as handle:
                self.assertEqual(sum(1 for _line in handle), 2)
            with (batch / "merged_kg_pack" / "projected_same_columns.csv").open(
                encoding="utf-8-sig", newline=""
            ) as handle:
                csv_rows = list(csv.reader(handle))
            self.assertEqual(csv_rows[0], ["patent_id", "hyperedge_id"])
            self.assertEqual(len(csv_rows), 3)


if __name__ == "__main__":
    unittest.main()
