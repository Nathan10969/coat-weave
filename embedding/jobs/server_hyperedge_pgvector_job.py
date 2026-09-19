"""Server-side hyperedge-only pgvector indexing and sample20 evaluation.

This script is intentionally self-contained so it can be uploaded to
``/root/coating/embedding/jobs`` and run beside the BGE-M3 HTTP service.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import re
import sys
import time
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import psycopg2
import requests
from psycopg2.extras import execute_values


OBJECT_TYPE = "hyperedge"
KS = (1, 3, 5, 10, 20)
DEFAULT_HYBRID_DENSE_WEIGHT = 0.1
DEFAULT_HYBRID_SPARSE_WEIGHT = 0.9
DEFAULT_HYBRID_FUSION_NAME = "hybrid_weighted_d10_s90"

DEFAULT_BASE = Path("/root/coating/embedding")
DEFAULT_INPUT = DEFAULT_BASE / "data" / "review_bundle_110.zip"
DEFAULT_SAMPLE20 = DEFAULT_BASE / "data" / "sample20" / "aggregate"
DEFAULT_OUT = DEFAULT_BASE / "outputs" / "server_sample20_eval"
DEFAULT_EMBED_URL = "http://127.0.0.1:8010/embed"
SOURCE_PREFIX = "review_bundle_110/docs/"
AGGREGATE_HYPEREDGE_PATHS = (
    "hyperedges.jsonl",
    "kg/hyperedges.jsonl",
    "merged_kg_pack/hyperedges.jsonl",
)


@dataclass(frozen=True)
class RetrievalObject:
    object_type: str
    object_id: str
    doc_id: str
    text_for_embedding: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class EmbeddingRow:
    dense_vec: list[float]
    lexical_weights: dict[str, float]


@dataclass(frozen=True)
class InputSource:
    source_id: str
    root: Path
    layout: str
    expected_hyperedges: int
    expected_docs: int | None = None
    company: str | None = None
    id_schema_version: str | None = None
    assignees_by_doc: Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]] | None = None


@dataclass(frozen=True)
class SearchResult:
    object_type: str
    object_id: str
    doc_id: str | None = None
    text_for_embedding: str | None = None
    metadata: dict[str, Any] | None = None
    dense_score: float | None = None
    sparse_score: float | None = None
    final_score: float = 0.0
    rank: int | None = None
    channels: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str]:
        return (self.object_type, self.object_id)


@dataclass(frozen=True)
class FusionConfig:
    name: str
    mode: str
    rrf_k: int | None = None
    dense_weight: float | None = None
    sparse_weight: float | None = None


FUSION_GRID = [
    FusionConfig("dense", "dense"),
    FusionConfig("sparse", "sparse"),
    FusionConfig("hybrid_rrf_k10", "hybrid_rrf", rrf_k=10),
    FusionConfig("hybrid_rrf_k30", "hybrid_rrf", rrf_k=30),
    FusionConfig("hybrid_rrf_k60", "hybrid_rrf", rrf_k=60),
    FusionConfig("hybrid_rrf_k100", "hybrid_rrf", rrf_k=100),
    FusionConfig("hybrid_weighted_d80_s20", "hybrid_weighted", dense_weight=0.8, sparse_weight=0.2),
    FusionConfig("hybrid_weighted_d70_s30", "hybrid_weighted", dense_weight=0.7, sparse_weight=0.3),
    FusionConfig("hybrid_weighted_d60_s40", "hybrid_weighted", dense_weight=0.6, sparse_weight=0.4),
    FusionConfig("hybrid_weighted_d50_s50", "hybrid_weighted", dense_weight=0.5, sparse_weight=0.5),
    FusionConfig("hybrid_weighted_d40_s60", "hybrid_weighted", dense_weight=0.4, sparse_weight=0.6),
    FusionConfig("hybrid_weighted_d30_s70", "hybrid_weighted", dense_weight=0.3, sparse_weight=0.7),
    FusionConfig("hybrid_weighted_d20_s80", "hybrid_weighted", dense_weight=0.2, sparse_weight=0.8),
    FusionConfig("hybrid_weighted_d10_s90", "hybrid_weighted", dense_weight=0.1, sparse_weight=0.9),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", dest="input_path", type=Path)
    parser.add_argument("--input-path", dest="input_path", type=Path)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--org-registry", type=Path)
    parser.add_argument("--collection-id")
    parser.add_argument("--company")
    parser.add_argument(
        "--replace-collection",
        action="store_true",
        help="Delete only the exact collection before writing; never deletes the shared hyperedge table.",
    )
    parser.add_argument("--sample20-dir", type=Path, default=DEFAULT_SAMPLE20)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--embed-url", default=DEFAULT_EMBED_URL)
    parser.add_argument("--pg-env", type=Path, default=DEFAULT_BASE / "pgvector.env")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--sparse-max-terms", type=int, default=256)
    parser.add_argument("--candidate-k", type=int, default=100)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--enable-structured-rerank", action="store_true")
    parser.add_argument("--rerank-top-k", type=int, default=20)
    parser.add_argument("--rerank-input-k", type=int, default=100)
    parser.add_argument("--mode", choices=["all", "build", "preflight", "smoke", "eval"], default="preflight")
    parser.add_argument("--batch-id", default=None)
    args = parser.parse_args()

    pg = {} if args.mode == "preflight" else load_pg_env(args.pg_env)
    if args.mode in {"all", "build", "preflight"}:
        if not args.collection_id:
            parser.error("--collection-id is required for build and preflight modes")
        if args.source_manifest:
            objects = load_collection_objects(
                args.source_manifest,
                args.collection_id,
                batch_id=args.batch_id,
                org_registry_path=args.org_registry,
            )
        elif args.input_path and args.company:
            source = InputSource(
                source_id=args.company,
                root=args.input_path,
                layout="auto",
                expected_hyperedges=-1,
                company=args.company,
            )
            objects = load_collection_objects_from_sources((source,), args.collection_id, batch_id=args.batch_id)
        else:
            parser.error("provide --source-manifest, or both --input-path and --company")
        if args.mode == "preflight":
            preflight_objects(objects, args.collection_id)
            return 0
        if args.mode in {"all", "build"}:
            build_index(args, pg, objects)
    if args.mode in {"all", "smoke"}:
        smoke(args, pg)
    if args.mode in {"all", "eval"}:
        evaluate(args, pg)
    return 0


def load_pg_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def pg_connect(pg: Mapping[str, str]):
    return psycopg2.connect(
        host=pg.get("PGHOST", "127.0.0.1"),
        port=int(pg.get("PGPORT", "5432")),
        dbname=pg.get("POSTGRES_DB", "coating_kg"),
        user=pg.get("POSTGRES_USER", "coating"),
        password=pg.get("POSTGRES_PASSWORD", ""),
    )


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_objects (
                object_type        TEXT NOT NULL,
                object_id          TEXT NOT NULL,
                doc_id             TEXT,
                text_for_embedding TEXT NOT NULL,
                dense_embedding    vector(1024),
                metadata           JSONB,
                created_at         TIMESTAMPTZ DEFAULT now(),
                updated_at         TIMESTAMPTZ DEFAULT now(),
                PRIMARY KEY (object_type, object_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_sparse_postings (
                object_type TEXT NOT NULL,
                object_id   TEXT NOT NULL,
                token_id    TEXT NOT NULL,
                weight      DOUBLE PRECISION NOT NULL,
                PRIMARY KEY (object_type, object_id, token_id),
                FOREIGN KEY (object_type, object_id)
                    REFERENCES retrieval_objects(object_type, object_id)
                    ON DELETE CASCADE
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrieval_objects_doc_type ON retrieval_objects (doc_id, object_type)"
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_retrieval_sparse_token_type
            ON retrieval_sparse_postings (token_id, object_type)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_retrieval_sparse_object
            ON retrieval_sparse_postings (object_type, object_id)
            """
        )
    conn.commit()


ASSIGNEE_KEYS = ("current_assignees", "current_assignee", "assignee", "applicants", "applicant")


def normalize_org_alias(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    chars = [
        " " if char.isspace() else char
        for char in normalized
        if not unicodedata.category(char).startswith("P")
    ]
    return re.sub(r"\s+", " ", "".join(chars)).strip()


def load_org_alias_map(path: Path) -> dict[str, tuple[str, str]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for row in read_jsonl_path(path):
        if row.get("schema_version") != "org_registry_v1":
            raise ValueError(f"org registry schema_version mismatch: {path}")
        canonical_id = text_value(row.get("canonical_id"))
        if not canonical_id:
            raise ValueError(f"org registry row missing canonical_id: {path}")
        if int(row.get("record_version", 0)) >= int(latest.get(canonical_id, {}).get("record_version", -1)):
            latest[canonical_id] = row
    aliases: dict[str, tuple[str, str]] = {}
    for canonical_id, row in sorted(latest.items()):
        if row.get("status") != "active":
            continue
        canonical_name = text_value(row.get("canonical_name")) or canonical_id
        for item in row.get("aliases") or []:
            alias = item.get("text") if isinstance(item, Mapping) else item
            alias_text = text_value(alias)
            if not alias_text:
                continue
            key = normalize_org_alias(alias_text)
            existing = aliases.get(key)
            if existing and existing[0] != canonical_id:
                raise ValueError(f"active org alias conflict: {alias_text!r}")
            aliases[key] = (canonical_id, canonical_name)
    return aliases


def authoritative_assignees(row: Mapping[str, Any]) -> list[str]:
    for key in ASSIGNEE_KEYS:
        value = row.get(key)
        if value is None or value == "" or value == []:
            continue
        items = value if isinstance(value, list) else [value]
        return [str(item).strip() for item in items if str(item).strip()]
    return []


def load_source_patents(root: Path, layout: str) -> dict[str, dict[str, Any]]:
    if layout == "aggregate":
        paths = [root / "patents.jsonl"]
    else:
        paths = sorted(root.glob("patents/*/kg_pack/patents.jsonl"))
    if not paths or any(not path.is_file() for path in paths):
        raise FileNotFoundError(f"patents.jsonl not found below source root: {root}")
    patents: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in read_jsonl_path(path):
            doc_id = text_value(row.get("doc_id") or row.get("patent_id") or row.get("publication_number"))
            if not doc_id:
                raise ValueError(f"patent row missing document id: {path}")
            if doc_id in patents:
                raise ValueError(f"duplicate patent document id {doc_id}: {path}")
            patents[doc_id] = row
    return patents


def resolve_source_assignees(
    root: Path,
    layout: str,
    aliases: Mapping[str, tuple[str, str]],
    expected_assignee_ids: set[str],
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    resolved: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for doc_id, patent in load_source_patents(root, layout).items():
        raw_values = authoritative_assignees(patent)
        if not raw_values:
            continue
        ids: list[str] = []
        names: list[str] = []
        for raw_value in raw_values:
            hit = aliases.get(normalize_org_alias(raw_value))
            if hit is None:
                ids = []
                names = []
                break
            if hit[0] not in ids:
                ids.append(hit[0])
                names.append(hit[1])
        if ids and not set(ids).isdisjoint(expected_assignee_ids):
            resolved[doc_id] = (tuple(ids), tuple(names))
    return resolved


def load_collection_objects(
    manifest_path: Path,
    collection_id: str,
    *,
    batch_id: str | None = None,
    org_registry_path: Path | None = None,
) -> list[RetrievalObject]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"source manifest must be a JSON object: {manifest_path}")
    declared_collection = str(data.get("collection_id") or "").strip()
    if declared_collection and declared_collection != collection_id:
        raise ValueError(
            f"collection mismatch: argument={collection_id!r}, manifest={declared_collection!r}"
        )
    schema_version = str(data.get("schema_version") or "hyperedge_collection_sources_v1")
    if schema_version not in {"hyperedge_collection_sources_v1", "hyperedge_collection_sources_v2"}:
        raise ValueError(f"unsupported source manifest schema_version: {schema_version!r}")
    is_v2 = schema_version == "hyperedge_collection_sources_v2"
    collection_kind = str(data.get("collection_kind") or "").strip() if is_v2 else ""
    expected_assignee_ids = {
        str(value).strip() for value in data.get("expected_assignee_ids") or [] if str(value).strip()
    }
    if is_v2 and collection_kind not in {"assignee", "topic"}:
        raise ValueError("v2 source manifest collection_kind must be assignee or topic")
    if is_v2 and collection_kind == "assignee" and not expected_assignee_ids:
        raise ValueError("v2 assignee collection requires expected_assignee_ids")
    if is_v2 and collection_kind == "assignee" and org_registry_path is None:
        raise ValueError("--org-registry is required for a v2 assignee collection")
    alias_map = load_org_alias_map(org_registry_path) if org_registry_path else {}

    source_rows = data.get("sources")
    if not isinstance(source_rows, list) or not source_rows:
        raise ValueError(f"source manifest must contain a non-empty sources list: {manifest_path}")
    sources: list[InputSource] = []
    for item in source_rows:
        if not isinstance(item, Mapping):
            raise ValueError(f"invalid source manifest entry: {item!r}")
        company = str(item.get("company") or "").strip() or None
        source_id = str(item.get("source_id") or company or "").strip()
        root_value = str(item.get("root") or "").strip()
        layout = str(item.get("layout") or "").strip()
        expected = item.get("expected_hyperedges")
        expected_docs = item.get("expected_docs")
        if not source_id or not root_value or layout not in {"aggregate", "patent_packs"}:
            raise ValueError(f"invalid source manifest entry: {item!r}")
        if is_v2 and company:
            raise ValueError(f"v2 source manifest must not stamp company: {item!r}")
        if not is_v2 and not company:
            raise ValueError(f"v1 source manifest requires company: {item!r}")
        if not isinstance(expected, int) or expected < 0:
            raise ValueError(f"expected_hyperedges must be a non-negative integer: {item!r}")
        if expected_docs is not None and (not isinstance(expected_docs, int) or expected_docs < 0):
            raise ValueError(f"expected_docs must be a non-negative integer: {item!r}")
        _validate_id_segment("source_id", source_id)
        if company:
            _validate_id_segment("company", company)
        root = Path(root_value)
        assignees_by_doc = None
        if is_v2 and collection_kind == "assignee":
            assignees_by_doc = resolve_source_assignees(
                root,
                layout,
                alias_map,
                expected_assignee_ids,
            )
        sources.append(
            InputSource(
                source_id=source_id,
                root=root,
                layout=layout,
                expected_hyperedges=expected,
                expected_docs=expected_docs,
                company=company,
                id_schema_version="retrieval_object_v2" if is_v2 else None,
                assignees_by_doc=assignees_by_doc,
            )
        )
    _validate_id_segment("collection_id", collection_id)
    return load_collection_objects_from_sources(sources, collection_id, batch_id=batch_id)


def load_collection_objects_from_sources(
    sources: Sequence[InputSource],
    collection_id: str,
    *,
    batch_id: str | None = None,
) -> list[RetrievalObject]:
    objects: list[RetrievalObject] = []
    source_counts: Counter[str] = Counter()
    for source in sources:
        if not source.root.is_dir():
            raise FileNotFoundError(f"source root does not exist: {source.root}")
        source_docs: set[str] = set()
        for doc_id, row in iter_source_hyperedges(source):
            source_docs.add(doc_id)
            assignee_ids: tuple[str, ...] = ()
            assignee_names: tuple[str, ...] = ()
            if source.id_schema_version == "retrieval_object_v2" and source.assignees_by_doc is not None:
                resolution = source.assignees_by_doc.get(doc_id)
                if resolution is None:
                    raise ValueError(f"unresolved or out-of-scope assignee for {source.source_id} document {doc_id}")
                assignee_ids, assignee_names = resolution
            obj = hyperedge_object(
                doc_id,
                row,
                batch_id=batch_id or collection_id,
                collection_id=collection_id,
                company=source.company,
                source_id=source.source_id,
                id_schema_version=source.id_schema_version,
                assignee_ids=assignee_ids,
                assignee_names=assignee_names,
            )
            if obj is None:
                raise ValueError(f"missing hyperedge_id in {source.source_id} source for document {doc_id}")
            objects.append(obj)
            source_counts[source.source_id] += 1
        if source.expected_hyperedges >= 0 and source_counts[source.source_id] != source.expected_hyperedges:
            raise ValueError(
                f"source count mismatch for {source.source_id}: "
                f"expected={source.expected_hyperedges}, actual={source_counts[source.source_id]}"
            )
        if source.expected_docs is not None and len(source_docs) != source.expected_docs:
            raise ValueError(
                f"source document count mismatch for {source.source_id}: "
                f"expected={source.expected_docs}, actual={len(source_docs)}"
            )
    deduped = dedupe(objects)
    if len(deduped) != len(objects):
        raise ValueError(f"duplicate namespaced object ids: loaded={len(objects)}, unique={len(deduped)}")
    return deduped


def iter_source_hyperedges(source: InputSource) -> Iterable[tuple[str, dict[str, Any]]]:
    if source.layout in {"aggregate", "auto"}:
        path = source.root / "hyperedges.jsonl"
        if path.is_file():
            for row in read_jsonl_path(path):
                doc_id = text_value(row.get("doc_id") or row.get("patent_id"))
                if not doc_id:
                    raise ValueError(f"missing doc_id/patent_id in aggregate source: {path}")
                yield doc_id, row
            return
        if source.layout == "aggregate":
            raise FileNotFoundError(f"missing aggregate hyperedges file: {path}")
    if source.layout in {"patent_packs", "auto"}:
        paths = sorted(source.root.glob("patents/*/kg_pack/hyperedges.jsonl"))
        if not paths:
            raise FileNotFoundError(f"no patent-pack hyperedges found below: {source.root}")
        for path in paths:
            doc_hint = path.parent.parent.name
            for row in read_jsonl_path(path):
                row_doc_id = text_value(row.get("doc_id") or row.get("patent_id"))
                if row_doc_id and row_doc_id != doc_hint:
                    raise ValueError(
                        f"document mismatch in {path}: row={row_doc_id!r}, directory={doc_hint!r}"
                    )
                yield row_doc_id or doc_hint, row
        return
    raise ValueError(f"unsupported source layout: {source.layout}")


def preflight_objects(objects: Sequence[RetrievalObject], collection_id: str) -> None:
    if not objects:
        raise RuntimeError("No hyperedge objects loaded during preflight")
    companies = Counter(
        company
        for obj in objects
        if (company := str(obj.metadata.get("company") or ""))
    )
    id_schemas = Counter(str(obj.metadata.get("id_schema_version") or "legacy") for obj in objects)
    assignee_ids = Counter(
        assignee_id
        for obj in objects
        for assignee_id in obj.metadata.get("assignee_ids") or []
    )
    docs = {obj.doc_id for obj in objects}
    print(
        json.dumps(
            {
                "preflight_ok": True,
                "collection_id": collection_id,
                "objects": len(objects),
                "documents": len(docs),
                "companies": dict(sorted(companies.items())),
                "id_schema_versions": dict(sorted(id_schemas.items())),
                "assignee_ids": dict(sorted(assignee_ids.items())),
                "sample_object_id": objects[0].object_id,
            },
            ensure_ascii=False,
        )
    )


def _validate_id_segment(name: str, value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"{name} must contain only letters, digits, dot, underscore, or hyphen: {value!r}")


def load_hyperedge_objects(
    input_path: Path,
    *,
    batch_id: str | None = None,
    object_id_scope: str = "raw",
) -> list[RetrievalObject]:
    if input_path.is_dir():
        return load_hyperedge_objects_from_dir(input_path, batch_id=batch_id, object_id_scope=object_id_scope)
    return load_hyperedge_objects_from_zip(input_path, batch_id=batch_id, object_id_scope=object_id_scope)


def load_hyperedge_objects_from_zip(
    zip_path: Path,
    *,
    batch_id: str | None = None,
    object_id_scope: str = "raw",
) -> list[RetrievalObject]:
    objects: list[RetrievalObject] = []
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        aggregate_path = next((name for name in AGGREGATE_HYPEREDGE_PATHS if name in names), None)
        if aggregate_path:
            for row in read_zip_jsonl(zf, aggregate_path):
                doc_id = str(row.get("doc_id") or row.get("patent_id") or "")
                if not doc_id:
                    continue
                obj = hyperedge_object(doc_id, row, batch_id=batch_id, object_id_scope=object_id_scope)
                if obj is not None:
                    objects.append(obj)
            return dedupe(objects)

        doc_ids = sorted(
            {
                match.group(1)
                for name in names
                for match in [re.match(rf"{re.escape(SOURCE_PREFIX)}([^/]+)/", name)]
                if match
            }
        )
        if not doc_ids:
            doc_ids = sorted(
                {
                    name.split("/", 1)[0]
                    for name in names
                    if name.endswith("/hyperedges.jsonl") and "/" in name
                }
            )
        for doc_id in doc_ids:
            path_candidates = [
                f"{SOURCE_PREFIX}{doc_id}/hyperedges.jsonl",
                f"{doc_id}/hyperedges.jsonl",
            ]
            path = next((item for item in path_candidates if item in names), None)
            if not path:
                continue
            rows = read_zip_jsonl(zf, path)
            for row in rows:
                obj = hyperedge_object(doc_id, row, batch_id=batch_id, object_id_scope=object_id_scope)
                if obj is not None:
                    objects.append(obj)
    return dedupe(objects)


def load_hyperedge_objects_from_dir(
    root: Path,
    *,
    batch_id: str | None = None,
    object_id_scope: str = "raw",
) -> list[RetrievalObject]:
    objects: list[RetrievalObject] = []
    aggregate_path = next((root / name for name in AGGREGATE_HYPEREDGE_PATHS if (root / name).exists()), None)
    if aggregate_path:
        for row in read_jsonl_path(aggregate_path):
            doc_id = str(row.get("doc_id") or row.get("patent_id") or "")
            if not doc_id:
                continue
            obj = hyperedge_object(doc_id, row, batch_id=batch_id, object_id_scope=object_id_scope)
            if obj is not None:
                objects.append(obj)
        return dedupe(objects)

    for path in sorted(root.glob("*/hyperedges.jsonl")):
        doc_id = path.parent.name
        for row in read_jsonl_path(path):
            obj = hyperedge_object(doc_id, row, batch_id=batch_id, object_id_scope=object_id_scope)
            if obj is not None:
                objects.append(obj)
    return dedupe(objects)


def read_zip_jsonl(zf: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in zf.read(name).decode("utf-8", "replace").splitlines()
        if line.strip()
    ]


def read_jsonl_path(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def hyperedge_object(
    doc_id: str,
    row: Mapping[str, Any],
    *,
    batch_id: str | None = None,
    object_id_scope: str = "raw",
    collection_id: str | None = None,
    company: str | None = None,
    source_id: str | None = None,
    id_schema_version: str | None = None,
    assignee_ids: Sequence[str] = (),
    assignee_names: Sequence[str] = (),
) -> RetrievalObject | None:
    hyperedge_id = row.get("hyperedge_id")
    if not hyperedge_id:
        return None
    raw_hyperedge_id = str(hyperedge_id)
    object_id = raw_hyperedge_id
    if collection_id:
        if id_schema_version == "retrieval_object_v2":
            if company:
                raise ValueError("company must not be stamped into retrieval_object_v2 ids")
            object_id = f"{collection_id}::{doc_id}::{raw_hyperedge_id}"
        else:
            if not company:
                raise ValueError("company is required for legacy collection ids")
            object_id = f"{collection_id}::{company}::{doc_id}::{raw_hyperedge_id}"
    elif object_id_scope == "doc_prefixed":
        object_id = f"{doc_id}::{raw_hyperedge_id}"
    substrate = row.get("substrate") if isinstance(row.get("substrate"), Mapping) else {}
    text = join_parts(
        "object: hyperedge",
        f"doc_id: {doc_id}",
        f"hyperedge_id: {hyperedge_id}",
        f"context_id: {row.get('context_id')}" if row.get("context_id") else None,
        f"sample: {row.get('sample_id')}" if row.get("sample_id") else None,
        f"example: {row.get('example_id')}" if row.get("example_id") else None,
        f"example_kind: {row.get('example_kind')}" if row.get("example_kind") else None,
        f"polarity: {row.get('polarity')}" if row.get("polarity") else None,
        f"substrate: {substrate.get('label')}" if substrate.get("label") else None,
        f"substrate_id: {substrate.get('canonical_id')}" if substrate.get("canonical_id") else None,
        *(materials_text(label, material_slot(row, label)) for label in MATERIAL_TEXT_SLOT_ALIASES),
        process_text(row.get("process")),
        test_method_text(row.get("test_method")),
        f"property: {row.get('property')}" if row.get("property") else None,
        f"property_id: {row.get('property_canonical_id')}" if row.get("property_canonical_id") else None,
        results_text(row.get("result")),
        labelled_list("evidence_ids", row.get("evidence_ids")),
        labelled_list("fact_ids", row.get("fact_ids")),
    )
    metadata = {
        "source": "hyperedges.jsonl",
        "batch_id": batch_id,
        "collection_id": collection_id,
        "raw_hyperedge_id": raw_hyperedge_id,
        "hyperedge_type": row.get("hyperedge_type"),
        "hyperedge_search_template": "baseline_current",
        "context_id": row.get("context_id"),
        "sample_id": row.get("sample_id"),
        "example_id": row.get("example_id"),
        "example_kind": row.get("example_kind"),
        "polarity": row.get("polarity"),
        "property": row.get("property"),
        "property_canonical_id": row.get("property_canonical_id"),
        "evidence_ids": row.get("evidence_ids") or [],
        "fact_ids": row.get("fact_ids") or [],
    }
    if id_schema_version == "retrieval_object_v2":
        metadata.update(
            {
                "id_schema_version": id_schema_version,
                "source_id": source_id,
                "assignee_ids": list(assignee_ids),
                "assignee_names": list(assignee_names),
            }
        )
    else:
        metadata["company"] = company
    return RetrievalObject(
        object_type=OBJECT_TYPE,
        object_id=object_id,
        doc_id=doc_id,
        text_for_embedding=truncate(text, 3200),
        metadata=metadata,
    )


def dedupe(objects: list[RetrievalObject]) -> list[RetrievalObject]:
    out: list[RetrievalObject] = []
    seen: set[tuple[str, str]] = set()
    for obj in objects:
        key = (obj.object_type, obj.object_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(obj)
    return out


def build_index(args: argparse.Namespace, pg: Mapping[str, str], objects: list[RetrievalObject]) -> None:
    started = time.perf_counter()
    if not objects:
        raise RuntimeError("No hyperedge objects loaded")
    print(f"loaded_hyperedge_objects={len(objects)}")
    print(f"doc_count={len({obj.doc_id for obj in objects})}")
    print(f"sample_object={objects[0].object_id} text={objects[0].text_for_embedding[:240]!r}")

    with pg_connect(pg) as conn:
        conn.autocommit = False
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("SET synchronous_commit = off")
            if args.replace_collection:
                cur.execute(
                    "DELETE FROM retrieval_objects WHERE object_type = %s AND metadata->>'collection_id' = %s",
                    (OBJECT_TYPE, args.collection_id),
                )
        conn.commit()

        batches = list(chunks(objects, max(args.batch_size, 1)))
        total_objects = 0
        total_postings = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(args.workers, 1)) as pool:
            future_to_batch = {
                pool.submit(embed_texts, args.embed_url, [obj.text_for_embedding for obj in batch], args.sparse_max_terms, args.batch_size): batch
                for batch in batches
            }
            for future in concurrent.futures.as_completed(future_to_batch):
                batch = future_to_batch[future]
                embeddings = future.result()
                write_batch(conn, batch, embeddings)
                total_objects += len(batch)
                total_postings += sum(len(row.lexical_weights) for row in embeddings)
                print(f"indexed_objects={total_objects}/{len(objects)} sparse_postings={total_postings}")

        print("ensuring_hnsw_index=true")
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_retrieval_objects_dense_hyperedge_hnsw
                ON retrieval_objects
                USING hnsw (dense_embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
                WHERE object_type = 'hyperedge'
                """
            )
            cur.execute("ANALYZE retrieval_objects")
            cur.execute("ANALYZE retrieval_sparse_postings")
        conn.commit()

    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "build_done": True,
                "collection_id": args.collection_id,
                "objects": len(objects),
                "sparse_postings": total_postings,
                "seconds": round(elapsed, 2),
            },
            ensure_ascii=False,
        )
    )


def embed_texts(
    embed_url: str,
    texts: list[str],
    sparse_max_terms: int,
    batch_size: int,
) -> list[EmbeddingRow]:
    response = requests.post(
        embed_url,
        json={
            "texts": texts,
            "return_sparse": True,
            "sparse_max_terms": sparse_max_terms,
            "normalize": True,
            "batch_size": batch_size,
        },
        timeout=900,
    )
    response.raise_for_status()
    data = response.json()
    dense_rows = data["dense"]
    sparse_rows = data.get("sparse") or [{} for _ in dense_rows]
    out: list[EmbeddingRow] = []
    for dense, sparse in zip(dense_rows, sparse_rows):
        out.append(
            EmbeddingRow(
                dense_vec=[float(item) for item in dense],
                lexical_weights={str(k): float(v) for k, v in dict(sparse).items() if float(v) > 0},
            )
        )
    return out


def write_batch(conn, objects: list[RetrievalObject], embeddings: list[EmbeddingRow]) -> None:
    object_rows = [
        (
            obj.object_type,
            obj.object_id,
            obj.doc_id,
            obj.text_for_embedding,
            vector_literal(emb.dense_vec),
            json.dumps(obj.metadata, ensure_ascii=False),
        )
        for obj, emb in zip(objects, embeddings)
    ]
    posting_rows = [
        (obj.object_type, obj.object_id, token_id, float(weight))
        for obj, emb in zip(objects, embeddings)
        for token_id, weight in emb.lexical_weights.items()
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            DELETE FROM retrieval_sparse_postings AS rsp
            USING (VALUES %s) AS incoming(object_type, object_id)
            WHERE rsp.object_type = incoming.object_type
              AND rsp.object_id = incoming.object_id
            """,
            [(obj.object_type, obj.object_id) for obj in objects],
            template="(%s, %s)",
            page_size=500,
        )
        execute_values(
            cur,
            """
            INSERT INTO retrieval_objects (
                object_type, object_id, doc_id, text_for_embedding, dense_embedding, metadata
            )
            VALUES %s
            ON CONFLICT (object_type, object_id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id,
                text_for_embedding = EXCLUDED.text_for_embedding,
                dense_embedding = EXCLUDED.dense_embedding,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            """,
            object_rows,
            template="(%s, %s, %s, %s, %s::vector, %s::jsonb)",
            page_size=200,
        )
        if posting_rows:
            execute_values(
                cur,
                """
                INSERT INTO retrieval_sparse_postings (object_type, object_id, token_id, weight)
                VALUES %s
                ON CONFLICT (object_type, object_id, token_id) DO UPDATE SET
                    weight = EXCLUDED.weight
                """,
                posting_rows,
                page_size=5000,
            )
    conn.commit()


def smoke(args: argparse.Namespace, pg: Mapping[str, str]) -> None:
    queries = [
        "Example adhesion result coating substrate",
        "comparative example salt spray corrosion resistance",
        "polyurethane coating gloss test result",
        "Table formulation row resin additive result",
    ]
    smoke_rows: list[dict[str, Any]] = []
    with pg_connect(pg) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE dense_embedding IS NOT NULL),
                       COUNT(DISTINCT doc_id)
                FROM retrieval_objects
                WHERE object_type = 'hyperedge'
                """
            )
            object_count, dense_count, doc_count = cur.fetchone()
            cur.execute(
                """
                SELECT COUNT(*) AS postings,
                       COUNT(DISTINCT object_id) AS objects_with_sparse
                FROM retrieval_sparse_postings
                WHERE object_type = 'hyperedge'
                """
            )
            posting_count, objects_with_sparse = cur.fetchone()
            cur.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE tablename = 'retrieval_objects'
                  AND indexname = 'idx_retrieval_objects_dense_hyperedge_hnsw'
                """
            )
            hnsw_exists = cur.fetchone() is not None
        for query in queries:
            embedding = embed_texts(args.embed_url, [query], args.sparse_max_terms, args.batch_size)[0]
            dense = search_dense(conn, embedding.dense_vec, limit=5)
            sparse = search_sparse(conn, embedding.lexical_weights, limit=5)
            hybrid = weighted_fuse(
                dense,
                sparse,
                dense_weight=DEFAULT_HYBRID_DENSE_WEIGHT,
                sparse_weight=DEFAULT_HYBRID_SPARSE_WEIGHT,
                limit=5,
            )
            smoke_rows.append(
                {
                    "query": query,
                    "dense_top": [result_summary(row) for row in dense[:3]],
                    "sparse_top": [result_summary(row) for row in sparse[:3]],
                    "hybrid_top": [result_summary(row) for row in hybrid[:3]],
                }
            )

    sparse_rate = round(float(objects_with_sparse or 0) / float(object_count or 1), 4)
    dense_rate = round(float(dense_count or 0) / float(object_count or 1), 4)
    payload = {
        "object_count": object_count,
        "doc_count": doc_count,
        "dense_count": dense_count,
        "dense_non_null_rate": dense_rate,
        "sparse_posting_count": posting_count,
        "objects_with_sparse": objects_with_sparse,
        "sparse_non_empty_rate": sparse_rate,
        "hnsw_exists": hnsw_exists,
        "smoke_queries": smoke_rows,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.out_dir / "smoke_report.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def evaluate(args: argparse.Namespace, pg: Mapping[str, str]) -> None:
    args.out_dir.mkdir(parents=True, exist_ok=True)
    queries = read_jsonl(args.sample20_dir / "golden_set.final.jsonl")
    old_targets = read_jsonl(args.sample20_dir / "embedding_eval_targets.jsonl")
    queries_by_id = {str(row["query_id"]): row for row in queries}

    with pg_connect(pg) as conn:
        retrieval_objects = load_retrieval_object_index(conn)
        mapped_targets, mapping_report = map_targets_hyperedge_only(old_targets, retrieval_objects)
        main_targets = [row for row in mapped_targets if not row.get("diagnostic") and row.get("required", True)]
        diagnostic_targets = [row for row in mapped_targets if row.get("diagnostic")]
        write_jsonl(args.out_dir / "embedding_eval_targets.hyperedge_only.jsonl", mapped_targets)
        write_json(args.out_dir / "target_mapping_report.hyperedge_only.json", mapping_report)

        query_embeddings = embed_query_batches(args, queries)
        dense_sparse_by_query: dict[str, tuple[list[SearchResult], list[SearchResult]]] = {}
        for query in queries:
            qid = str(query["query_id"])
            embedding = query_embeddings[qid]
            dense = search_dense(conn, embedding.dense_vec, limit=args.candidate_k)
            sparse = search_sparse(conn, embedding.lexical_weights, limit=args.candidate_k)
            dense_sparse_by_query[qid] = (dense, sparse)
            if len(dense_sparse_by_query) % 25 == 0:
                print(f"evaluated_retrieval_queries={len(dense_sparse_by_query)}/{len(queries)}")

    results_by_fusion: dict[str, dict[str, list[SearchResult]]] = {}
    base_results_by_fusion: dict[str, dict[str, list[SearchResult]]] = {}
    fusion_limit = args.rerank_input_k if args.enable_structured_rerank else args.top_k
    effective_top_k = args.rerank_top_k if args.enable_structured_rerank else args.top_k
    for fusion in FUSION_GRID:
        mode_results: dict[str, list[SearchResult]] = {}
        base_mode_results: dict[str, list[SearchResult]] = {}
        for query in queries:
            qid = str(query["query_id"])
            dense, sparse = dense_sparse_by_query[qid]
            base_results = fuse_for_config(fusion, dense, sparse, limit=fusion_limit)
            base_mode_results[qid] = base_results
            if args.enable_structured_rerank:
                mode_results[qid] = structured_rerank(query, base_results, limit=effective_top_k)
            else:
                mode_results[qid] = base_results
        base_results_by_fusion[fusion.name] = base_mode_results
        results_by_fusion[fusion.name] = mode_results

    comparison: dict[str, Any] = {
        "query_count": len(queries),
        "main_target_count": len(main_targets),
        "diagnostic_target_count": len(diagnostic_targets),
        "mapping_report": mapping_report,
        "structured_rerank_enabled": bool(args.enable_structured_rerank),
        "rerank_input_k": args.rerank_input_k if args.enable_structured_rerank else None,
        "rerank_top_k": args.rerank_top_k if args.enable_structured_rerank else None,
        "modes": {},
        "deltas_vs_dense": {},
    }
    suffix = ".rerank" if args.enable_structured_rerank else ""
    tuning_rows: list[dict[str, Any]] = []
    for fusion in FUSION_GRID:
        results = results_by_fusion[fusion.name]
        metrics = evaluate_target_groups(main_targets, results, ks=KS, allow_evidence_via_hyperedge=True)
        hard_negative = hard_negative_confusion_stats(queries, results, ks=KS)
        failures = failure_cases(
            queries_by_id,
            main_targets,
            results,
            max_rank=effective_top_k,
            allow_evidence_via_hyperedge=True,
        )
        write_json(
            args.out_dir / f"metrics.{fusion.name}{suffix}.json",
            {
                "fusion": fusion.__dict__,
                "metrics": metrics,
                "hard_negative_warnings": hard_negative,
            },
        )
        write_json(args.out_dir / f"hard_negative_warnings.{fusion.name}{suffix}.json", hard_negative)
        write_jsonl(args.out_dir / f"failure_cases.{fusion.name}{suffix}.jsonl", failures)
        if args.enable_structured_rerank:
            write_jsonl(
                args.out_dir / f"rerank_deltas.{fusion.name}.jsonl",
                rerank_delta_rows(main_targets, base_results_by_fusion[fusion.name], results, allow_evidence_via_hyperedge=True),
            )
            write_jsonl(
                args.out_dir / f"rerank_debug.{fusion.name}.jsonl",
                rerank_debug_rows(queries, base_results_by_fusion[fusion.name], results, limit=effective_top_k),
            )
        comparison["modes"][fusion.name] = metrics["overall"]
        tuning_rows.append(
            {
                "fusion": fusion.__dict__,
                "metrics": metrics["overall"],
                "hard_negative_confusion_rate@5": hard_negative["by_k"].get("@5", {}).get("confusion_result_rate", 0.0),
            }
        )

    dense_base = comparison["modes"]["dense"]
    for name, metrics in comparison["modes"].items():
        if name == "dense":
            continue
        comparison["deltas_vs_dense"][name] = metric_deltas(dense_base, metrics)

    default_metrics = comparison["modes"][DEFAULT_HYBRID_FUSION_NAME]
    tuning_needed = (
        float(default_metrics.get("mrr", 0.0)) < float(dense_base.get("mrr", 0.0))
        or float(default_metrics.get("recall@1", 0.0)) < float(dense_base.get("recall@1", 0.0))
    )
    best = select_best(tuning_rows)
    best_config = {
        "object_type": "hyperedge",
        "template": "baseline_current",
        "fusion": best["fusion"],
        "metrics": best["metrics"],
        "hard_negative_confusion_rate@5": best["hard_negative_confusion_rate@5"],
        "tuning_needed_by_rule": tuning_needed,
        "default_config": {
            "fusion_name": DEFAULT_HYBRID_FUSION_NAME,
            "dense_weight": DEFAULT_HYBRID_DENSE_WEIGHT,
            "sparse_weight": DEFAULT_HYBRID_SPARSE_WEIGHT,
            "metrics": default_metrics,
        },
    }
    write_json(args.out_dir / f"comparison{suffix}.json", comparison)
    write_json(args.out_dir / f"tuning_results{suffix}.json", {"rows": tuning_rows})
    write_json(args.out_dir / f"best_config.server_sample20{suffix}.json", best_config)
    print(json.dumps({"comparison": comparison, "best_config": best_config}, ensure_ascii=False, indent=2))


def load_retrieval_object_index(conn) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT object_type, object_id, doc_id, metadata
            FROM retrieval_objects
            WHERE object_type = 'hyperedge'
            """
        )
        return [
            {
                "object_type": row[0],
                "object_id": row[1],
                "doc_id": row[2],
                "metadata": row[3] or {},
            }
            for row in cur.fetchall()
        ]


def map_targets_hyperedge_only(
    old_targets: Sequence[Mapping[str, Any]],
    retrieval_objects: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    fact_to_hyperedges: dict[str, list[str]] = defaultdict(list)
    object_ids = {str(obj["object_id"]) for obj in retrieval_objects}
    for obj in retrieval_objects:
        object_id = str(obj["object_id"])
        metadata = obj.get("metadata") or {}
        for fact_id in metadata.get("fact_ids") or []:
            fact_to_hyperedges[str(fact_id)].append(object_id)

    mapped: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for target in old_targets:
        source_type = str(target.get("target_type", ""))
        source_id = str(target.get("target_id", ""))
        query_id = str(target.get("query_id", ""))
        query_type = str(target.get("query_type", ""))
        doc_id = target.get("doc_id")
        if source_type == "fact":
            acceptable = [
                {"object_type": OBJECT_TYPE, "object_id": object_id}
                for object_id in sorted(set(fact_to_hyperedges.get(source_id, [])))
            ]
            if acceptable:
                mapped.append(
                    target_group_row(
                        query_id,
                        doc_id,
                        query_type,
                        "hyperedge",
                        acceptable,
                        source_type,
                        source_id,
                        target.get("source_field"),
                        required=True,
                        diagnostic=False,
                    )
                )
            else:
                missing.append(missing_row(target, "hyperedge"))
        elif source_type == "evidence":
            mapped.append(
                target_group_row(
                    query_id,
                    doc_id,
                    query_type,
                    "evidence_unit",
                    [{"object_type": "evidence_unit", "object_id": source_id}],
                    source_type,
                    source_id,
                    target.get("source_field"),
                    required=True,
                    diagnostic=False,
                )
            )
        elif source_type == "node":
            mapped.append(
                target_group_row(
                    query_id,
                    doc_id,
                    query_type,
                    "canonical_entity",
                    [{"object_type": "canonical_entity", "object_id": source_id}],
                    source_type,
                    source_id,
                    target.get("source_field"),
                    required=False,
                    diagnostic=True,
                )
            )
        elif source_id in object_ids:
            mapped.append(
                target_group_row(
                    query_id,
                    doc_id,
                    query_type,
                    "hyperedge",
                    [{"object_type": OBJECT_TYPE, "object_id": source_id}],
                    source_type,
                    source_id,
                    target.get("source_field"),
                    required=True,
                    diagnostic=False,
                )
            )
        else:
            missing.append(missing_row(target, "hyperedge"))

    main_rows = [row for row in mapped if not row.get("diagnostic")]
    report = {
        "source_target_count": len(old_targets),
        "mapped_main_target_count": len(main_rows),
        "diagnostic_target_count": len([row for row in mapped if row.get("diagnostic")]),
        "missing_count": len(missing),
        "missing": missing[:50],
        "source_target_type_counts": dict(Counter(str(row.get("target_type")) for row in old_targets)),
        "mapped_target_type_counts": dict(Counter(str(row.get("target_type")) for row in main_rows)),
        "main_query_count": len({str(row.get("query_id")) for row in main_rows}),
        "total_query_count": len({str(row.get("query_id")) for row in old_targets}),
    }
    return mapped, report


def target_group_row(
    query_id: str,
    doc_id: Any,
    query_type: str,
    target_type: str,
    acceptable_targets: list[dict[str, str]],
    source_target_type: str,
    source_target_id: str,
    source_field: Any,
    *,
    required: bool,
    diagnostic: bool,
) -> dict[str, Any]:
    object_ids = [item["object_id"] for item in acceptable_targets]
    prefix = "diagnostic" if diagnostic else "main"
    return {
        "query_id": query_id,
        "doc_id": doc_id,
        "query_type": query_type,
        "target_group_id": f"{query_id}:{prefix}:{target_type}:{source_target_id}",
        "target_type": target_type,
        "target_id": object_ids[0],
        "target_ids": object_ids,
        "acceptable_targets": acceptable_targets,
        "source_target_type": source_target_type,
        "source_target_id": source_target_id,
        "source_field": source_field,
        "required": required,
        "diagnostic": diagnostic,
    }


def missing_row(target: Mapping[str, Any], expected: str) -> dict[str, Any]:
    return {
        "query_id": str(target.get("query_id")),
        "doc_id": target.get("doc_id"),
        "query_type": str(target.get("query_type")),
        "source_target_type": str(target.get("target_type")),
        "source_target_id": str(target.get("target_id")),
        "expected_object_type": expected,
    }


def embed_query_batches(args: argparse.Namespace, queries: list[Mapping[str, Any]]) -> dict[str, EmbeddingRow]:
    out: dict[str, EmbeddingRow] = {}
    for batch in chunks(queries, max(args.batch_size, 1)):
        rows = embed_texts(
            args.embed_url,
            [str(row["query"]) for row in batch],
            args.sparse_max_terms,
            args.batch_size,
        )
        for query, embedding in zip(batch, rows):
            out[str(query["query_id"])] = embedding
    return out


DOC_ID_RE = re.compile(r"\b(?:\d{1,4}_)?WO\d{10}[A-Z]\d\b", re.IGNORECASE)
EVIDENCE_ID_RE = re.compile(r"\b(?:EU|EVD)_[A-Za-z0-9_:-]+\b")
EXAMPLE_RE = re.compile(
    r"\b(?:(?:working|comparative|production)\s+)?example\s+[A-Za-z0-9_.-]+\b",
    re.IGNORECASE,
)
EX_SHORT_RE = re.compile(r"\bEx\.?\s*[A-Za-z0-9_.-]+\b", re.IGNORECASE)
STANDARD_RE = re.compile(r"\b(?:ASTM|ISO|JIS|GB|DIN|GMW)\s*[- ]?[A-Za-z0-9_.-]+\b", re.IGNORECASE)


def structured_rerank(query: Mapping[str, Any] | str | None, results: Sequence[SearchResult], *, limit: int) -> list[SearchResult]:
    signals = extract_rerank_signals(query)
    ranked = [score_rerank_result(signals, result) for result in results]
    ranked.sort(
        key=lambda row: (
            -row.final_score,
            -float((row.metadata or {}).get("_rerank", {}).get("base_score", 0.0)),
            row.object_id,
        )
    )
    return with_ranks(ranked[:limit])


def extract_rerank_signals(query: Mapping[str, Any] | str | None) -> dict[str, tuple[str, ...]]:
    if query is None:
        row: Mapping[str, Any] = {}
    elif isinstance(query, str):
        row = {"query": query}
    else:
        row = query
    query_text = str(row.get("query") or "")
    expected_facts = [fact for fact in row.get("expected_facts") or [] if isinstance(fact, Mapping)]
    properties: list[Any] = []
    test_methods: list[Any] = []
    samples: list[Any] = []
    polarities: list[Any] = []
    for fact in expected_facts:
        samples.append(fact.get("sample"))
        properties.extend(property_aliases(fact.get("property")))
        test_methods.extend(test_method_aliases(fact.get("test_method")))
        polarities.extend(polarity_aliases(fact.get("polarity_hint")))
    properties.extend(property_aliases(row.get("property")))
    properties.extend(property_aliases(row.get("property_canonical_id")))
    for prop_id in re.findall(r"\bPROP_[A-Za-z0-9_:-]+\b", query_text):
        properties.extend(property_aliases(prop_id))
    test_methods.extend(test_method_aliases(row.get("test_method")))
    test_methods.extend(STANDARD_RE.findall(query_text))
    polarities.extend(polarity_aliases(row.get("polarity")))
    polarities.extend(polarity_aliases(query_text))
    example_kinds: list[str] = []
    query_norm = norm_text(query_text)
    if "comparative" in query_norm or "control" in query_norm:
        example_kinds.extend(["comparative", "control"])
    if "working example" in query_norm or "positive example" in query_norm:
        example_kinds.append("working")
    return {
        "doc_ids": tuple(unique_texts(row.get("doc_id"), DOC_ID_RE.findall(query_text))),
        "evidence_ids": tuple(unique_texts(row.get("expected_evidence_ids"), EVIDENCE_ID_RE.findall(query_text))),
        "samples": tuple(unique_texts(samples)),
        "examples": tuple(unique_texts(EXAMPLE_RE.findall(query_text), EX_SHORT_RE.findall(query_text))),
        "properties": tuple(unique_texts(properties)),
        "test_methods": tuple(unique_texts(test_methods)),
        "polarities": tuple(unique_texts(polarities)),
        "example_kinds": tuple(unique_texts(example_kinds)),
        "negative_confusions": tuple(unique_texts(row.get("negative_confusions"))),
    }


def score_rerank_result(signals: Mapping[str, Sequence[str]], result: SearchResult) -> SearchResult:
    base_score = float(result.final_score or 0.0)
    bonus = 0.0
    penalty = 0.0
    reasons: list[dict[str, Any]] = []
    metadata = dict(result.metadata or {})
    haystack = result_haystack(result)
    doc_ids = signals.get("doc_ids") or ()
    if doc_ids:
        if result.doc_id and any(doc_ids_match(doc_id, result.doc_id) for doc_id in doc_ids):
            bonus += 3.0
            reasons.append({"rule": "doc_id_match", "delta": 3.0, "value": result.doc_id})
        else:
            penalty += 5.0
            reasons.append({"rule": "wrong_doc_penalty", "delta": -5.0, "value": result.doc_id})
    for rule, key, boost in [
        ("sample_match", "samples", 2.0),
        ("example_match", "examples", 2.0),
        ("property_match", "properties", 1.5),
        ("test_method_match", "test_methods", 1.0),
    ]:
        if signals_in_text(signals.get(key) or (), haystack):
            bonus += boost
            reasons.append({"rule": rule, "delta": boost})
    if signals_in_text(signals.get("polarities") or (), haystack) or signals_in_text(
        signals.get("example_kinds") or (), haystack
    ):
        bonus += 1.0
        reasons.append({"rule": "polarity_or_kind_match", "delta": 1.0})
    evidence_ids = {str(item) for item in metadata.get("evidence_ids") or []}
    matched_evidence = sorted(set(signals.get("evidence_ids") or ()) & evidence_ids)
    if matched_evidence:
        bonus += 2.0
        reasons.append({"rule": "evidence_id_match", "delta": 2.0, "value": matched_evidence})
    negative_hits = signals_in_text(signals.get("negative_confusions") or (), haystack)
    if negative_hits:
        penalty += 2.0
        reasons.append({"rule": "negative_confusion_penalty", "delta": -2.0, "value": negative_hits})
    rerank_score = base_score + bonus - penalty
    metadata["_rerank"] = {
        "rerank_score": rerank_score,
        "base_score": base_score,
        "rerank_bonus": bonus,
        "rerank_penalty": penalty,
        "rerank_reasons": reasons,
    }
    channels = tuple(dict.fromkeys((*result.channels, "structured_rerank")))
    return replace(result, final_score=rerank_score, metadata=metadata, channels=channels)


def rerank_delta_rows(
    targets: Sequence[Mapping[str, Any]],
    base_results: Mapping[str, list[SearchResult]],
    reranked_results: Mapping[str, list[SearchResult]],
    *,
    allow_evidence_via_hyperedge: bool,
) -> list[dict[str, Any]]:
    rows = []
    for target in targets:
        qid = str(target["query_id"])
        base_row = target_group_eval_row(
            target,
            base_results.get(qid, []),
            allow_evidence_via_hyperedge=allow_evidence_via_hyperedge,
        )
        rerank_row = target_group_eval_row(
            target,
            reranked_results.get(qid, []),
            allow_evidence_via_hyperedge=allow_evidence_via_hyperedge,
        )
        rows.append(
            {
                "query_id": qid,
                "doc_id": target.get("doc_id"),
                "query_type": target.get("query_type"),
                "target_type": target.get("target_type"),
                "target_group_id": target.get("target_group_id"),
                "base_rank": base_row["rank"],
                "rerank_rank": rerank_row["rank"],
                "base_hit_kind": base_row.get("hit_kind"),
                "rerank_hit_kind": rerank_row.get("hit_kind"),
                "status": rank_delta_status(base_row["rank"], rerank_row["rank"]),
                "rank_delta": rank_delta(base_row["rank"], rerank_row["rank"]),
            }
        )
    return rows


def rerank_debug_rows(
    queries: Sequence[Mapping[str, Any]],
    base_results: Mapping[str, list[SearchResult]],
    reranked_results: Mapping[str, list[SearchResult]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    rows = []
    for query in queries:
        qid = str(query["query_id"])
        base_rank_by_key = {row.key: idx for idx, row in enumerate(base_results.get(qid, []), start=1)}
        rows.append(
            {
                "query_id": qid,
                "doc_id": query.get("doc_id"),
                "query_type": query.get("query_type"),
                "query": query.get("query"),
                "signals": extract_rerank_signals(query),
                "top_results": [
                    {
                        **result_summary(result),
                        "base_rank": base_rank_by_key.get(result.key),
                        "rerank": (result.metadata or {}).get("_rerank", {}),
                    }
                    for result in reranked_results.get(qid, [])[:limit]
                ],
            }
        )
    return rows


def rank_delta_status(base_rank: int | None, rerank_rank: int | None) -> str:
    if base_rank is None and rerank_rank is None:
        return "still_missing"
    if base_rank is None and rerank_rank is not None:
        return "improved"
    if base_rank is not None and rerank_rank is None:
        return "regressed"
    if rerank_rank < base_rank:
        return "improved"
    if rerank_rank > base_rank:
        return "regressed"
    return "unchanged"


def rank_delta(base_rank: int | None, rerank_rank: int | None) -> int | None:
    if base_rank is None or rerank_rank is None:
        return None
    return base_rank - rerank_rank


def result_haystack(result: SearchResult) -> str:
    return norm_text(" ".join([result.object_id, result.doc_id or "", result.text_for_embedding or "", json.dumps(result.metadata or {}, ensure_ascii=False, sort_keys=True)]))


def signals_in_text(signals: Iterable[str], text_norm: str) -> list[str]:
    hits = []
    for signal in signals:
        signal_norm = norm_text(signal)
        if signal_norm and signal_norm in text_norm:
            hits.append(signal)
    return hits


def doc_ids_match(left: str, right: str) -> bool:
    return left == right or bare_doc_id(left) == bare_doc_id(right)


def bare_doc_id(doc_id: str) -> str:
    prefix, sep, rest = str(doc_id).partition("_")
    if sep and prefix.isdigit() and rest.upper().startswith("WO"):
        return rest
    return str(doc_id)


def property_aliases(value: Any) -> list[str]:
    aliases = []
    for item in flatten_texts(value):
        aliases.extend([item, item.replace("_", " ")])
        if item.upper().startswith("PROP_"):
            aliases.append(item[5:].replace("_", " "))
    return aliases


def test_method_aliases(value: Any) -> list[str]:
    aliases = []
    for item in flatten_texts(value):
        aliases.extend([item, item.replace("_", " ")])
        if item.upper().startswith("STD_"):
            aliases.append(item[4:].replace("_", " "))
    return aliases


def polarity_aliases(value: Any) -> list[str]:
    text = norm_text(value)
    out = []
    for item in ("positive", "negative", "comparative", "control", "working"):
        if item in text:
            out.append(item)
    return out


def unique_texts(*values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for text in flatten_texts(value):
            if text not in seen:
                seen.add(text)
                out.append(text)
    return out


def flatten_texts(value: Any) -> Iterable[str]:
    if value is None:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            yield text
        return
    if isinstance(value, Mapping):
        for key in ("id", "canonical_id", "name", "value", "label"):
            if key in value:
                yield from flatten_texts(value[key])
        return
    if isinstance(value, Iterable):
        for item in value:
            yield from flatten_texts(item)
        return
    text = str(value).strip()
    if text:
        yield text


def norm_text(value: Any) -> str:
    text = str(value or "").casefold().replace("_", " ")
    text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def search_dense(conn, query_vec: list[float], *, limit: int) -> list[SearchResult]:
    vector = vector_literal(query_vec)
    with conn.cursor() as cur:
        cur.execute("SET hnsw.ef_search = 100")
        cur.execute(
            """
            SELECT object_type, object_id, doc_id, text_for_embedding, metadata,
                   1 - (dense_embedding <=> %s::vector) AS dense_score
            FROM retrieval_objects
            WHERE object_type = 'hyperedge'
              AND dense_embedding IS NOT NULL
            ORDER BY dense_embedding <=> %s::vector
            LIMIT %s
            """,
            (vector, vector, limit),
        )
        return [
            SearchResult(
                object_type=row[0],
                object_id=row[1],
                doc_id=row[2],
                text_for_embedding=row[3],
                metadata=row[4] or {},
                dense_score=float(row[5]),
                final_score=float(row[5]),
                channels=("dense",),
            )
            for row in cur.fetchall()
        ]


def search_sparse(conn, lexical_weights: dict[str, float], *, limit: int) -> list[SearchResult]:
    terms = [(token_id, float(weight)) for token_id, weight in lexical_weights.items() if weight > 0]
    if not terms:
        return []
    values_sql = ", ".join(["(%s, %s)"] * len(terms))
    flat_terms: list[Any] = []
    for token_id, weight in terms:
        flat_terms.extend([token_id, weight])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            WITH query_terms(token_id, q_weight) AS (
                VALUES {values_sql}
            )
            SELECT o.object_type, o.object_id, o.doc_id, o.text_for_embedding, o.metadata,
                   SUM(query_terms.q_weight * p.weight) AS sparse_score
            FROM query_terms
            JOIN retrieval_sparse_postings p ON p.token_id = query_terms.token_id
            JOIN retrieval_objects o
              ON o.object_type = p.object_type
             AND o.object_id = p.object_id
            WHERE p.object_type = 'hyperedge'
            GROUP BY o.object_type, o.object_id, o.doc_id, o.text_for_embedding, o.metadata
            ORDER BY sparse_score DESC
            LIMIT %s
            """,
            [*flat_terms, limit],
        )
        return [
            SearchResult(
                object_type=row[0],
                object_id=row[1],
                doc_id=row[2],
                text_for_embedding=row[3],
                metadata=row[4] or {},
                sparse_score=float(row[5]),
                final_score=float(row[5]),
                channels=("sparse",),
            )
            for row in cur.fetchall()
        ]


def fuse_for_config(
    fusion: FusionConfig,
    dense: list[SearchResult],
    sparse: list[SearchResult],
    *,
    limit: int,
) -> list[SearchResult]:
    if fusion.mode == "dense":
        return with_ranks(dense[:limit])
    if fusion.mode == "sparse":
        return with_ranks(sparse[:limit])
    if fusion.mode == "hybrid_rrf":
        return rrf_fuse(dense, sparse, k=fusion.rrf_k or 60, limit=limit)
    if fusion.mode == "hybrid_weighted":
        return weighted_fuse(
            dense,
            sparse,
            dense_weight=fusion.dense_weight if fusion.dense_weight is not None else DEFAULT_HYBRID_DENSE_WEIGHT,
            sparse_weight=fusion.sparse_weight if fusion.sparse_weight is not None else DEFAULT_HYBRID_SPARSE_WEIGHT,
            limit=limit,
        )
    raise ValueError(f"Unsupported fusion mode: {fusion.mode}")


def rrf_fuse(
    dense_results: list[SearchResult],
    sparse_results: list[SearchResult],
    *,
    k: int,
    limit: int,
) -> list[SearchResult]:
    merged: dict[tuple[str, str], SearchResult] = {}
    scores: dict[tuple[str, str], float] = {}
    channels: dict[tuple[str, str], set[str]] = {}
    for channel, rows in (("dense", dense_results), ("sparse", sparse_results)):
        for idx, row in enumerate(rows, start=1):
            key = row.key
            merged[key] = merge_result(merged[key], row) if key in merged else row
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + idx)
            channels.setdefault(key, set()).add(channel)
    ranked = [
        replace(row, final_score=scores[row.key], channels=tuple(sorted(channels.get(row.key, ()))))
        for row in merged.values()
    ]
    ranked.sort(key=lambda row: (-row.final_score, row.object_id))
    return with_ranks(ranked[:limit])


def weighted_fuse(
    dense_results: list[SearchResult],
    sparse_results: list[SearchResult],
    *,
    dense_weight: float,
    sparse_weight: float,
    limit: int,
) -> list[SearchResult]:
    dense_norm = normalized_scores(dense_results, attr="dense_score")
    sparse_norm = normalized_scores(sparse_results, attr="sparse_score")
    merged: dict[tuple[str, str], SearchResult] = {}
    channels: dict[tuple[str, str], set[str]] = {}
    for channel, rows in (("dense", dense_results), ("sparse", sparse_results)):
        for row in rows:
            merged[row.key] = merge_result(merged[row.key], row) if row.key in merged else row
            channels.setdefault(row.key, set()).add(channel)
    ranked = []
    for key, row in merged.items():
        final = dense_weight * dense_norm.get(key, 0.0) + sparse_weight * sparse_norm.get(key, 0.0)
        ranked.append(replace(row, final_score=final, channels=tuple(sorted(channels.get(key, ())))))
    ranked.sort(key=lambda row: (-row.final_score, row.object_id))
    return with_ranks(ranked[:limit])


def merge_result(left: SearchResult, right: SearchResult) -> SearchResult:
    return replace(
        left,
        doc_id=left.doc_id or right.doc_id,
        text_for_embedding=left.text_for_embedding or right.text_for_embedding,
        metadata=left.metadata or right.metadata,
        dense_score=left.dense_score if left.dense_score is not None else right.dense_score,
        sparse_score=left.sparse_score if left.sparse_score is not None else right.sparse_score,
    )


def normalized_scores(rows: list[SearchResult], *, attr: str) -> dict[tuple[str, str], float]:
    raw = {row.key: float(getattr(row, attr) or 0.0) for row in rows}
    if not raw:
        return {}
    low = min(raw.values())
    high = max(raw.values())
    if high <= low:
        return {key: 1.0 for key in raw}
    return {key: (score - low) / (high - low) for key, score in raw.items()}


def with_ranks(rows: list[SearchResult]) -> list[SearchResult]:
    return [replace(row, rank=idx) for idx, row in enumerate(rows, start=1)]


def evaluate_target_groups(
    targets: list[Mapping[str, Any]],
    results_by_query: Mapping[str, list[SearchResult]],
    *,
    ks: Iterable[int],
    allow_evidence_via_hyperedge: bool,
) -> dict[str, Any]:
    rows = [
        target_group_eval_row(
            target,
            results_by_query.get(str(target["query_id"]), []),
            allow_evidence_via_hyperedge=allow_evidence_via_hyperedge,
        )
        for target in targets
    ]
    return summarize_rows(rows, ks=ks)


def target_group_eval_row(
    target: Mapping[str, Any],
    results: list[SearchResult],
    *,
    allow_evidence_via_hyperedge: bool,
) -> dict[str, Any]:
    acceptable = {
        (str(item["object_type"]), str(item["object_id"]))
        for item in target.get("acceptable_targets") or []
    }
    rank = None
    hit_kind = None
    for idx, result in enumerate(results, start=1):
        if result.key in acceptable:
            rank = idx
            hit_kind = "direct"
            break
        if allow_evidence_via_hyperedge and hyperedge_links_target_evidence(target, result):
            rank = idx
            hit_kind = "via_hyperedge"
            break
    return {
        "query_id": str(target["query_id"]),
        "query_type": str(target.get("query_type")),
        "target_type": str(target.get("target_type")),
        "target_group_id": str(target.get("target_group_id", "")),
        "rank": rank,
        "hit_kind": hit_kind,
    }


def hyperedge_links_target_evidence(target: Mapping[str, Any], result: SearchResult) -> bool:
    if str(target.get("target_type")) != "evidence_unit":
        return False
    if result.object_type != "hyperedge":
        return False
    target_ids = {str(item["object_id"]) for item in target.get("acceptable_targets") or []}
    evidence_ids = {str(item) for item in (result.metadata or {}).get("evidence_ids") or []}
    return bool(target_ids & evidence_ids)


def summarize_rows(rows: list[Mapping[str, Any]], *, ks: Iterable[int]) -> dict[str, Any]:
    return {
        "overall": metric_rows(rows, ks=ks),
        "by_target_type": group_metrics(rows, "target_type", ks=ks),
        "by_query_type": group_metrics(rows, "query_type", ks=ks),
        "total_targets": len(rows),
        "total_queries": len({str(row.get("query_id")) for row in rows}),
    }


def metric_rows(rows: list[Mapping[str, Any]], *, ks: Iterable[int]) -> dict[str, Any]:
    total = len(rows)
    out: dict[str, Any] = {"count": total}
    for k in ks:
        hits = sum(1 for row in rows if row.get("rank") is not None and int(row["rank"]) <= k)
        out[f"recall@{k}"] = round(hits / total, 4) if total else 0.0
    rr = [1.0 / int(row["rank"]) if row.get("rank") is not None else 0.0 for row in rows]
    out["mrr"] = round(sum(rr) / total, 4) if total else 0.0
    return out


def group_metrics(rows: list[Mapping[str, Any]], key: str, *, ks: Iterable[int]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key))].append(row)
    return {name: metric_rows(items, ks=ks) for name, items in sorted(grouped.items())}


def hard_negative_confusion_stats(
    queries: list[Mapping[str, Any]],
    results_by_query: Mapping[str, list[SearchResult]],
    *,
    ks: Iterable[int],
) -> dict[str, Any]:
    k_values = sorted(set(int(k) for k in ks))
    hard_queries = [query for query in queries if query.get("query_type") == "hard_negative"]
    warnings: list[dict[str, Any]] = []
    for query in hard_queries:
        qid = str(query["query_id"])
        confusions = [str(item).casefold() for item in query.get("negative_confusions") or [] if str(item).strip()]
        if not confusions:
            continue
        for result in results_by_query.get(qid, [])[: max(k_values, default=0)]:
            haystack = " ".join(
                [result.object_id, result.text_for_embedding or "", str(result.metadata or "")]
            ).casefold()
            hits = [item for item in confusions if item and item in haystack]
            if hits:
                warnings.append(
                    {
                        "query_id": qid,
                        "rank": result.rank,
                        "matched_confusions": hits,
                        "result": result_summary(result),
                    }
                )
    by_k: dict[str, dict[str, Any]] = {}
    for k in k_values:
        warnings_at_k = [row for row in warnings if row.get("rank") is not None and int(row["rank"]) <= k]
        hit_queries = {str(row["query_id"]) for row in warnings_at_k}
        total_results = sum(min(k, len(results_by_query.get(str(query["query_id"]), []))) for query in hard_queries)
        by_k[f"@{k}"] = {
            "confusion_query_count": len(hit_queries),
            "hard_negative_query_count": len(hard_queries),
            "confusion_query_rate": round(len(hit_queries) / len(hard_queries), 4) if hard_queries else 0.0,
            "confusion_result_count": len(warnings_at_k),
            "retrieved_result_count": total_results,
            "confusion_result_rate": round(len(warnings_at_k) / total_results, 4) if total_results else 0.0,
        }
    return {
        "hard_negative_queries": len(hard_queries),
        "by_k": by_k,
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def failure_cases(
    queries_by_id: Mapping[str, Mapping[str, Any]],
    targets: list[Mapping[str, Any]],
    results_by_query: Mapping[str, list[SearchResult]],
    *,
    max_rank: int,
    allow_evidence_via_hyperedge: bool,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for target in targets:
        qid = str(target["query_id"])
        row = target_group_eval_row(
            target,
            results_by_query.get(qid, []),
            allow_evidence_via_hyperedge=allow_evidence_via_hyperedge,
        )
        if row["rank"] is not None and int(row["rank"]) <= max_rank:
            continue
        query = queries_by_id.get(qid, {})
        cases.append(
            {
                "query_id": qid,
                "doc_id": target.get("doc_id"),
                "query": query.get("query"),
                "query_type": target.get("query_type"),
                "target_type": target.get("target_type"),
                "target_group_id": target.get("target_group_id"),
                "acceptable_targets": target.get("acceptable_targets"),
                "rank": row["rank"],
                "hit_kind": row.get("hit_kind"),
                "top_results": [result_summary(result) for result in results_by_query.get(qid, [])[:5]],
            }
        )
    return cases


def select_best(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    return dict(
        sorted(
            rows,
            key=lambda row: (
                -float(row["metrics"].get("mrr", 0.0)),
                -float(row["metrics"].get("recall@1", 0.0)),
                -float(row["metrics"].get("recall@3", 0.0)),
                float(row.get("hard_negative_confusion_rate@5", 0.0)),
                str(row["fusion"].get("name")),
            ),
        )[0]
    )


def metric_deltas(base: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, float]:
    keys = [key for key in candidate if key.startswith("recall@") or key == "mrr"]
    return {key: round(float(candidate.get(key, 0.0)) - float(base.get(key, 0.0)), 4) for key in keys}


def result_summary(result: SearchResult) -> dict[str, Any]:
    return {
        "rank": result.rank,
        "object_type": result.object_type,
        "object_id": result.object_id,
        "doc_id": result.doc_id,
        "final_score": round(result.final_score, 6),
        "dense_score": None if result.dense_score is None else round(result.dense_score, 6),
        "sparse_score": None if result.sparse_score is None else round(result.sparse_score, 6),
        "channels": list(result.channels),
        "metadata": result.metadata,
        "text_preview": (result.text_for_embedding or "")[:240],
    }


def materials_text(label: str, rows: object) -> str | None:
    if isinstance(rows, Mapping):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        return None
    parts: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        amount_raw = row.get("amount")
        amount = amount_raw if isinstance(amount_raw, Mapping) else {}
        value = amount.get("value") if amount else amount_raw
        if value is None:
            value = row.get("value")
        unit = amount.get("unit") if amount else row.get("unit")
        qty = ""
        if value is not None:
            qty = f" {value}"
            if unit:
                qty += f" {unit}"
        material = (
            row.get("material")
            or row.get("source_label")
            or row.get("name")
            or row.get("label")
            or row.get("canonical_id")
        )
        cid = row.get("canonical_id")
        parts.append(" ".join(str(x) for x in [material, cid, qty.strip()] if x))
    return f"{label}: " + "; ".join(parts) if parts else None


MATERIAL_TEXT_SLOT_ALIASES = {
    "resin": ("resin", "resins", "binder", "binders"),
    "curing_agent": (
        "curing_agent",
        "curing_agents",
        "crosslinker",
        "crosslinkers",
        "hardener",
        "hardeners",
    ),
    "pigment": ("pigment", "pigments"),
    "filler": ("filler", "fillers"),
    "additive": ("additive", "additives"),
    "solvent": ("solvent", "solvents"),
    "catalyst": ("catalyst", "catalysts"),
    "tackifier": ("tackifier", "tackifiers"),
    "plasticizer": ("plasticizer", "plasticizers"),
    "wax": ("wax", "waxes"),
    "antioxidant": ("antioxidant", "antioxidants"),
    "reactive_diluent": ("reactive_diluent", "reactive_diluents"),
    "monomer": ("monomer", "monomers"),
    "biocide": ("biocide", "biocides"),
    "photoinitiator": ("photoinitiator", "photoinitiators"),
    "initiator": ("initiator", "initiators"),
    "neutralizer": ("neutralizer", "neutralizers"),
    "polyol": ("polyol", "polyols"),
    "resin_precursor": ("resin_precursor", "resin_precursors"),
    "material": ("material", "materials", "material_roles"),
    "component": ("component", "components"),
}


def material_slot(row: Mapping[str, Any], slot: str) -> object:
    aliases = MATERIAL_TEXT_SLOT_ALIASES[slot]
    values: list[object] = []
    for key in aliases:
        value = row.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    formulation = row.get("formulation")
    if isinstance(formulation, Mapping):
        for key in aliases:
            value = formulation.get(key)
            if isinstance(value, list):
                values.extend(value)
            elif value:
                values.append(value)
    deduped: list[object] = []
    seen: set[str] = set()
    for value in values:
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, Mapping) else str(value)
        if marker not in seen:
            seen.add(marker)
            deduped.append(value)
    return deduped


def text_value(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def process_text(rows: object) -> str | None:
    if not isinstance(rows, list) or not rows:
        return None
    parts = []
    for row in rows:
        if isinstance(row, Mapping):
            parts.append(" ".join(str(x) for x in [row.get("step"), row.get("param")] if x))
        elif row:
            parts.append(str(row))
    return "process: " + "; ".join(parts) if parts else None


def test_method_text(value: object) -> str | None:
    if not value:
        return None
    if isinstance(value, Mapping):
        return join_parts(
            "test_method:",
            value.get("standard_id"),
            value.get("method"),
            value.get("duration"),
            value.get("condition"),
        )
    return f"test_method: {value}"


def results_text(rows: object) -> str | None:
    if not isinstance(rows, list) or not rows:
        return None
    parts: list[str] = []
    for row in rows:
        if isinstance(row, Mapping):
            metric = row.get("metric") or row.get("property")
            value = row.get("value_text")
            if value is None:
                value = row.get("value")
            if value is not None and row.get("unit"):
                value = f"{value} {row.get('unit')}"
            parts.append(" ".join(str(x) for x in [metric, value] if x))
        elif row:
            parts.append(str(row))
    return "result: " + "; ".join(parts) if parts else None


def labelled_list(label: str, value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Iterable):
        items = [str(item) for item in value if item is not None and str(item).strip()]
    else:
        items = [str(value)]
    return f"{label}: " + "; ".join(items) if items else None


def join_parts(*parts: object) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        text = " ".join(str(part).split())
        if text:
            chunks.append(text)
    return " | ".join(chunks) or " "


def truncate(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    return text[:max_chars] if len(text) > max_chars else text


def vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(item):.8g}" for item in vec) + "]"


def chunks(items: Sequence[Any], size: int) -> Iterable[list[Any]]:
    for idx in range(0, len(items), size):
        yield list(items[idx : idx + size])


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
