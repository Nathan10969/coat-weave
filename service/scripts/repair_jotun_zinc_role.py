from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TARGET_DOC = "WO2020157150A1"
TARGET_CANONICAL_ID = "MAT_zinc_dust"
ROLE_EVIDENCE_ID = "EU_WO2020157150A1_0001"
EXPECTED_FACTS = 22
EXPECTED_HYPEREDGES = 46


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def doc_id(row: dict[str, Any]) -> str:
    return str(
        row.get("doc_id")
        or row.get("patent_id")
        or row.get("publication_number")
        or ""
    ).strip()


def json_line(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"


def validate_role_evidence(path: Path) -> dict[str, Any]:
    match: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            row = json.loads(line)
            if doc_id(row) != TARGET_DOC:
                continue
            evidence_id = str(row.get("evidence_id") or row.get("id") or "")
            if evidence_id != ROLE_EVIDENCE_ID:
                continue
            text = str(row.get("quote") or row.get("text") or row.get("content") or "")
            folded = text.casefold()
            if "zinc dust = anticorrosive agent" not in folded:
                raise RuntimeError(
                    f"{ROLE_EVIDENCE_ID} does not contain the required direct role statement"
                )
            match = {
                "file": path.name,
                "line": line_number,
                "evidence_id": evidence_id,
                "page": row.get("page") or row.get("page_number"),
                "table": row.get("table") or row.get("table_id"),
                "statement": "Zinc dust = Anticorrosive agent",
            }
    if match is None:
        raise RuntimeError(f"required evidence {ROLE_EVIDENCE_ID} was not found")
    return match


def rewrite_facts(source: Path, target: Path) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    target_zinc_count = 0
    with source.open("r", encoding="utf-8-sig") as reader, target.open(
        "w", encoding="utf-8", newline=""
    ) as writer:
        for line_number, line in enumerate(reader, start=1):
            row = json.loads(line)
            is_target = (
                doc_id(row) == TARGET_DOC
                and row.get("material_canonical_id") == TARGET_CANONICAL_ID
            )
            if not is_target:
                writer.write(line.lstrip("\ufeff"))
                continue
            target_zinc_count += 1
            old_role = row.get("material_role")
            if old_role != "additive":
                raise RuntimeError(
                    f"unexpected role at facts.jsonl:{line_number}: {old_role!r}"
                )
            row["material_role"] = "pigment"
            writer.write(json_line(row))
            changes.append(
                {
                    "record_type": "fact",
                    "file": source.name,
                    "line": line_number,
                    "doc_id": TARGET_DOC,
                    "record_id": row.get("fact_id") or row.get("id"),
                    "context_id": row.get("context_id"),
                    "evidence_id": row.get("evidence_id"),
                    "field": "material_role",
                    "old_value": "additive",
                    "new_value": "pigment",
                    "basis_evidence_id": ROLE_EVIDENCE_ID,
                }
            )
    if target_zinc_count != EXPECTED_FACTS or len(changes) != EXPECTED_FACTS:
        raise RuntimeError(
            f"fact count mismatch: expected {EXPECTED_FACTS}, found {target_zinc_count}"
        )
    return changes


def rewrite_hyperedges(source: Path, target: Path) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8-sig") as reader, target.open(
        "w", encoding="utf-8", newline=""
    ) as writer:
        for line_number, line in enumerate(reader, start=1):
            row = json.loads(line)
            if doc_id(row) != TARGET_DOC:
                writer.write(line.lstrip("\ufeff"))
                continue

            additives = list(row.get("additives") or [])
            moved = [
                item
                for item in additives
                if isinstance(item, dict)
                and item.get("canonical_id") == TARGET_CANONICAL_ID
            ]
            if not moved:
                writer.write(line.lstrip("\ufeff"))
                continue
            if len(moved) != 1:
                raise RuntimeError(
                    f"expected one zinc dust additive at hyperedges.jsonl:{line_number}"
                )
            pigments = list(row.get("pigments") or [])
            if any(
                isinstance(item, dict)
                and item.get("canonical_id") == TARGET_CANONICAL_ID
                for item in pigments
            ):
                raise RuntimeError(
                    f"zinc dust already exists in pigments at hyperedges.jsonl:{line_number}"
                )
            row["additives"] = [item for item in additives if item not in moved]
            row["pigments"] = pigments + moved
            evidence_ids = list(row.get("evidence_ids") or [])
            if ROLE_EVIDENCE_ID not in evidence_ids:
                evidence_ids.append(ROLE_EVIDENCE_ID)
                row["evidence_ids"] = evidence_ids
            writer.write(json_line(row))
            changes.append(
                {
                    "record_type": "hyperedge",
                    "file": source.name,
                    "line": line_number,
                    "doc_id": TARGET_DOC,
                    "record_id": row.get("hyperedge_id") or row.get("id"),
                    "context_id": row.get("context_id"),
                    "field": "additives->pigments",
                    "old_value": moved[0],
                    "new_value": moved[0],
                    "basis_evidence_id": ROLE_EVIDENCE_ID,
                }
            )
    if len(changes) != EXPECTED_HYPEREDGES:
        raise RuntimeError(
            f"hyperedge count mismatch: expected {EXPECTED_HYPEREDGES}, found {len(changes)}"
        )
    return changes


def validate_jsonl(path: Path, expected_lines: int) -> None:
    count = 0
    with path.open("r", encoding="utf-8-sig") as handle:
        for count, line in enumerate(handle, start=1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise RuntimeError(f"non-object JSON at {path}:{count}")
    if count != expected_lines:
        raise RuntimeError(
            f"line count changed for {path.name}: expected {expected_lines}, found {count}"
        )


def line_count(path: Path) -> int:
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        for row in rows:
            handle.write(json_line(row))


def rewrite_manifest(
    source: Path,
    target: Path,
    proposed_files: dict[str, dict[str, Any]],
    applied_at: str,
) -> None:
    manifest = json.loads(source.read_text(encoding="utf-8-sig"))
    for name in ("facts.jsonl", "hyperedges.jsonl"):
        file_entry = manifest.setdefault("files", {}).setdefault(name, {})
        file_entry["bytes"] = proposed_files[name]["bytes"]
        file_entry["rows"] = proposed_files[name]["lines"]
        manifest.setdefault("counts", {})[name] = proposed_files[name]["lines"]
    repairs = manifest.setdefault("repairs", [])
    repairs.append(
        {
            "repair_id": "jotun_wo2020157150a1_zinc_role_20260804",
            "applied_at": applied_at,
            "doc_id": TARGET_DOC,
            "material_canonical_id": TARGET_CANONICAL_ID,
            "role_from": "additive",
            "role_to": "pigment",
            "basis_evidence_id": ROLE_EVIDENCE_ID,
            "fact_changes": EXPECTED_FACTS,
            "hyperedge_changes": EXPECTED_HYPEREDGES,
        }
    )
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_backup(backup_dir: Path, originals: dict[str, dict[str, Any]]) -> None:
    for name, metadata in originals.items():
        backup = backup_dir / name
        if not backup.is_file():
            raise RuntimeError(f"missing backup file: {backup}")
        if sha256(backup) != metadata["sha256"]:
            raise RuntimeError(f"backup hash mismatch: {backup}")


def verify_state(root: Path) -> dict[str, Any]:
    fact_count = 0
    fact_roles: set[str] = set()
    with (root / "facts.jsonl").open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            row = json.loads(line)
            if (
                doc_id(row) == TARGET_DOC
                and row.get("material_canonical_id") == TARGET_CANONICAL_ID
            ):
                fact_count += 1
                fact_roles.add(str(row.get("material_role")))

    pigment_hyperedges = 0
    additive_hyperedges = 0
    missing_role_evidence = 0
    with (root / "hyperedges.jsonl").open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            row = json.loads(line)
            if doc_id(row) != TARGET_DOC:
                continue
            in_pigments = any(
                isinstance(item, dict)
                and item.get("canonical_id") == TARGET_CANONICAL_ID
                for item in (row.get("pigments") or [])
            )
            in_additives = any(
                isinstance(item, dict)
                and item.get("canonical_id") == TARGET_CANONICAL_ID
                for item in (row.get("additives") or [])
            )
            if in_pigments:
                pigment_hyperedges += 1
                if ROLE_EVIDENCE_ID not in (row.get("evidence_ids") or []):
                    missing_role_evidence += 1
            if in_additives:
                additive_hyperedges += 1

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    manifest_files = manifest.get("files") or {}
    manifest_matches = all(
        (manifest_files.get(name) or {}).get("bytes") == (root / name).stat().st_size
        and (manifest_files.get(name) or {}).get("rows") == line_count(root / name)
        for name in ("facts.jsonl", "hyperedges.jsonl")
    )
    verification = {
        "fact_count": fact_count,
        "fact_roles": sorted(fact_roles),
        "pigment_hyperedge_count": pigment_hyperedges,
        "additive_hyperedge_count": additive_hyperedges,
        "missing_role_evidence_count": missing_role_evidence,
        "manifest_matches_files": manifest_matches,
    }
    expected = {
        "fact_count": EXPECTED_FACTS,
        "fact_roles": ["pigment"],
        "pigment_hyperedge_count": EXPECTED_HYPEREDGES,
        "additive_hyperedge_count": 0,
        "missing_role_evidence_count": 0,
        "manifest_matches_files": True,
    }
    if verification != expected:
        raise RuntimeError(f"post-repair verification failed: {verification}")
    return verification


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--audit-dir", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    audit_dir = args.audit_dir.resolve()
    audit_dir.mkdir(parents=True, exist_ok=True)
    facts = root / "facts.jsonl"
    hyperedges = root / "hyperedges.jsonl"
    evidence = root / "evidence_units.jsonl"
    manifest = root / "manifest.json"
    evidence_basis = validate_role_evidence(evidence)

    originals = {
        "facts.jsonl": {"sha256": sha256(facts), "lines": line_count(facts)},
        "hyperedges.jsonl": {
            "sha256": sha256(hyperedges),
            "lines": line_count(hyperedges),
        },
        "manifest.json": {"sha256": sha256(manifest), "bytes": manifest.stat().st_size},
    }
    if args.apply:
        if args.backup_dir is None:
            raise RuntimeError("--backup-dir is required with --apply")
        validate_backup(args.backup_dir.resolve(), originals)
    facts_tmp = facts.with_name(f".{facts.name}.jotun-zinc.tmp")
    hyperedges_tmp = hyperedges.with_name(f".{hyperedges.name}.jotun-zinc.tmp")
    manifest_tmp = manifest.with_name(f".{manifest.name}.jotun-zinc.tmp")

    try:
        fact_changes = rewrite_facts(facts, facts_tmp)
        hyperedge_changes = rewrite_hyperedges(hyperedges, hyperedges_tmp)
        validate_jsonl(facts_tmp, originals["facts.jsonl"]["lines"])
        validate_jsonl(hyperedges_tmp, originals["hyperedges.jsonl"]["lines"])
        proposed = {
            "facts.jsonl": {
                "sha256": sha256(facts_tmp),
                "lines": line_count(facts_tmp),
                "bytes": facts_tmp.stat().st_size,
            },
            "hyperedges.jsonl": {
                "sha256": sha256(hyperedges_tmp),
                "lines": line_count(hyperedges_tmp),
                "bytes": hyperedges_tmp.stat().st_size,
            },
        }
        applied_at = datetime.now(timezone.utc).isoformat()
        rewrite_manifest(manifest, manifest_tmp, proposed, applied_at)
        json.loads(manifest_tmp.read_text(encoding="utf-8"))
        proposed["manifest.json"] = {
            "sha256": sha256(manifest_tmp),
            "bytes": manifest_tmp.stat().st_size,
        }
        write_jsonl(audit_dir / "changes.jsonl", fact_changes + hyperedge_changes)
        report = {
            "status": "applied" if args.apply else "dry_run",
            "doc_id": TARGET_DOC,
            "role_change": {"from": "additive", "to": "pigment"},
            "fact_changes": len(fact_changes),
            "hyperedge_changes": len(hyperedge_changes),
            "evidence_basis": evidence_basis,
            "originals": originals,
            "proposed": proposed,
        }
        if args.apply:
            backup_dir = args.backup_dir.resolve()
            try:
                os.replace(facts_tmp, facts)
                os.replace(hyperedges_tmp, hyperedges)
                os.replace(manifest_tmp, manifest)
                verification = verify_state(root)
            except Exception:
                for name in ("facts.jsonl", "hyperedges.jsonl", "manifest.json"):
                    shutil.copy2(backup_dir / name, root / name)
                raise
            report["installed"] = {
                "facts.jsonl": {
                    "sha256": sha256(facts),
                    "lines": line_count(facts),
                    "bytes": facts.stat().st_size,
                },
                "hyperedges.jsonl": {
                    "sha256": sha256(hyperedges),
                    "lines": line_count(hyperedges),
                    "bytes": hyperedges.stat().st_size,
                },
                "manifest.json": {
                    "sha256": sha256(manifest),
                    "bytes": manifest.stat().st_size,
                },
            }
            report["verification"] = verification
        (audit_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        facts_tmp.unlink(missing_ok=True)
        hyperedges_tmp.unlink(missing_ok=True)
        manifest_tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
