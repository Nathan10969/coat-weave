"""Mechanically rename legacy hyperedge ``id`` fields to ``hyperedge_id``.

This migration preserves every semantic value. It only normalizes the record
identifier required by the retrieval indexer, saves the original JSONL under
``agent_outputs``, and records before/after hashes for audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def normalize_hyperedge_ids(kg_dir: Path | str) -> dict[str, Any]:
    """Rename each legacy ``id`` field in one pack's hyperedges JSONL in place."""

    pack_dir = Path(kg_dir).resolve()
    hyperedges_path = pack_dir / "hyperedges.jsonl"
    if not hyperedges_path.is_file():
        raise FileNotFoundError(f"missing hyperedges JSONL: {hyperedges_path}")

    original = hyperedges_path.read_bytes()
    normalized_rows: list[dict[str, Any]] = []
    changed_rows = 0
    for line_no, line in enumerate(original.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL {hyperedges_path}:{line_no}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"non-object JSONL row: {hyperedges_path}:{line_no}")

        hyperedge_id = row.get("hyperedge_id")
        legacy_id = row.get("id")
        if hyperedge_id not in (None, "") and legacy_id not in (None, "") and hyperedge_id != legacy_id:
            raise ValueError(f"conflicting id fields at {hyperedges_path}:{line_no}")
        if hyperedge_id in (None, ""):
            if legacy_id in (None, ""):
                raise ValueError(f"missing hyperedge identifier at {hyperedges_path}:{line_no}")
            normalized = {("hyperedge_id" if key == "id" else key): value for key, value in row.items()}
            changed_rows += 1
        else:
            normalized = {key: value for key, value in row.items() if key != "id"}
            changed_rows += int("id" in row)
        normalized_rows.append(normalized)

    after = "\n".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) for row in normalized_rows
    )
    if normalized_rows:
        after += "\n"
    after_bytes = after.encode("utf-8")

    audit_dir = pack_dir.parent / "agent_outputs"
    audit_dir.mkdir(parents=True, exist_ok=True)
    backup_path = audit_dir / "hyperedges.before_hyperedge_id_migration.jsonl"
    if changed_rows:
        if backup_path.exists():
            raise FileExistsError(f"migration backup already exists: {backup_path}")
        backup_path.write_bytes(original)
        hyperedges_path.write_bytes(after_bytes)

    report = {
        "schema_version": "legacy_hyperedge_id_normalization_v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "kg_dir": str(pack_dir),
        "hyperedges_path": str(hyperedges_path),
        "changed_rows": changed_rows,
        "row_count": len(normalized_rows),
        "original_sha256": _sha256(original),
        "normalized_sha256": _sha256(after_bytes),
        "backup_path": str(backup_path) if changed_rows else None,
        "semantic_boundary": "Only the record key id was renamed to hyperedge_id; all other values were preserved.",
    }
    (audit_dir / "hyperedge_id_migration.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize legacy hyperedge id fields in one KG pack.")
    parser.add_argument("kg_dir", type=Path)
    args = parser.parse_args()

    print(json.dumps(normalize_hyperedge_ids(args.kg_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
