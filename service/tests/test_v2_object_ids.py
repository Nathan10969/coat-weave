from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


KG_TOOLS = Path(__file__).resolve().parents[1] / "kg_tools"
sys.path.insert(0, str(KG_TOOLS))

import expand_hyperedge_multihop as expand
import kg_expand_http_service as service


class V2ObjectIdTests(unittest.TestCase):
    def test_store_builds_v2_and_legacy_object_ids(self) -> None:
        row = {"doc_id": "DOC1", "hyperedge_id": "HE1"}
        v2_store = expand.KgStore()
        expand.add_row(
            v2_store,
            "hyperedges.jsonl",
            row,
            None,
            expand.KgDirectorySource(Path("."), "new_collection", None, "retrieval_object_v2"),
        )
        legacy_store = expand.KgStore()
        expand.add_row(
            legacy_store,
            "hyperedges.jsonl",
            row,
            None,
            expand.KgDirectorySource(Path("."), "old_collection", "hb_fuller"),
        )

        self.assertEqual(v2_store.hyperedges[("DOC1", "HE1")]["object_id"], "new_collection::DOC1::HE1")
        self.assertEqual(
            legacy_store.hyperedges[("DOC1", "HE1")]["object_id"],
            "old_collection::hb_fuller::DOC1::HE1",
        )

    def test_raw_hyperedge_fallback_uses_last_id_segment(self) -> None:
        self.assertEqual(expand.resolve_raw_hyperedge_id("new::DOC1::HE1", "DOC1", {}), "HE1")
        self.assertEqual(expand.resolve_raw_hyperedge_id("old::hb::DOC1::HE1", "DOC1", {}), "HE1")
        self.assertEqual(expand.resolve_raw_hyperedge_id("DOC1::HE1", "DOC1", {}), "HE1")

    def test_service_manifest_accepts_v2_without_company_and_rejects_ambiguous_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "pack"
            source_root.mkdir()
            manifest = root / "sources.json"
            manifest.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "root": str(source_root),
                                "collection_id": "new_collection",
                                "id_schema_version": "retrieval_object_v2",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            sources = service.load_kg_source_manifest(manifest)

            self.assertEqual(sources[0].collection_id, "new_collection")
            self.assertIsNone(sources[0].company)
            self.assertEqual(sources[0].id_schema_version, "retrieval_object_v2")

            manifest.write_text(
                json.dumps({"sources": [{"root": str(source_root), "collection_id": "ambiguous"}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "legacy KG source requires"):
                service.load_kg_source_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
