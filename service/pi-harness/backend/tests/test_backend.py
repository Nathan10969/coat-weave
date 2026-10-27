import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from patent_milvus_tool import PatentMilvusTool, build_from_env
from patent_milvus_tool.collection_profile import collection_profile
from patent_milvus_tool.documents import DocumentStore
from patent_milvus_tool.tos_documents import TosDocumentStore, DocumentReadError, markdown_page
from patent_milvus_tool.server import app


class FakeClient:
    def query(self, **kwargs):
        self.last_query = kwargs
        return [{"count(*)": 7}]


class BackendTests(unittest.TestCase):
    def test_schema_is_packaged_and_operations_are_read_only(self):
        schema = PatentMilvusTool.definition()
        self.assertEqual(schema["name"], "patent_search")
        ops = schema["inputSchema"]["properties"]["statement"]["properties"]["op"]["enum"]
        self.assertEqual(set(ops), {"describe", "query", "count", "count_distinct", "search", "read_document"})

    def test_live_schema_never_invents_document_count_rule(self):
        result = collection_profile(schema={"fields": [{"name": "doc_id", "type": 21}]},
            database="demo", collection="demo_chunks", doc_id_field="doc_id", content_field="content", fulltext={})
        self.assertIsNone(result["document_count_filter"])
        self.assertEqual(result["document_count_rule"]["status"], "unvalidated_for_current_collection")
        self.assertNotIn("provenance", result["document_count_rule"])

    def test_count_counts_rows_without_inserting_representative_filter(self):
        client = FakeClient()
        result = PatentMilvusTool(collection_name="demo_chunks", client=client).execute({"op": "count", "filter": ""})
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 7)
        self.assertEqual(result["count_unit"], "chunks")
        self.assertEqual(client.last_query["filter"], "")

    def test_service_errors_do_not_echo_signed_urls_or_tokens(self):
        class BrokenClient:
            def query(self, **kwargs):
                raise RuntimeError("https://private.invalid/?token=DO_NOT_ECHO")
        result = PatentMilvusTool(collection_name="demo_chunks", client=BrokenClient()).execute({"op": "count"})
        self.assertFalse(result["success"])
        self.assertNotIn("DO_NOT_ECHO", json.dumps(result))
        self.assertNotIn("private.invalid", json.dumps(result))

    def test_local_pages_preserve_versions_and_reject_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "SYNTHETIC-DEMO.md"
            file.write_text("line one\nline two\nline three\n", encoding="utf-8")
            store = DocumentStore(directory)
            tool = PatentMilvusTool(collection_name="demo_chunks", client=FakeClient(), document_store=store)
            first = tool.execute({"op": "read_document", "doc_id": "SYNTHETIC-DEMO", "line_limit": 1})
            self.assertTrue(first["has_more"])
            self.assertIn("L1: line one", first["content"])
            second = tool.execute(first["next_statement"])
            self.assertIn("L2: line two", second["content"])
            file.write_text("changed", encoding="utf-8")
            self.assertFalse(tool.execute(first["next_statement"])["success"])
            with self.assertRaises(ValueError):
                store.resolve("../escape.md")
            with self.assertRaises(ValueError):
                store.read("../escape")

    def test_long_markdown_lines_use_character_continuation(self):
        text = "x" * 49000 + "\nend"
        first = markdown_page(text, doc_id="SYNTHETIC-DEMO", uri="patent://demo", etag="version")
        statement = first["next_statement"]
        self.assertEqual(statement["start_line"], 1)
        self.assertGreater(statement["start_char"], 0)
        second = markdown_page(text, doc_id=statement["doc_id"], uri="patent://demo", etag=statement["etag"],
            start_line=statement["start_line"], start_char=statement["start_char"])
        self.assertFalse(second["has_more"])

    def test_backend_schema_needs_no_database_and_enforces_token(self):
        with patch.dict(os.environ, {"PATENT_SEARCH_API_TOKEN": "synthetic-service-token"}), TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(client.get("/tools/patent_search/schema").status_code, 401)
            response = client.get("/tools/patent_search/schema", headers={"Authorization": "Bearer synthetic-service-token"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["name"], "patent_search")

    def test_storage_and_collection_must_be_explicit(self):
        with patch.dict(os.environ, {}, clear=True), patch("patent_milvus_tool.patent_milvus_tool.PatentMilvusTool") as factory:
            with self.assertRaises(ValueError):
                build_from_env()
            os.environ["MILVUS_COLLECTION"] = "demo_chunks"
            build_from_env()
            self.assertIsNone(factory.call_args.kwargs["document_store"])

    def test_remote_backend_access_requires_service_token(self):
        with patch.dict(os.environ, {}, clear=True), TestClient(app) as client:
            self.assertEqual(client.get("/tools/patent_search/schema").status_code, 401)

    def test_tos_failure_is_safe_and_uses_explicit_bucket(self):
        class BrokenTos:
            def get_object(self, *args, **kwargs):
                raise RuntimeError("signed_url=DO_NOT_ECHO")
        store = TosDocumentStore(bucket="synthetic-demo", client=BrokenTos())
        with self.assertRaises(DocumentReadError) as caught:
            store.read("WO2009000001A3")
        self.assertNotIn("DO_NOT_ECHO", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
