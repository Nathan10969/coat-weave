"""Expand retrieved hyperedge object ids into raw KG multi-hop JSON.

The script is intentionally read-only: it queries ``retrieval_objects`` for
already retrieved hyperedge rows, then uses structured ids to pull raw records
from KG zip packages.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OBJECT_TYPE = "hyperedge"
SCHEMA_VERSION = "hyperedge_multihop_expand_v1"

DEFAULT_BASE = Path("/root/coating/embedding")
DEFAULT_PG_ENV = DEFAULT_BASE / "pgvector.env"
DEFAULT_KG_DIR = DEFAULT_BASE / "data" / "kg_286_aggregate"
DEFAULT_KG_ZIPS = (
    DEFAULT_BASE / "data" / "review_bundle_110.zip",
    DEFAULT_BASE / "data" / "merged_kg_pack_150.zip",
    DEFAULT_BASE / "data" / "review_bundle_35.zip",
)
DEFAULT_OUT = DEFAULT_BASE / "outputs" / "multihop_expand" / "result.json"

KG_FILENAMES = {
    "hyperedges.jsonl",
    "facts.jsonl",
    "evidence_units.jsonl",
    "patents.jsonl",
    "patent_profiles.jsonl",
}


@dataclass
class KgStore:
    """In-memory id index over one or more KG zip packages."""

    kg_dirs: list[str] = field(default_factory=list)
    kg_zips: list[str] = field(default_factory=list)
    hyperedges: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    facts_by_id: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    facts_by_source_hyperedge: dict[tuple[str, str], list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    facts_by_context: dict[tuple[str, str], list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    evidence_by_id: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    patents_by_doc: dict[str, dict[str, Any]] = field(default_factory=dict)
    patent_profiles_by_doc: dict[str, dict[str, Any]] = field(default_factory=dict)
    full_records: dict[tuple[str, str, str], list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    full_records_by_doc: dict[tuple[str, str], list[dict[str, Any]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def remember(
        self, kind: str, row: Mapping[str, Any], doc_hint: str | None,
        provenance: Mapping[str, Any] | None = None,
    ) -> None:
        """Retain source-scoped variants without changing the legacy first-row indexes."""
        doc_id = doc_id_from(row, doc_hint)
        if not doc_id:
            return
        id_field = {"hyperedges": "hyperedge_id", "facts": "fact_id", "evidence": "evidence_id"}.get(kind)
        record_id = text_or_none(row.get(id_field) or row.get("id")) if id_field else doc_id
        origin = {**(provenance or {}), "doc_id": doc_id}
        origin.setdefault("source_id", None)
        raw = dict(row)
        if record_id:
            variants = self.full_records[(kind, doc_id, record_id)]
            for existing in variants:
                if existing["provenance"]["source_id"] == origin["source_id"] and existing["raw"] == raw:
                    if origin not in existing["origins"]:
                        existing["origins"].append(origin)
                    return
        else:
            # Anonymous records have no identity on which deduplication can be justified.
            variants = []
        record = {"id": record_id, "raw": raw, "provenance": origin, "origins": [origin]}
        variants.append(record)
        self.full_records_by_doc[(kind, doc_id)].append(record)

    def add_hyperedge(
        self, row: Mapping[str, Any], doc_hint: str | None = None, *,
        provenance: Mapping[str, Any] | None = None, original: Mapping[str, Any] | None = None,
    ) -> None:
        self.remember("hyperedges", original if original is not None else row, doc_hint, provenance)
        doc_id = doc_id_from(row, doc_hint)
        hyperedge_id = text_or_none(row.get("hyperedge_id"))
        if doc_id and hyperedge_id:
            self.hyperedges.setdefault((doc_id, hyperedge_id), dict(row))

    def add_fact(self, row: Mapping[str, Any], doc_hint: str | None = None, *, provenance: Mapping[str, Any] | None = None) -> None:
        self.remember("facts", row, doc_hint, provenance)
        doc_id = doc_id_from(row, doc_hint)
        fact_id = text_or_none(row.get("fact_id") or row.get("id"))
        if not doc_id:
            return
        raw = dict(row)
        if fact_id:
            self.facts_by_id.setdefault((doc_id, fact_id), raw)
        source_hyperedge_id = text_or_none(row.get("source_hyperedge_id"))
        if source_hyperedge_id:
            self.facts_by_source_hyperedge[(doc_id, source_hyperedge_id)].append(raw)
        context_id = text_or_none(row.get("context_id"))
        if context_id:
            self.facts_by_context[(doc_id, context_id)].append(raw)

    def add_evidence(self, row: Mapping[str, Any], doc_hint: str | None = None, *, provenance: Mapping[str, Any] | None = None) -> None:
        self.remember("evidence", row, doc_hint, provenance)
        doc_id = doc_id_from(row, doc_hint)
        evidence_id = text_or_none(row.get("evidence_id") or row.get("id"))
        if doc_id and evidence_id:
            self.evidence_by_id.setdefault((doc_id, evidence_id), dict(row))

    def add_patent(self, row: Mapping[str, Any], doc_hint: str | None = None, *, provenance: Mapping[str, Any] | None = None) -> None:
        self.remember("patent", row, doc_hint, provenance)
        doc_id = doc_id_from(row, doc_hint)
        if doc_id:
            self.patents_by_doc.setdefault(doc_id, dict(row))

    def add_patent_profile(self, row: Mapping[str, Any], doc_hint: str | None = None, *, provenance: Mapping[str, Any] | None = None) -> None:
        self.remember("patent_profile", row, doc_hint, provenance)
        doc_id = doc_id_from(row, doc_hint)
        if doc_id:
            self.patent_profiles_by_doc.setdefault(doc_id, dict(row))


@dataclass(frozen=True)
class KgDirectorySource:
    path: Path
    collection_id: str | None = None
    company: str | None = None
    id_schema_version: str | None = None


def main() -> int:
    args = parse_args()
    object_ids = collect_object_ids(args.object_id, args.object_ids_file)
    if not object_ids:
        raise SystemExit("No hyperedge object ids provided. Use --object-id or --object-ids-file.")

    kg_dir_paths = tuple(args.kg_dir or ())
    kg_zip_paths = tuple(args.kg_zip or ())
    if not kg_dir_paths and not kg_zip_paths:
        if DEFAULT_KG_DIR.exists():
            kg_dir_paths = (DEFAULT_KG_DIR,)
        else:
            kg_zip_paths = DEFAULT_KG_ZIPS
    pg_env = load_pg_env(args.pg_env)
    store = load_kg_store(kg_dir_paths, kg_zip_paths)

    with pg_connect(pg_env) as conn:
        db_rows = fetch_db_hyperedges(conn, object_ids)

    payload = build_output(
        object_ids=object_ids,
        db_rows=db_rows,
        store=store,
        kg_dirs=kg_dir_paths,
        kg_zips=kg_zip_paths,
        pg_env=args.pg_env,
        max_context_facts=args.max_context_facts,
        evidence_mode=args.evidence_mode,
    )
    write_json(args.out, payload, pretty=args.pretty)
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--object-id", action="append", default=[], help="Hyperedge object_id; repeatable.")
    parser.add_argument("--object-ids-file", type=Path, help="Text file with one object_id per line.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pg-env", type=Path, default=DEFAULT_PG_ENV)
    parser.add_argument("--kg-dir", type=Path, action="append", default=None)
    parser.add_argument("--kg-zip", type=Path, action="append", default=None)
    parser.add_argument("--max-context-facts", type=int, default=20)
    parser.add_argument("--evidence-mode", choices=("compact", "full"), default="compact")
    parser.add_argument("--pretty", dest="pretty", action="store_true", default=True)
    parser.add_argument("--no-pretty", dest="pretty", action="store_false")
    return parser.parse_args()


def collect_object_ids(object_ids: Sequence[str], object_ids_file: Path | None) -> list[str]:
    out = [item.strip() for item in object_ids if item and item.strip()]
    if object_ids_file:
        out.extend(
            line.strip()
            for line in object_ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return out


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
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - depends on deployment env
        raise RuntimeError("psycopg2 is required to query pgvector retrieval_objects") from exc

    return psycopg2.connect(
        host=pg.get("PGHOST", "127.0.0.1"),
        port=int(pg.get("PGPORT", "5432")),
        dbname=pg.get("POSTGRES_DB", "coating_kg"),
        user=pg.get("POSTGRES_USER", "coating"),
        password=pg.get("POSTGRES_PASSWORD", ""),
    )


def fetch_db_hyperedges(conn: Any, object_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    unique_ids = list(dict.fromkeys(object_ids))
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT object_type, object_id, doc_id, text_for_embedding, metadata
            FROM retrieval_objects
            WHERE object_type = %s AND object_id = ANY(%s)
            """,
            (OBJECT_TYPE, unique_ids),
        )
        rows = cur.fetchall()
    out: dict[str, dict[str, Any]] = {}
    for object_type, object_id, doc_id, text_for_embedding, metadata in rows:
        out[str(object_id)] = {
            "object_type": object_type,
            "object_id": object_id,
            "doc_id": doc_id,
            "text_for_embedding": text_for_embedding,
            "metadata": normalize_metadata(metadata),
        }
    return out


def normalize_metadata(metadata: Any) -> dict[str, Any]:
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        try:
            value = json.loads(metadata)
        except json.JSONDecodeError:
            return {"raw_metadata": metadata}
        return value if isinstance(value, dict) else {"raw_metadata": value}
    return {}


def load_kg_store(
    kg_dirs: Sequence[Path | KgDirectorySource],
    kg_zips: Sequence[Path] | None = None,
) -> KgStore:
    if kg_zips is None:
        kg_zips = tuple(item.path if isinstance(item, KgDirectorySource) else Path(item) for item in kg_dirs)
        kg_dirs = ()
    directory_sources = [
        item if isinstance(item, KgDirectorySource) else KgDirectorySource(Path(item))
        for item in kg_dirs
    ]
    paths = [*(source.path for source in directory_sources), *kg_zips]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("KG source not found: " + ", ".join(missing))

    store = KgStore(
        kg_dirs=[str(source.path) for source in directory_sources],
        kg_zips=[str(path) for path in kg_zips],
    )
    for source in directory_sources:
        load_kg_dir(store, source)
    for path in kg_zips:
        with zipfile.ZipFile(path) as zf:
            for name in sorted(zf.namelist()):
                normalized = name.replace("\\", "/").strip("/")
                filename = normalized.rsplit("/", 1)[-1]
                if filename not in KG_FILENAMES:
                    continue
                doc_hint = doc_hint_from_member(normalized, filename)
                for record_number, row in enumerate(read_zip_jsonl(zf, name), start=1):
                    add_row(store, filename, row, doc_hint, provenance={
                        "source_id": str(path.resolve()), "archive": str(path),
                        "file": name, "record_number": record_number,
                    })
    return store


def load_kg_dir(store: KgStore, source: KgDirectorySource) -> None:
    path = source.path
    for filename in sorted(KG_FILENAMES):
        for table_path in sorted(path.rglob(filename)):
            doc_hint = doc_hint_from_path(table_path, path)
            for record_number, row in enumerate(read_jsonl_path(table_path), start=1):
                add_row(store, filename, row, doc_hint, source, provenance={
                    "file": str(table_path), "record_number": record_number,
                })


def read_jsonl_path(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def doc_hint_from_member(member: str, filename: str) -> str | None:
    parts = member.split("/")
    if member == filename:
        return None
    if len(parts) == 2 and parts[0] in {"kg", "merged_kg_pack"}:
        return None
    if len(parts) >= 2:
        return parts[-2]
    return None


def doc_hint_from_path(table_path: Path, root: Path) -> str | None:
    relative_parts = table_path.relative_to(root).parts
    if "patents" not in relative_parts:
        return None
    index = relative_parts.index("patents")
    if len(relative_parts) >= index + 4 and relative_parts[index + 2] == "kg_pack":
        return relative_parts[index + 1]
    return None


def read_zip_jsonl(zf: zipfile.ZipFile, name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in zf.read(name).decode("utf-8-sig", "replace").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def add_row(
    store: KgStore,
    filename: str,
    row: Mapping[str, Any],
    doc_hint: str | None,
    source: KgDirectorySource | None = None,
    *,
    provenance: Mapping[str, Any] | None = None,
) -> None:
    origin = dict(provenance or {})
    if source:
        origin.update(source_id=str(source.path.resolve()), collection_id=source.collection_id,
                      company=source.company, id_schema_version=source.id_schema_version)
    origin.setdefault("file", filename)
    if filename == "hyperedges.jsonl":
        raw = dict(row)
        if source and source.collection_id:
            doc_id = doc_id_from(raw, doc_hint)
            hyperedge_id = text_or_none(raw.get("hyperedge_id"))
            if doc_id and hyperedge_id:
                if source.id_schema_version == "retrieval_object_v2":
                    if source.company:
                        raise ValueError("retrieval_object_v2 KG source must not include company")
                    object_id = f"{source.collection_id}::{doc_id}::{hyperedge_id}"
                elif source.company:
                    object_id = f"{source.collection_id}::{source.company}::{doc_id}::{hyperedge_id}"
                else:
                    raise ValueError("legacy namespaced KG source requires company")
                existing_object_id = text_or_none(raw.get("object_id"))
                if existing_object_id and existing_object_id != object_id:
                    raise ValueError(
                        f"conflicting object_id for {doc_id}/{hyperedge_id}: {existing_object_id!r} != {object_id!r}"
                    )
                raw["object_id"] = object_id
        if raw.get("object_id"):
            origin["object_id"] = raw["object_id"]
        store.add_hyperedge(raw, doc_hint, provenance=origin, original=row)
    elif filename == "facts.jsonl":
        store.add_fact(row, doc_hint, provenance=origin)
    elif filename == "evidence_units.jsonl":
        store.add_evidence(row, doc_hint, provenance=origin)
    elif filename == "patents.jsonl":
        store.add_patent(row, doc_hint, provenance=origin)
    elif filename == "patent_profiles.jsonl":
        store.add_patent_profile(row, doc_hint, provenance=origin)


def build_output(
    *,
    object_ids: Sequence[str],
    db_rows: Mapping[str, dict[str, Any]],
    store: KgStore,
    kg_dirs: Sequence[Path],
    kg_zips: Sequence[Path],
    pg_env: Path,
    max_context_facts: int,
    evidence_mode: str = "compact",
) -> dict[str, Any]:
    if evidence_mode not in {"compact", "full"}:
        raise ValueError("evidence_mode must be compact or full")
    results = [
        expand_one(object_id, db_rows.get(object_id), store, max_context_facts=max_context_facts,
                   evidence_mode=evidence_mode)
        for object_id in object_ids
    ]
    summary = {
        "requested": len(object_ids),
        "db_found": sum(1 for row in results if row["db_hyperedge"] is not None),
        "db_missing": sum(1 for row in results if row["db_hyperedge"] is None),
        "hyperedge_raw_found": sum(1 for row in results if row["hyperedge"]["found"]),
        "fact_count": sum(len(row["facts"]) for row in results),
        "evidence_count": sum(len(row["evidence"]) for row in results),
        "patent_found": sum(1 for row in results if row["patent"]["found"]),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "object_ids": list(object_ids),
            "kg_dirs": [str(path) for path in kg_dirs],
            "kg_zips": [str(path) for path in kg_zips],
            "pg_env": str(pg_env),
        },
        "summary": summary,
        "results": results,
    }


def expand_one(
    object_id: str,
    db_row: Mapping[str, Any] | None,
    store: KgStore,
    *,
    max_context_facts: int,
    evidence_mode: str = "compact",
) -> dict[str, Any]:
    if evidence_mode == "full":
        return expand_one_full(object_id, db_row, store, max_context_facts=max_context_facts)
    if evidence_mode != "compact":
        raise ValueError("evidence_mode must be compact or full")
    if db_row is None:
        return empty_result(object_id)

    doc_id = text_or_none(db_row.get("doc_id"))
    metadata = normalize_metadata(db_row.get("metadata"))
    raw_hyperedge_id = resolve_raw_hyperedge_id(object_id, doc_id, metadata)
    raw_hyperedge = find_raw_hyperedge(store, doc_id, object_id, raw_hyperedge_id, metadata)
    if raw_hyperedge is not None:
        raw_hyperedge_id = text_or_none(raw_hyperedge.get("hyperedge_id")) or raw_hyperedge_id

    facts, unresolved_fact_ids = expand_facts(
        store,
        doc_id,
        object_id,
        raw_hyperedge_id,
        metadata,
        raw_hyperedge,
        max_context_facts=max_context_facts,
    )
    evidence, unresolved_evidence_ids = expand_evidence(store, doc_id, metadata, raw_hyperedge)
    patent = store.patents_by_doc.get(doc_id or "")
    patent_profile = store.patent_profiles_by_doc.get(doc_id or "")

    return {
        "object_id": object_id,
        "doc_id": doc_id,
        "db_hyperedge": {
            "object_type": db_row.get("object_type"),
            "object_id": db_row.get("object_id"),
            "doc_id": db_row.get("doc_id"),
            "text_for_embedding": db_row.get("text_for_embedding"),
            "metadata": metadata,
        },
        "hyperedge": {
            "found": raw_hyperedge is not None,
            "raw_hyperedge_id": raw_hyperedge_id,
            "raw": raw_hyperedge,
        },
        "facts": facts,
        "evidence": evidence,
        "patent": {"found": patent is not None, "raw": patent},
        "patent_profile": {"found": patent_profile is not None, "raw": patent_profile},
        "unresolved": {
            "fact_ids": unresolved_fact_ids,
            "evidence_ids": unresolved_evidence_ids,
        },
    }


def full_record_result(record: Mapping[str, Any], kind: str, match_mode: str) -> dict[str, Any]:
    id_field = {"facts": "fact_id", "evidence": "evidence_id"}.get(kind, "id")
    return {
        id_field: record["id"], "match_mode": match_mode, "raw": record["raw"],
        "provenance": record["provenance"], "origins": record["origins"],
    }


def expand_one_full(
    object_id: str, db_row: Mapping[str, Any] | None, store: KgStore, *, max_context_facts: int,
) -> dict[str, Any]:
    """Expand the direct closure first; context is a separately budgeted supplement."""
    result = empty_result(object_id)
    doc_id = text_or_none(db_row.get("doc_id")) if db_row else None
    metadata = normalize_metadata(db_row.get("metadata")) if db_row else {}
    raw_id = resolve_raw_hyperedge_id(object_id, doc_id, metadata)
    unresolved: dict[str, Any] = {
        "fact_ids": [], "evidence_ids": [], "conflicts": [],
        "db_object_ids": [] if db_row else [object_id], "hyperedge_ids": [],
        "patent_doc_ids": [],
    }
    supplementary_unresolved: dict[str, Any] = {
        "context_fact_ids": [], "context_evidence_ids": [], "patent_profile_doc_ids": [],
        "conflicts": [], "dropped_context_facts": [],
    }

    def records(kind: str, record_id: str) -> list[dict[str, Any]]:
        return store.full_records.get((kind, doc_id or "", record_id), []) if db_row else []

    candidates = []
    for candidate_id in unique_texts(raw_id, object_id.rsplit("::", 1)[-1],
                                     metadata.get("raw_hyperedge_id"), metadata.get("hyperedge_id"), object_id):
        candidates = records("hyperedges", candidate_id)
        if candidates:
            break

    # Use the existing retrieval object identity to select provenance, without rewriting IDs.
    suffix = f"::{doc_id}::{object_id.rsplit('::', 1)[-1]}"
    namespace = object_id[:-len(suffix)] if object_id.endswith(suffix) else None

    def matches_request_scope(row: dict[str, Any]) -> bool:
        origin = row["provenance"]
        collection = origin.get("collection_id")
        if not collection:
            return True
        if namespace:
            return namespace == "::".join(str(value) for value in (collection, origin.get("company")) if value)
        return not metadata.get("collection_id") or (
            metadata["collection_id"] == collection
            and (not metadata.get("company") or metadata["company"] == origin.get("company"))
        )

    candidates = [row for row in candidates if matches_request_scope(row)]
    exact = [row for row in candidates
             if object_id in (row["provenance"].get("object_id"), row["raw"].get("object_id"))]
    requested_sources: set[str | None] = set()
    for kind in ("hyperedges", "facts", "evidence", "patent", "patent_profile"):
        for row in store.full_records_by_doc.get((kind, doc_id or ""), []) if db_row else []:
            origin = row["provenance"]
            collection = origin.get("collection_id")
            if not collection:
                continue
            if matches_request_scope(row):
                requested_sources.add(origin["source_id"])
    if exact:
        candidates = exact
    elif requested_sources:
        candidates = [row for row in candidates if row["provenance"]["source_id"] in requested_sources]

    def report_conflicts(
        kind: str, rows: Sequence[dict[str, Any]], issues: dict[str, Any] | None = None,
    ) -> None:
        issues = unresolved if issues is None else issues
        groups: dict[tuple[Any, Any], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row["id"] is not None:
                groups[(row["provenance"]["source_id"], row["id"])].append(row)
        for (source_id, record_id), variants in groups.items():
            if len(variants) > 1:
                issues["conflicts"].append({
                    "entity_type": kind, "doc_id": doc_id, "source_id": source_id, "id": record_id,
                    "variants": [full_record_result(row, kind, "conflicting_id") for row in variants],
                })

    candidate_sources = {row["provenance"]["source_id"] for row in candidates}
    scopes = candidate_sources or requested_sources
    if len(scopes) > 1:
        unresolved["conflicts"].append({
            "entity_type": "hyperedges", "doc_id": doc_id, "id": raw_id,
            "reason": "ambiguous_source", "source_ids": sorted(scopes, key=str),
            "variants": [full_record_result(row, "hyperedges", "ambiguous_source") for row in candidates],
        })
        # Keep one source's partial result; never bind its IDs to another source's rows.
        scopes = {candidates[0]["provenance"]["source_id"]} if candidates else {sorted(scopes, key=str)[0]}

    def in_scope(row: dict[str, Any]) -> bool:
        return matches_request_scope(row) and (not scopes or row["provenance"]["source_id"] in scopes)

    candidates = [row for row in candidates if in_scope(row)]
    report_conflicts("hyperedges", candidates)
    raw_hyperedges = [row["raw"] for row in candidates]
    raw_hyperedge = raw_hyperedges[0] if raw_hyperedges else None
    if raw_hyperedge is not None:
        raw_id = text_or_none(raw_hyperedge.get("hyperedge_id")) or raw_id
    else:
        unresolved["hyperedge_ids"] = [raw_id]

    def references(rows: Sequence[Mapping[str, Any]], *keys: str) -> list[str]:
        return unique_texts(*(row.get(key) for row in rows for key in keys))

    def resolve(
        kind: str, ids: Sequence[str], issues: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        issues = unresolved if issues is None else issues
        found, missing = [], []
        for record_id in ids:
            variants = [row for row in records(kind, record_id) if in_scope(row)]
            if variants:
                found.extend(variants)
                if len({row["provenance"]["source_id"] for row in variants}) > 1:
                    issues["conflicts"].append({
                        "entity_type": kind, "doc_id": doc_id, "id": record_id,
                        "reason": "ambiguous_source",
                        "variants": [full_record_result(row, kind, "ambiguous_source") for row in variants],
                    })
            else:
                missing.append(record_id)
        return found, missing

    direct_fact_ids = references([metadata, *raw_hyperedges], "fact_ids")
    facts, unresolved["fact_ids"] = resolve("facts", direct_fact_ids)
    direct_facts_found = len(direct_fact_ids) - len(unresolved["fact_ids"])
    fact_modes = {id(row): "fact_ids" for row in facts}
    source_ids = set(unique_texts(raw_id, object_id.rsplit("::", 1)[-1], object_id,
                                  *(raw.get("hyperedge_id") for raw in raw_hyperedges)))
    doc_facts = [row for row in store.full_records_by_doc.get(("facts", doc_id or ""), [])
                 if db_row and in_scope(row)]
    for row in doc_facts:
        if source_ids.intersection(unique_texts(row["raw"].get("source_hyperedge_id"))) and id(row) not in fact_modes:
            facts.append(row)
            fact_modes[id(row)] = "source_hyperedge_id"
    core_keys = {(row["provenance"]["source_id"], row["id"]) for row in facts if row["id"]}
    for row in doc_facts:
        if (row["provenance"]["source_id"], row["id"]) in core_keys and id(row) not in fact_modes:
            facts.append(row)
            fact_modes[id(row)] = "conflicting_id"
    report_conflicts("facts", facts)

    context_ids = set(references([metadata, *raw_hyperedges], "context_id"))
    context_candidates = [row for row in doc_facts if id(row) not in fact_modes
                          and context_ids.intersection(unique_texts(row["raw"].get("context_id")))]
    context_limit = max(max_context_facts, 0)
    context_facts = context_candidates[:context_limit]
    context_selected_count = len(context_facts)
    # Report conflicts even if a supplementary limit would hide a conflicting variant.
    context_keys = {(row["provenance"]["source_id"], row["id"]) for row in context_facts if row["id"]}
    report_conflicts("facts", [row for row in doc_facts
                               if (row["provenance"]["source_id"], row["id"]) in context_keys],
                     supplementary_unresolved)

    evidence_ids = references([metadata, *raw_hyperedges, *(row["raw"] for row in facts)],
                              "evidence_ids", "evidence_id")
    evidence, unresolved["evidence_ids"] = resolve("evidence", evidence_ids)
    report_conflicts("evidence", evidence)
    context_evidence_ids = [value for value in references([row["raw"] for row in context_facts],
                                                         "evidence_ids", "evidence_id")
                            if value not in evidence_ids]
    context_evidence, supplementary_unresolved["context_evidence_ids"] = resolve(
        "evidence", context_evidence_ids, supplementary_unresolved,
    )
    report_conflicts("evidence", context_evidence, supplementary_unresolved)
    context_evidence_found = len(context_evidence)

    # Supplementary failures must not invalidate direct evidence or enter verified context.
    all_conflicts = [*unresolved["conflicts"], *supplementary_unresolved["conflicts"]]
    unsafe_fact_ids = {issue["id"] for issue in all_conflicts if issue["entity_type"] == "facts"}
    unsafe_evidence_ids = {
        *unresolved["evidence_ids"], *supplementary_unresolved["context_evidence_ids"],
        *(issue["id"] for issue in all_conflicts if issue["entity_type"] == "evidence"),
    }
    safe_context_facts = []
    for row in context_facts:
        support_ids = references([row["raw"]], "evidence_ids", "evidence_id")
        unsafe_support = [value for value in support_ids if value in unsafe_evidence_ids]
        reason = (
            "conflicting_fact_id" if row["id"] in unsafe_fact_ids else
            "missing_evidence_binding" if not support_ids else
            "unresolved_evidence" if unsafe_support else None
        )
        if reason:
            supplementary_unresolved["dropped_context_facts"].append({
                "fact_id": row["id"], "provenance": row["provenance"], "reason": reason,
                "evidence_ids": unsafe_support,
            })
        else:
            safe_context_facts.append(row)
    context_facts = safe_context_facts
    supplementary_unresolved["context_fact_ids"] = unique_texts(
        [row["fact_id"] for row in supplementary_unresolved["dropped_context_facts"]],
    )
    retained_context_evidence_ids = set(references([row["raw"] for row in context_facts],
                                                 "evidence_ids", "evidence_id"))
    context_evidence = [row for row in context_evidence if row["id"] in retained_context_evidence_ids
                        and row["id"] not in unsafe_evidence_ids]

    profile_available_count = 0
    for kind in ("patent", "patent_profile"):
        issues = supplementary_unresolved if kind == "patent_profile" else unresolved
        rows, _ = resolve(kind, [doc_id] if doc_id else [], issues)
        report_conflicts(kind, rows, issues)
        if not rows:
            issues[f"{kind}_doc_ids"] = [doc_id] if doc_id else []
        if kind == "patent_profile":
            profile_available_count = len(rows)
            if any(issue["entity_type"] == kind for issue in issues["conflicts"]):
                rows = []
        result[kind] = {"found": bool(rows), "raw": rows[0]["raw"] if rows else None,
                        "provenance": rows[0]["provenance"] if rows else None}

    result.update({
        "doc_id": doc_id, "db_hyperedge": {**db_row, "metadata": metadata} if db_row else None,
        "hyperedge": {"found": raw_hyperedge is not None, "raw_hyperedge_id": raw_id,
                      "raw": raw_hyperedge, "provenance": candidates[0]["provenance"] if candidates else None},
        "facts": [full_record_result(row, "facts", fact_modes[id(row)]) for row in facts],
        "evidence": [full_record_result(row, "evidence", "evidence_ids") for row in evidence],
        "context_facts": [full_record_result(row, "facts", "context_id") for row in context_facts],
        "context_evidence": [full_record_result(row, "evidence", "context_fact_evidence_ids") for row in context_evidence],
        "unresolved": unresolved,
        "supplementary_unresolved": supplementary_unresolved,
        "coverage": {
            "complete": not any(unresolved.values()),
            "direct_complete": not any(unresolved.values()),
            "supplementary_complete": not any(supplementary_unresolved.values())
                                      and len(context_candidates) == len(context_facts),
            "db_found": db_row is not None, "hyperedge_found": raw_hyperedge is not None,
            "patent_found": result["patent"]["found"], "patent_profile_found": result["patent_profile"]["found"],
            "facts": {"referenced": len(direct_fact_ids), "resolved": direct_facts_found,
                      "missing": len(unresolved["fact_ids"]), "returned": len(facts),
                      "source_linked": sum(mode == "source_hyperedge_id" for mode in fact_modes.values())},
            "evidence": {"referenced": len(evidence_ids), "resolved": len(evidence_ids) - len(unresolved["evidence_ids"]),
                         "missing": len(unresolved["evidence_ids"]), "returned": len(evidence)},
            "context_facts": {"available": len(context_candidates), "selected": context_selected_count,
                              "returned": len(context_facts), "dropped": context_selected_count - len(context_facts),
                              "limit": context_limit, "truncated": len(context_candidates) > context_selected_count},
            "context_evidence": {"referenced": len(context_evidence_ids), "available": context_evidence_found,
                                 "returned": len(context_evidence), "dropped": context_evidence_found - len(context_evidence),
                                 "missing": len(supplementary_unresolved["context_evidence_ids"])},
            "patent_profile": {"available": profile_available_count, "returned": int(result["patent_profile"]["found"]),
                               "dropped": profile_available_count - int(result["patent_profile"]["found"]),
                               "missing": len(supplementary_unresolved["patent_profile_doc_ids"])},
            "conflict_count": len(unresolved["conflicts"]),
            "supplementary_conflict_count": len(supplementary_unresolved["conflicts"]),
        },
    })
    return result


def empty_result(object_id: str) -> dict[str, Any]:
    return {
        "object_id": object_id,
        "doc_id": None,
        "db_hyperedge": None,
        "hyperedge": {"found": False, "raw_hyperedge_id": None, "raw": None},
        "facts": [],
        "evidence": [],
        "patent": {"found": False, "raw": None},
        "patent_profile": {"found": False, "raw": None},
        "unresolved": {"fact_ids": [], "evidence_ids": []},
    }


def resolve_raw_hyperedge_id(
    object_id: str,
    doc_id: str | None,
    metadata: Mapping[str, Any],
) -> str:
    raw = text_or_none(metadata.get("raw_hyperedge_id") or metadata.get("hyperedge_id"))
    if raw:
        return raw
    if "::" in object_id:
        return object_id.rsplit("::", 1)[1]
    return object_id


def find_raw_hyperedge(
    store: KgStore,
    doc_id: str | None,
    object_id: str,
    raw_hyperedge_id: str,
    metadata: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not doc_id:
        return None
    candidate_ids = unique_texts(
        raw_hyperedge_id,
        object_id.rsplit("::", 1)[1] if "::" in object_id else None,
        metadata.get("raw_hyperedge_id"),
        metadata.get("hyperedge_id"),
        object_id,
    )
    for candidate in candidate_ids:
        raw = store.hyperedges.get((doc_id, candidate))
        if raw is not None:
            return raw
    return None


def expand_facts(
    store: KgStore,
    doc_id: str | None,
    object_id: str,
    raw_hyperedge_id: str,
    metadata: Mapping[str, Any],
    raw_hyperedge: Mapping[str, Any] | None,
    *,
    max_context_facts: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not doc_id:
        return [], []

    facts: list[dict[str, Any]] = []
    seen: set[str] = set()
    direct_fact_ids = unique_texts(metadata.get("fact_ids"), raw_hyperedge.get("fact_ids") if raw_hyperedge else None)
    unresolved = []
    for fact_id in direct_fact_ids:
        raw = store.facts_by_id.get((doc_id, fact_id))
        if raw is None:
            unresolved.append(fact_id)
            continue
        add_fact_result(facts, seen, raw, "fact_ids")

    source_ids = unique_texts(
        raw_hyperedge_id,
        raw_hyperedge.get("hyperedge_id") if raw_hyperedge else None,
        object_id.rsplit("::", 1)[1] if "::" in object_id else None,
        object_id,
    )
    for source_id in source_ids:
        for raw in store.facts_by_source_hyperedge.get((doc_id, source_id), []):
            add_fact_result(facts, seen, raw, "source_hyperedge_id")

    if not facts:
        context_id = text_or_none(
            metadata.get("context_id") or (raw_hyperedge.get("context_id") if raw_hyperedge else None)
        )
        if context_id:
            for raw in store.facts_by_context.get((doc_id, context_id), [])[: max(max_context_facts, 0)]:
                add_fact_result(facts, seen, raw, "context_id")
    return facts, unresolved


def add_fact_result(
    facts: list[dict[str, Any]],
    seen: set[str],
    raw: Mapping[str, Any],
    match_mode: str,
) -> None:
    fact_id = text_or_none(raw.get("fact_id") or raw.get("id")) or f"anon_fact_{len(facts)}"
    if fact_id in seen:
        return
    seen.add(fact_id)
    facts.append({"fact_id": fact_id, "match_mode": match_mode, "raw": dict(raw)})


def expand_evidence(
    store: KgStore,
    doc_id: str | None,
    metadata: Mapping[str, Any],
    raw_hyperedge: Mapping[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not doc_id:
        return [], []

    evidence_ids = unique_texts(
        metadata.get("evidence_ids"),
        raw_hyperedge.get("evidence_ids") if raw_hyperedge else None,
    )
    evidence = []
    unresolved = []
    for evidence_id in evidence_ids:
        raw = store.evidence_by_id.get((doc_id, evidence_id))
        if raw is None:
            unresolved.append(evidence_id)
            continue
        evidence.append({"evidence_id": evidence_id, "match_mode": "evidence_ids", "raw": raw})
    return evidence, unresolved


def doc_id_from(row: Mapping[str, Any], doc_hint: str | None = None) -> str | None:
    return text_or_none(
        row.get("doc_id")
        or row.get("patent_id")
        or row.get("publication_number")
        or row.get("publication_id")
        or doc_hint
    )


def unique_texts(*values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in flatten_texts(value):
            if item not in seen:
                seen.add(item)
                out.append(item)
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
        for key in ("id", "fact_id", "evidence_id", "hyperedge_id"):
            text = text_or_none(value.get(key))
            if text:
                yield text
                return
        return
    if isinstance(value, Iterable):
        for item in value:
            yield from flatten_texts(item)
        return
    text = text_or_none(value)
    if text:
        yield text


def text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def write_json(path: Path, payload: Any, *, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    indent = 2 if pretty else None
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
