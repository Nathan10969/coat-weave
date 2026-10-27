"""Read-only patent search over Milvus, with bounded snippets and full-text references."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import closing, suppress
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.request import Request, urlopen

try:
    from .collection_profile import collection_profile
    from .documents import DocumentStore
    from .tos_documents import TosDocumentStore, tos_store_from_env
except ImportError:  # Original directory-local entry point.
    from collection_profile import collection_profile
    from documents import DocumentStore
    from tos_documents import TosDocumentStore, tos_store_from_env


def integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in {low}..{high}")
    return value


class PatentMilvusTool:
    def __init__(self, *, collection_name: str, milvus_uri: str = "http://127.0.0.1:19530",
                 milvus_token: str | None = None, milvus_database: str = "default",
                 embedding_model: str | None = None, reranker_model: str | None = None,
                 embedding_url: str | None = None, embedding_dimension: int = 1024,
                 embedding_query_prompt: str = "", vector_field: str = "embedding",
                 text_field: str = "content", doc_id_field: str = "doc_id",
                 title_field: str = "title", publication_number_field: str = "publication_number",
                 source_path_field: str = "source_path", sparse_field: str = "",
                 metric_type: str = "COSINE", device: str | None = None,
                 default_recall_docs: int = 40, default_group_size: int = 2,
                 default_final_docs: int = 10, rerank_batch_size: int = 4,
                 rerank_max_chars: int = 12000, timeout: float = 60,
                 distinct_max_rows: int = 1_000_000, distinct_max_seconds: float = 60,
                 document_store: DocumentStore | TosDocumentStore | None = None,
                 client: Any = None, embedder: Any = None, reranker: Any = None):
        if client is None:
            from pymilvus import MilvusClient
            client = MilvusClient(uri=milvus_uri, token=milvus_token or "",
                                  db_name=milvus_database, timeout=timeout)
        self.client, self.collection_name = client, collection_name
        self.milvus_database = milvus_database
        self.vector_field, self.text_field = vector_field, text_field
        self.doc_id_field, self.title_field = doc_id_field, title_field
        self.publication_number_field = publication_number_field
        self.source_path_field, self.sparse_field = source_path_field, sparse_field
        self.metric_type, self.device = metric_type.upper(), device
        self.default_recall_docs = default_recall_docs
        self.default_group_size, self.default_final_docs = default_group_size, default_final_docs
        self.rerank_batch_size, self.rerank_max_chars = rerank_batch_size, rerank_max_chars
        self.embedding_model, self.reranker_model = embedding_model, reranker_model
        self.embedding_url, self.embedding_dimension = embedding_url, embedding_dimension
        self.embedding_query_prompt = embedding_query_prompt
        self.embedder, self.reranker = embedder, reranker
        self.timeout, self.distinct_max_seconds = float(timeout), float(distinct_max_seconds)
        self.distinct_max_rows = integer(distinct_max_rows, "distinct_max_rows", 1, 10**12)
        if not all(math.isfinite(v) and v > 0 for v in (self.timeout, self.distinct_max_seconds)):
            raise ValueError("Timeouts must be finite and positive")
        self.documents = document_store
        self._schema: dict | None = None
        self._indexes: list[dict] | None = None
        self._index_inspection_error: str | None = None
        self._sequence = 0

    @staticmethod
    def definition() -> dict:
        return json.loads(Path(__file__).with_name("tool_schema.json").read_text(encoding="utf-8"))

    @classmethod
    def schema(cls) -> dict:
        d = cls.definition()
        return {"type": "function", "function": {
            "name": d["name"], "description": d["description"], "parameters": d["inputSchema"]}}

    def execute(self, statement: dict[str, Any]) -> dict[str, Any]:
        self._sequence += 1
        base = {"result_id": f"patent_search:{self._sequence}", "collection": self.collection_name}
        try:
            if not isinstance(statement, dict):
                raise ValueError("statement must be a JSON object")
            self._validate_options(statement)
            op = statement["op"]
            result = getattr(self, "_" + op)(statement)
            result = self._jsonable({**base, "success": True, "op": op, **result})
            json.dumps(result, ensure_ascii=False, allow_nan=False)
            return result
        except Exception as exc:
            return {**base, "success": False, "error_type": type(exc).__name__,
                    "message": "Patent query failed; check parameters and backend configuration. Raw service errors are not exposed.",
                    "hint": "Inspect the error/describe and correct the statement; errors are not zero results."}

    __call__ = execute

    def _validate_options(self, s: dict) -> None:
        common = {"op", "filter", "filter_params", "partition_names", "consistency_level"}
        options = {"describe": {"op"}, "count": common,
            "query": common | {"limit", "offset", "output_fields", "group_by_fields", "order_by_fields"},
            "count_distinct": common | {"distinct_field"},
            "read_document": {"op", "doc_id", "start_line", "line_limit", "start_char", "etag"},
            "search": common | {"query_text", "mode", "limit", "recall_docs", "group_by_field",
                "group_size", "output_fields", "search_params", "sparse_search_params", "rerank"}}
        op = s.get("op")
        if not isinstance(op, str) or op not in options:
            raise ValueError("op must be describe/query/count/count_distinct/search/read_document")
        if set(s) - options[op]:
            raise ValueError(f"Unsupported fields for {op}: {sorted(set(s) - options[op])}")
        if not isinstance(s.get("filter", ""), str) or len(s.get("filter", "")) > 32000:
            raise ValueError("filter must be a native Milvus expression of at most 32000 characters, not SQL/ES DSL")
        for key in ("filter_params", "search_params", "sparse_search_params"):
            if key in s and not isinstance(s[key], dict):
                raise ValueError(f"{key} must be an object")
        for key in ("output_fields", "partition_names", "group_by_fields", "order_by_fields"):
            if key in s and (not isinstance(s[key], list) or not s[key] or
                             not all(isinstance(x, str) and x for x in s[key])):
                raise ValueError(f"{key} must be a nonempty string array")
        if "consistency_level" in s and s["consistency_level"] not in ("Strong", "Bounded", "Session", "Eventually"):
            raise ValueError("Unknown consistency_level")
        if "rerank" in s and type(s["rerank"]) is not bool:
            raise ValueError("rerank must be boolean")
        if op == "read_document":
            if not isinstance(s.get("doc_id"), str) or not s["doc_id"]:
                raise ValueError("read_document requires doc_id")
            integer(s.get("start_line", 1), "start_line", 1, 100_000_000)
            integer(s.get("line_limit", 200), "line_limit", 1, 2000)
            integer(s.get("start_char", 0), "start_char", 0, 32 * 1024 * 1024)

    def _read_document(self, s: dict) -> dict:
        if self.documents is None:
            raise ValueError("read_document requires configured local or TOS storage")
        return self.documents.read(s["doc_id"], **{key: s[key] for key in
            ("start_line", "line_limit", "start_char", "etag") if key in s})

    def _collection_schema(self, refresh: bool = False) -> dict:
        if refresh or self._schema is None:
            self._schema = self.client.describe_collection(collection_name=self.collection_name, timeout=self.timeout)
        return self._schema

    def _fields(self) -> dict[str, dict]:
        return {f["name"]: f for f in self._collection_schema()["fields"]}

    def _describe(self, s: dict) -> dict:
        schema = self._collection_schema(refresh=True)
        indexes = self._collection_indexes(refresh=True)
        bm25 = self._bm25_capability()
        server_version = None
        with suppress(Exception):
            server_version = self.client.get_server_version(timeout=self.timeout)
        fulltext = self.documents.describe() if isinstance(self.documents, TosDocumentStore) else {
            "source": "local" if self.documents else "unconfigured"}
        if isinstance(self.documents, TosDocumentStore):
            fulltext = {**fulltext, "resolution": "derived_from_doc_id", "result_field": "full_text.uri",
                "schema_field": None, "availability": "Checked only by read_document; a derived URI does not prove existence."}
        elif self.documents:
            fulltext = {**fulltext, "resolution": "stored_markdown_path_or_doc_id_template",
                "result_field": "full_text.read_path", "template": self.documents.template,
                "schema_field": None}
        profile = collection_profile(schema=schema, database=self.milvus_database,
            collection=self.collection_name, doc_id_field=self.doc_id_field,
            content_field=self.text_field, fulltext=fulltext)
        embedding_configured = bool(self.embedding_url or self.embedding_model or self.embedder is not None)
        reranker_configured = bool(self.reranker_model or self.reranker is not None)
        fields = self._fields()
        dense_configured = embedding_configured and self.vector_field in fields
        bm25_configured = bm25["schema_configured"] and bm25["bm25_index_present"] is not False
        capabilities = {name: {"configured": configured, "availability": "not_probed"}
            for name, configured in (("dense", dense_configured), ("bm25", bm25_configured),
                ("hybrid", dense_configured and bm25_configured),
                ("rerank", reranker_configured and self.text_field in fields),
                ("read_document", self.documents is not None))}
        return {"database_engine": "Milvus", "database": self.milvus_database,
            "schema": schema, "indexes": indexes, "collection_profile": profile,
            "index_inspection_error": self._index_inspection_error,
            "server_version": server_version, "tool_semantics": {
            "record_level": "chunk", "document_identity_field": self.doc_id_field,
            "rerank_text_field": self.text_field, "vector_field": self.vector_field,
            "full_text_root": str(self.documents.root) if self.documents and self.documents.root else None,
            "full_text_template": self.documents.template if self.documents else None,
            "full_text_storage": fulltext,
            "sparse_field": bm25["sparse_field"], "bm25_schema_configured": bm25["schema_configured"],
            "bm25": bm25,
            "search_modes": ["dense", "bm25", "hybrid"],
            "embedding_configured": embedding_configured, "reranker_configured": reranker_configured,
            "capabilities": capabilities,
            "notice": "search_modes lists supported protocol modes. Capabilities report configuration only; describe does not probe embedding/reranker/TOS connectivity or model compatibility. BM25/hybrid needs a BM25 function, sparse field and index. Scalar grouping/order needs compatible server AND SDK. No collections are created or altered."}}

    def _common(self, s: dict) -> dict:
        return {"collection_name": self.collection_name, "timeout": self.timeout,
                **{k: s[k] for k in ("partition_names", "consistency_level", "filter_params") if k in s}}

    def _projection(self, s: dict, *, search: bool = False, rerank: bool = False) -> list[str]:
        fields = self._fields()
        requested = s.get("output_fields", [self.doc_id_field, self.title_field, self.publication_number_field])
        if any(f not in fields for f in requested):
            raise ValueError("output_fields must name real scalar fields; use describe first")
        for f in requested:
            dtype = fields[f].get("type", "")
            if "VECTOR" in str(dtype).upper() or (isinstance(dtype, int) and dtype >= 100):
                raise ValueError("Select scalar output fields, not vectors")
        required = [self.doc_id_field, self.title_field, self.publication_number_field,
                    "chunk_metadata", "metadata", "full_text_path", "markdown_path", "md_path", self.source_path_field]
        if search:
            # Body fields are exposed only as bounded matched-chunk excerpts.
            requested = [f for f in requested if f not in {self.text_field, "content", "search_text"}]
            required += ["chunk_uid", "section_title", self.text_field]
        if rerank:
            if self.text_field not in fields:
                raise ValueError(f"Rerank text field {self.text_field!r} is absent")
            required.append(self.text_field)
        return list(dict.fromkeys(requested + [f for f in required if f in fields]))

    def _reference(self, row: dict) -> dict:
        doc_id = row.get(self.doc_id_field)
        if not isinstance(doc_id, str) or not doc_id:
            raise ValueError("A returned chunk has no valid document identity")
        if self.documents:
            reference = self.documents.reference(row, doc_id, self.source_path_field)
            if isinstance(self.documents, DocumentStore) and reference.get("path"):
                # Keep a portable citation identity plus an unambiguous path for
                # read/bash when the agent working directory differs from the mount.
                reference["read_path"] = str(self.documents.resolve(reference["path"], exists=False))
            return reference
        return {"path": None, "available": False,
                "reason": "Full-text storage is not configured; select local Markdown or TOS in the backend"}

    def _require_scalar_grouping(self) -> None:
        try:
            sdk_major = int(version("pymilvus").split(".")[0])
            server_major = int(self.client.get_server_version(timeout=self.timeout).lstrip("v").split(".")[0])
        except (ValueError, AttributeError, PackageNotFoundError):
            raise ValueError("Cannot verify scalar grouping/order support; use ordinary query/count_distinct") from None
        if min(sdk_major, server_major) < 3:
            raise ValueError("Scalar group_by_fields/order_by_fields requires Milvus AND PyMilvus >= 3")

    def _query(self, s: dict) -> dict:
        limit = integer(s.get("limit", 10), "limit", 1, 100)
        offset = integer(s.get("offset", 0), "offset", 0, 16000)
        if offset + limit + 1 >= 16384:
            raise ValueError("Deep offset pagination is unsupported; narrow the filter")
        grouped = bool(s.get("group_by_fields"))
        if grouped or s.get("order_by_fields"):
            self._require_scalar_grouping()
        projection = s.get("output_fields", s.get("group_by_fields", []) + ["count(*)"]) if grouped else self._projection(s)
        rows = self.client.query(**self._common(s), filter=s.get("filter", ""),
            output_fields=projection, limit=limit + 1, offset=offset,
            **{k: s[k] for k in ("group_by_fields", "order_by_fields") if k in s})
        results = []
        for row in rows[:limit]:
            selected = {k: v for k, v in row.items() if k in s.get("output_fields", [self.doc_id_field, self.title_field, self.publication_number_field])}
            if grouped:
                selected = dict(row)
            else:
                selected[self.doc_id_field] = row[self.doc_id_field]
                selected["full_text"] = self._reference(row)
            results.append(selected)
        return {"results": results, "count_returned": len(results), "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
            "record_level": "groups_of_chunks" if grouped else "chunk",
            "notice": "Bounded native query. Repeated doc_id rows and group count(*) count chunks, not patents. Concurrent updates can affect pagination."}

    def _count(self, s: dict) -> dict:
        rows = self.client.query(**self._common(s), filter=s.get("filter", ""), output_fields=["count(*)"])
        if not rows or "count(*)" not in rows[0]:
            raise ValueError("Milvus did not return count(*)")
        return {"count": int(rows[0]["count(*)"]), "count_unit": "chunks", "exact": True,
                "filter": s.get("filter", ""), "representative_filter_applied_automatically": False,
                "notice": "This is the exact matching row count visible to this query. Interpret it as a document count only when the caller explicitly used a currently valid representative-row filter with document-level metadata; describe provides any historically audited rule."}

    def _count_distinct(self, s: dict) -> dict:
        field = s.get("distinct_field", self.doc_id_field)
        if field != self.doc_id_field:
            raise ValueError(f"count_distinct supports {self.doc_id_field} (patent identity) only")
        started, scanned, unique = time.monotonic(), 0, 0
        iterator, reason = None, None
        with TemporaryDirectory(prefix="patent-distinct-") as directory:
            with closing(sqlite3.connect(Path(directory) / "ids.sqlite3")) as db:
                db.execute("PRAGMA cache_size=-4096")
                db.execute("CREATE TABLE ids (id TEXT PRIMARY KEY) WITHOUT ROWID")
                try:
                    iterator_options = self._common(s)
                    # Unlike query/search, query_iterator passes options straight to
                    # the RPC layer, which expects expr_params rather than filter_params.
                    if "filter_params" in iterator_options:
                        iterator_options["expr_params"] = iterator_options.pop("filter_params")
                    iterator = self.client.query_iterator(**iterator_options, filter=s.get("filter", ""),
                        output_fields=[field], batch_size=1000, limit=-1)
                    while True:
                        if time.monotonic() - started >= self.distinct_max_seconds:
                            reason = "time_budget"
                            break
                        batch = iterator.next()
                        if not batch:
                            break
                        remaining = self.distinct_max_rows - scanned
                        values = []
                        for row in batch[:remaining]:
                            value = row.get(field)
                            if not isinstance(value, str) or not value:
                                raise ValueError("Cannot count malformed document identities")
                            values.append((value,))
                        before = db.total_changes
                        db.executemany("INSERT OR IGNORE INTO ids VALUES (?)", values)
                        unique += db.total_changes - before
                        scanned += len(values)
                        if len(batch) > remaining:
                            reason = "row_budget"
                            break
                finally:
                    if iterator is not None:
                        with suppress(Exception):
                            iterator.close()
        return {"exact": reason is None, "status": "incomplete" if reason else "complete",
            "count": None if reason else unique, "lower_bound": unique if reason else None,
            "count_unit": "unique_doc_id", "reason": reason, "scanned_chunks": scanned,
            "filter": s.get("filter", ""), "method": "disk_distinct_iterator",
            "notice": "Exact only after iterator exhaustion; bounded scan, not a top-k count. Reflects iterator-visible data."}

    def _embed(self, text: str) -> list[float]:
        if self.embedding_url:
            req = Request(self.embedding_url, data=json.dumps({"inputs": self.embedding_query_prompt + text,
                "dimensions": self.embedding_dimension, "normalize": True, "truncate": False}).encode(),
                headers={"Content-Type": "application/json"})
            with urlopen(req, timeout=self.timeout) as response:
                value = json.loads(response.read(1_000_001))
            if isinstance(value, list) and len(value) == 1 and isinstance(value[0], list):
                value = value[0]
        else:
            if self.embedder is None:
                if not self.embedding_model:
                    raise ValueError("Set EMBEDDING_MODEL or EMBEDDING_URL to the SAME model used during ingestion")
                from sentence_transformers import SentenceTransformer
                self.embedder = SentenceTransformer(self.embedding_model, device=self.device, trust_remote_code=True)
            kwargs = {"prompt": self.embedding_query_prompt} if self.embedding_query_prompt else {}
            value = self.embedder.encode(text, normalize_embeddings=self.metric_type in {"IP", "COSINE"},
                                         convert_to_numpy=True, **kwargs)
            if hasattr(value, "tolist"):
                value = value.tolist()
        dimension = int(self._fields()[self.vector_field].get("params", {}).get("dim", self.embedding_dimension))
        if not isinstance(value, list) or len(value) != dimension:
            raise ValueError(f"Embedding dimension must equal collection dimension {dimension}; do not mix models")
        if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in value):
            raise ValueError("Embedding must contain finite numbers")
        if not any(value):
            raise ValueError("Embedding must be nonzero")
        return value

    def _collection_indexes(self, refresh: bool = False) -> list[dict] | None:
        if refresh or self._indexes is None:
            try:
                names = self.client.list_indexes(collection_name=self.collection_name, timeout=self.timeout)
                self._indexes = [self.client.describe_index(collection_name=self.collection_name,
                    index_name=name, timeout=self.timeout) for name in names]
                self._index_inspection_error = None
            except Exception as exc:
                # Restricted read accounts/older adapters may not expose index
                # metadata. Do not claim that unknown means either ready or absent.
                self._indexes = None
                self._index_inspection_error = "Index inspection failed; check backend configuration."
        return self._indexes

    def _bm25_capability(self) -> dict:
        candidates = []
        fields = self._fields()
        for function in self._collection_schema().get("functions", []):
            kind = function.get("type", function.get("function_type", ""))
            if kind != 1 and "BM25" not in str(kind).upper():
                continue
            for output in function.get("output_field_names", []):
                dtype = fields.get(output, {}).get("type")
                if dtype == 104 or "SPARSE_FLOAT_VECTOR" in str(dtype).upper():
                    candidates.append(output)
        candidates = list(dict.fromkeys(candidates))
        sparse = self.sparse_field or (candidates[0] if len(candidates) == 1 else None)
        configured = sparse in candidates
        # _describe or a BM25 search populates index metadata; dense/query need
        # neither index introspection nor a configured sparse field.
        matched = [i for i in self._indexes or [] if i.get("field_name") == sparse
                   and str(i.get("metric_type", "")).upper() == "BM25"]
        indexed = bool(matched) if self._indexes is not None else None
        return {"sparse_field": sparse, "candidate_sparse_fields": candidates,
            "schema_configured": configured, "bm25_index_present": indexed,
            "status": "ambiguous_sparse_field" if not sparse and len(candidates) > 1 else
                      "missing_schema" if not configured else "missing_index" if indexed is False else
                      "index_unverified" if indexed is None else "configured",
            "notice": "Search still depends on collection load state and server/index support. An index alone is insufficient without an analyzer-enabled text field and BM25 function."}

    def _has_bm25(self) -> bool:
        return self._bm25_capability()["schema_configured"]

    def _search(self, s: dict) -> dict:
        query = s.get("query_text", "")
        if not isinstance(query, str) or not query.strip() or len(query) > 16000:
            raise ValueError("search requires query_text of 1..16000 characters")
        mode = s.get("mode", "dense")
        if mode not in ("dense", "bm25", "hybrid"):
            raise ValueError("mode must be dense, bm25 or hybrid")
        final = integer(s.get("limit", self.default_final_docs), "limit", 1, 50)
        recall = integer(s.get("recall_docs", max(self.default_recall_docs, final * 4)), "recall_docs", final, 500)
        group_size = integer(s.get("group_size", self.default_group_size), "group_size", 1, 5)
        if s.get("group_by_field", self.doc_id_field) != self.doc_id_field:
            raise ValueError("Search groups by the configured document identity field")
        sparse_field = self.sparse_field
        if mode != "dense":
            capability = self._bm25_capability()
            if not capability["schema_configured"] or capability["bm25_index_present"] is not True:
                # Retry missing/unknown capabilities so a running tool sees a
                # newly added index. Confirmed metadata is reused until describe.
                self._collection_schema(refresh=True)
                self._collection_indexes(refresh=True)
                capability = self._bm25_capability()
            if not capability["schema_configured"] or capability["bm25_index_present"] is False:
                raise ValueError(f"BM25 unavailable ({capability['status']}): select SPARSE_FIELD when ambiguous; the collection needs analyzer-enabled text, a BM25 function, sparse output and BM25 index. Use describe, dense or native scalar filters.")
            sparse_field = capability["sparse_field"]
        rerank = s.get("rerank", bool(self.reranker is not None or self.reranker_model))
        if rerank and self.reranker is None and not self.reranker_model:
            raise ValueError("rerank=true requires RERANKER_MODEL")
        fields = self._projection(s, search=True, rerank=rerank)
        params = s.get("search_params", {"metric_type": self.metric_type, "params": {}})
        sparse_params = s.get("sparse_search_params", {"metric_type": "BM25", "params": {}})
        vector = self._embed(query.strip()) if mode != "bm25" else None
        common = self._common(s)
        if mode == "hybrid":
            from pymilvus import AnnSearchRequest, RRFRanker
            common.pop("filter_params", None)
            requests = [AnnSearchRequest(data=[data], anns_field=field, param=param,
                limit=recall * group_size, expr=s.get("filter", ""),
                **({"expr_params": s["filter_params"]} if "filter_params" in s else {}))
                for data, field, param in ((vector, self.vector_field, params), (query, sparse_field, sparse_params))]
            raw = self.client.hybrid_search(**common, reqs=requests, ranker=RRFRanker(),
                limit=recall, output_fields=fields, group_by_field=self.doc_id_field,
                group_size=group_size, strict_group_size=False)
        else:
            raw = self.client.search(**common, data=[query if mode == "bm25" else vector],
                anns_field=sparse_field if mode == "bm25" else self.vector_field,
                search_params=sparse_params if mode == "bm25" else params,
                filter=s.get("filter", ""), limit=recall, output_fields=fields,
                group_by_field=self.doc_id_field, group_size=group_size, strict_group_size=False)
        hits = []
        for hit in raw[0] if raw else []:
            row = dict(hit.get("entity") or {})
            row.update(_pk=hit.get("id"), _distance=hit.get("distance"))
            hits.append(row)
        if rerank and hits:
            if self.reranker is None:
                if not self.reranker_model:
                    raise ValueError("rerank=true requires RERANKER_MODEL")
                from sentence_transformers import CrossEncoder
                self.reranker = CrossEncoder(self.reranker_model, device=self.device)
            pairs = []
            for hit in hits:
                content = hit.get(self.text_field)
                if not isinstance(content, str) or not content.strip():
                    raise ValueError(f"Hit is missing rerank text {self.text_field}")
                pairs.append((query, content[:self.rerank_max_chars]))
            scores = self.reranker.predict(pairs, batch_size=self.rerank_batch_size, show_progress_bar=False)
            if hasattr(scores, "reshape"):
                scores = scores.reshape(-1).tolist()
            if len(scores) != len(hits) or not all(math.isfinite(float(x)) for x in scores):
                raise ValueError("Reranker returned an invalid score count or nonfinite score")
            for hit, score in zip(hits, scores):
                hit["_score"] = float(score)
        else:
            for hit in hits:
                hit["_score"] = float(hit["_distance"])
                if mode == "dense" and str(params.get("metric_type", self.metric_type)).upper() == "L2":
                    hit["_score"] *= -1
        grouped: dict[str, list[dict]] = defaultdict(list)
        for hit in hits:
            doc_id = hit.get(self.doc_id_field)
            if not isinstance(doc_id, str) or not doc_id:
                raise ValueError("Search hit missing document identity")
            grouped[doc_id].append(hit)
        for chunks in grouped.values():
            chunks.sort(key=lambda h: h["_score"], reverse=True)
        ranked = sorted(grouped.items(), key=lambda item: item[1][0]["_score"], reverse=True)[:final]
        results, excerpt_budget = [], 24000
        for doc_id, chunks in ranked:
            best = chunks[0]
            matched_chunks = []
            for hit in chunks:
                content = hit.get(self.text_field)
                content = content if isinstance(content, str) else None
                excerpt = content[:min(1200, excerpt_budget)] if content is not None else None
                excerpt_budget -= len(excerpt or "")
                matched_chunks.append({"chunk_uid": hit.get("chunk_uid", hit["_pk"]),
                    "section_title": hit.get("section_title"), "score": hit["_score"],
                    "milvus_distance": hit["_distance"], "content": excerpt,
                    "content_total_chars": len(content) if content is not None else None,
                    "content_truncated": content is not None and len(excerpt) < len(content)})
            result = {self.doc_id_field: doc_id, self.title_field: best.get(self.title_field),
                self.publication_number_field: best.get(self.publication_number_field),
                "score": best["_score"], "full_text": self._reference(best),
                "matched_chunks": matched_chunks}
            for field in s.get("output_fields", []):
                if field not in result and field not in {self.text_field, "content", "search_text", "chunk_metadata"}:
                    result[field] = best.get(field)
            results.append(result)
        return {"results": results, "query_text": query, "filter": s.get("filter", ""),
            "retrieval": {"mode": mode, "embedding": mode != "bm25", "reranked": rerank and bool(hits),
                "recall_docs": recall, "group_size": group_size, "returned_patents": min(len(results), final),
                "score_order": "higher_is_better", "exhaustive": False,
                "snippet_chars_per_chunk": 1200, "snippet_chars_total_limit": 24000,
                "snippet_chars_returned": 24000 - excerpt_budget, "snippet_selection": "chunk_prefix"},
            "notice": "Ranked candidates with bounded chunk excerpts. Top-k is not a corpus count. Read full text before using formulations, processes, performance, examples or claims as technical evidence."}

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(k): PatentMilvusTool._jsonable(v) for k, v in value.items()}
        # Milvus function schemas may contain protobuf repeated-field containers.
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [PatentMilvusTool._jsonable(v) for v in value]
        if hasattr(value, "tolist"):
            return value.tolist()
        return value


def build_from_env() -> PatentMilvusTool:
    collection = os.environ.get("MILVUS_COLLECTION", "").strip()
    if not collection:
        raise ValueError("Configure MILVUS_COLLECTION explicitly")
    root = os.environ.get("PATENT_MD_ROOT")
    storage = os.environ.get("PATENT_MD_STORAGE", "local" if root else "none")
    if storage not in ("local", "tos", "none"):
        raise ValueError("PATENT_MD_STORAGE must be local, tos or none")
    if storage == "local" and not root:
        raise ValueError("Local document storage requires PATENT_MD_ROOT")
    documents = tos_store_from_env() if storage == "tos" else (
        DocumentStore(root, os.environ.get("PATENT_MD_TEMPLATE", "{doc_id}.md")) if storage == "local" and root else None)
    return PatentMilvusTool(
        milvus_uri=os.environ.get("MILVUS_URI", "http://127.0.0.1:19530"),
        milvus_token=os.environ.get("MILVUS_TOKEN"),
        milvus_database=os.environ.get("MILVUS_DATABASE", "default"),
        collection_name=collection,
        embedding_model=os.environ.get("EMBEDDING_MODEL"), embedding_url=os.environ.get("EMBEDDING_URL"),
        embedding_dimension=int(os.environ.get("EMBEDDING_DIMENSION", "1024")),
        embedding_query_prompt=os.environ.get("EMBEDDING_QUERY_PROMPT", ""),
        reranker_model=os.environ.get("RERANKER_MODEL"),
        vector_field=os.environ.get("VECTOR_FIELD", "embedding"), text_field=os.environ.get("TEXT_FIELD", "content"),
        doc_id_field=os.environ.get("DOC_ID_FIELD", "doc_id"), title_field=os.environ.get("TITLE_FIELD", "title"),
        publication_number_field=os.environ.get("PUBLICATION_NUMBER_FIELD", "publication_number"),
        source_path_field=os.environ.get("SOURCE_PATH_FIELD", "source_path"), sparse_field=os.environ.get("SPARSE_FIELD", ""),
        metric_type=os.environ.get("METRIC_TYPE", "COSINE"), device=os.environ.get("MODEL_DEVICE"),
        timeout=float(os.environ.get("MILVUS_TIMEOUT", "60")),
        distinct_max_rows=int(os.environ.get("DISTINCT_MAX_ROWS", "1000000")),
        distinct_max_seconds=float(os.environ.get("DISTINCT_MAX_SECONDS", "60")),
        document_store=documents)
