from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path


try:
    import psycopg2  # noqa: F401
except ImportError:
    psycopg2_module = types.ModuleType("psycopg2")
    extras_module = types.ModuleType("psycopg2.extras")
    extras_module.execute_values = lambda *args, **kwargs: None
    psycopg2_module.extras = extras_module
    sys.modules["psycopg2"] = psycopg2_module
    sys.modules["psycopg2.extras"] = extras_module

try:
    import requests  # noqa: F401
except ImportError:
    sys.modules["requests"] = types.ModuleType("requests")

import server_hyperedge_pgvector_job as job


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


class CollectionManifestV2Tests(unittest.TestCase):
    def make_pack(self, root: Path, applicants: list[str]) -> None:
        write_jsonl(
            root / "patents.jsonl",
            [
                {
                    "schema_version": "coating_patent_v2",
                    "doc_id": "DOC1",
                    "patent_id": "DOC1",
                    "publication_number": "DOC1",
                    "title": "Example",
                    "applicants": applicants,
                }
            ],
        )
        write_jsonl(
            root / "hyperedges.jsonl",
            [
                {
                    "schema_version": "coating_hyperedge_v2",
                    "doc_id": "DOC1",
                    "hyperedge_id": "HE1",
                    "hyperedge_type": "formulation",
                    "evidence_ids": ["E1"],
                    "fact_ids": ["F1"],
                }
            ],
        )

    def make_registry(self, path: Path) -> None:
        write_jsonl(
            path,
            [
                {
                    "schema_version": "org_registry_v1",
                    "canonical_id": "ORG_HB_FULLER",
                    "canonical_name": "H.B. Fuller Company",
                    "aliases": [{"text": "H.B. Fuller Company"}],
                    "status": "active",
                    "record_version": 1,
                },
                {
                    "schema_version": "org_registry_v1",
                    "canonical_id": "ORG_DOW",
                    "canonical_name": "The Dow Chemical Company",
                    "aliases": [{"text": "The Dow Chemical Company"}],
                    "status": "active",
                    "record_version": 1,
                },
            ],
        )

    def test_v2_joint_assignee_uses_three_segment_id_and_full_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            self.make_pack(pack, ["H.B. Fuller Company", "The Dow Chemical Company"])
            registry = root / "org_registry.jsonl"
            self.make_registry(registry)
            manifest = root / "manifest.json"
            write_json(
                manifest,
                {
                    "schema_version": "hyperedge_collection_sources_v2",
                    "collection_id": "hb_v2",
                    "collection_kind": "assignee",
                    "expected_assignee_ids": ["ORG_HB_FULLER"],
                    "sources": [
                        {
                            "source_id": "hb",
                            "root": str(pack),
                            "layout": "aggregate",
                            "expected_hyperedges": 1,
                        }
                    ],
                },
            )

            objects = job.load_collection_objects(manifest, "hb_v2", org_registry_path=registry)

            self.assertEqual(objects[0].object_id, "hb_v2::DOC1::HE1")
            self.assertNotIn("company", objects[0].metadata)
            self.assertEqual(objects[0].metadata["id_schema_version"], "retrieval_object_v2")
            self.assertEqual(objects[0].metadata["assignee_ids"], ["ORG_HB_FULLER", "ORG_DOW"])
            self.assertEqual(objects[0].metadata["hyperedge_type"], "formulation")

    def test_v2_fails_closed_when_assignee_does_not_match_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            self.make_pack(pack, ["The Dow Chemical Company"])
            registry = root / "org_registry.jsonl"
            self.make_registry(registry)
            manifest = root / "manifest.json"
            write_json(
                manifest,
                {
                    "schema_version": "hyperedge_collection_sources_v2",
                    "collection_id": "hb_v2",
                    "collection_kind": "assignee",
                    "expected_assignee_ids": ["ORG_HB_FULLER"],
                    "sources": [
                        {
                            "source_id": "hb",
                            "root": str(pack),
                            "layout": "aggregate",
                            "expected_hyperedges": 1,
                        }
                    ],
                },
            )

            with self.assertRaisesRegex(ValueError, "unresolved or out-of-scope assignee"):
                job.load_collection_objects(manifest, "hb_v2", org_registry_path=registry)

    def test_v1_company_id_format_remains_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "pack"
            pack.mkdir()
            self.make_pack(pack, ["H.B. Fuller Company"])
            manifest = root / "manifest.json"
            write_json(
                manifest,
                {
                    "schema_version": "hyperedge_collection_sources_v1",
                    "collection_id": "legacy",
                    "sources": [
                        {
                            "company": "hb_fuller",
                            "root": str(pack),
                            "layout": "aggregate",
                            "expected_hyperedges": 1,
                        }
                    ],
                },
            )

            objects = job.load_collection_objects(manifest, "legacy")

            self.assertEqual(objects[0].object_id, "legacy::hb_fuller::DOC1::HE1")
            self.assertEqual(objects[0].metadata["company"], "hb_fuller")
            self.assertNotIn("id_schema_version", objects[0].metadata)


if __name__ == "__main__":
    unittest.main()
