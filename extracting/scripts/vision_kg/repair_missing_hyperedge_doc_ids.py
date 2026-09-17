"""Repair only hyperedges whose document identity is uniquely evidenced elsewhere.

This is a structural migration for merged KG packs.  It never derives a
document identifier from semantic text: a missing ``doc_id`` is populated only
when referenced evidence units and/or example contexts resolve to exactly one
existing document.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


JSONL_ARTIFACTS = (
    "patents.jsonl",
    "patent_profiles.jsonl",
    "evidence_units.jsonl",
    "example_contexts.jsonl",
    "facts.jsonl",
    "canonical_entities.jsonl",
    "canonical_relations.jsonl",
    "edges.jsonl",
    "hyperedges.jsonl",
)


def repair_missing_hyperedge_doc_ids(
    kg_dir: Path | str,
    *,
    expected_doc_id: str | None = None,
) -> dict[str, Any]:
    """Add a missing ``doc_id`` only when direct KG references prove it uniquely."""

    pack_dir = Path(kg_dir).resolve()
    hyperedges_path = pack_dir / "hyperedges.jsonl"
    evidence_path = pack_dir / "evidence_units.jsonl"
    contexts_path = pack_dir / "example_contexts.jsonl"
    manifest_path = pack_dir / "manifest.json"
    for path in (hyperedges_path, evidence_path, contexts_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(f"missing required file: {path}")

    evidence_docs = _document_index(_read_jsonl(evidence_path), "evidence_id")
    context_docs = _document_index(_read_jsonl(contexts_path), "context_id")
    original = hyperedges_path.read_bytes()
    newline = "\r\n" if b"\r\n" in original else "\n"
    repaired_lines: list[str] = []
    repairs: list[dict[str, Any]] = []

    for line_no, line in enumerate(original.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            repaired_lines.append(line)
            continue
        row = _parse_row(line, hyperedges_path, line_no)
        if _doc_id(row):
            repaired_lines.append(line)
            continue

        evidence_ids = _as_text_list(row.get("evidence_ids"))
        context_id = _as_text(row.get("context_id"))
        candidates = set()
        for evidence_id in evidence_ids:
            candidates.update(evidence_docs.get(evidence_id, set()))
        if context_id:
            candidates.update(context_docs.get(context_id, set()))
        if len(candidates) != 1:
            raise ValueError(
                f"cannot uniquely resolve document for {hyperedges_path}:{line_no}; "
                f"candidates={sorted(candidates)}"
            )
        resolved_doc_id = next(iter(candidates))
        if expected_doc_id is not None and resolved_doc_id != expected_doc_id:
            raise ValueError(
                f"unexpected document for {hyperedges_path}:{line_no}; "
                f"expected={expected_doc_id}, resolved={resolved_doc_id}"
            )
        row["doc_id"] = resolved_doc_id
        repaired_lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        repairs.append(
            {
                "line": line_no,
                "hyperedge_id": row.get("hyperedge_id"),
                "old_doc_id": None,
                "new_doc_id": resolved_doc_id,
                "context_id": context_id,
                "evidence_ids": evidence_ids,
                "resolution_basis": "unique document id from referenced evidence_units and/or example_contexts",
            }
        )

    repaired = (newline.join(repaired_lines) + newline).encode("utf-8") if repaired_lines else b""
    audit_dir = pack_dir.parent / "agent_outputs"
    audit_dir.mkdir(parents=True, exist_ok=True)
    backup_path = audit_dir / "hyperedges.before_doc_id_repair.jsonl"
    audit_path = audit_dir / "axalta_missing_hyperedge_doc_id_repair.json"
    if repairs:
        if backup_path.exists():
            raise FileExistsError(f"repair backup already exists: {backup_path}")
        backup_path.write_bytes(original)
        hyperedges_path.write_bytes(repaired)

    manifest_before = manifest_path.read_bytes()
    manifest = json.loads(manifest_before)
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest is not an object: {manifest_path}")
    artifact_sha256 = manifest.get("artifact_sha256")
    if artifact_sha256 is None:
        artifact_sha256 = {}
        manifest["artifact_sha256"] = artifact_sha256
    if not isinstance(artifact_sha256, dict):
        raise ValueError(f"manifest artifact_sha256 is not an object: {manifest_path}")
    for name in JSONL_ARTIFACTS:
        artifact_path = pack_dir / name
        if artifact_path.is_file():
            artifact_sha256[name] = _sha256(artifact_path.read_bytes())
    manifest["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["structural_repairs"] = [
        *(
            manifest.get("structural_repairs", [])
            if isinstance(manifest.get("structural_repairs"), list)
            else []
        ),
        {
            "name": "axalta_missing_hyperedge_doc_id_repair",
            "changed_rows": len(repairs),
            "audit_path": str(audit_path),
            "original_hyperedges_sha256": _sha256(original),
            "repaired_hyperedges_sha256": _sha256(repaired),
        },
    ]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {
        "schema_version": "merged_hyperedge_doc_id_repair_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "kg_dir": str(pack_dir),
        "hyperedges_path": str(hyperedges_path),
        "changed_rows": len(repairs),
        "original_hyperedges_sha256": _sha256(original),
        "repaired_hyperedges_sha256": _sha256(repaired),
        "manifest_before_sha256": _sha256(manifest_before),
        "manifest_after_sha256": _sha256(manifest_path.read_bytes()),
        "backup_path": str(backup_path) if repairs else None,
        "expected_doc_id": expected_doc_id,
        "repairs": repairs,
        "semantic_boundary": "Only missing doc_id values were populated from existing unique context/evidence references.",
    }
    audit_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def _document_index(rows: Iterable[dict[str, Any]], primary_id_key: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in rows:
        item_id = _as_text(row.get(primary_id_key) or row.get("id"))
        doc_id = _doc_id(row)
        if item_id and doc_id:
            out.setdefault(item_id, set()).add(doc_id)
    return out


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            yield _parse_row(line, path, line_no)


def _parse_row(line: str, path: Path, line_no: int) -> dict[str, Any]:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSONL {path}:{line_no}: {exc.msg}") from exc
    if not isinstance(row, dict):
        raise ValueError(f"non-object JSONL row: {path}:{line_no}")
    return row


def _doc_id(row: dict[str, Any]) -> str | None:
    return _as_text(row.get("doc_id") or row.get("patent_id") or row.get("publication_number"))


def _as_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _as_text_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value] if value is not None else []
    return [text for item in values if (text := _as_text(item))]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kg_dir", type=Path)
    parser.add_argument("--expected-doc-id")
    args = parser.parse_args()
    print(
        json.dumps(
            repair_missing_hyperedge_doc_ids(args.kg_dir, expected_doc_id=args.expected_doc_id),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
