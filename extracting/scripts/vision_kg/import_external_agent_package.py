"""Import an externally produced agent-native KG archive without semantic rewriting.

The importer copies each required JSONL member byte-for-byte into the local
agent-native batch layout.  It may validate JSON syntax, count records, and
check PDF hashes for provenance, but it never changes a fact, entity, edge, or
hyperedge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


KG_JSONL_FILES = (
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

RUN_SCHEMA_VERSION = "external_agent_package_import_v1"
DOC_ID_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def import_package(archive_path: Path | str, run_dir: Path | str) -> dict[str, Any]:
    """Stage a complete external archive into the local nine-JSONL run layout."""

    source_archive = Path(archive_path).resolve()
    target_dir = Path(run_dir).resolve()
    if not source_archive.is_file():
        raise ValueError(f"archive does not exist: {source_archive}")
    if target_dir.exists():
        raise FileExistsError(f"refusing to overwrite run directory: {target_dir}")
    if target_dir.parent.exists() and not target_dir.parent.is_dir():
        raise ValueError(f"run directory parent is not a directory: {target_dir.parent}")

    staging_dir = target_dir.with_name(f".{target_dir.name}.importing")
    if staging_dir.exists():
        raise FileExistsError(f"staging directory already exists: {staging_dir}")
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    archive_sha256 = _sha256_file(source_archive)

    with zipfile.ZipFile(source_archive) as archive:
        document_ids = _document_ids(archive)
        _require_document_members(archive, document_ids)
        staging_dir.mkdir()
        (staging_dir / "patents").mkdir()
        (staging_dir / "data" / "evidence_assets").mkdir(parents=True)
        source_metadata_files = _copy_root_metadata(archive, staging_dir / "source_package")

        documents: list[dict[str, Any]] = []
        run_documents: dict[str, str] = {}
        pdf_hash_mismatches: list[dict[str, str]] = []
        for doc_id in document_ids:
            document, pdf_mismatch = _import_document(
                archive,
                staging_dir,
                doc_id,
                source_archive,
                archive_sha256,
            )
            documents.append(document)
            run_documents[doc_id] = "imported_with_pdf_hash_mismatch" if pdf_mismatch else "imported"
            if pdf_mismatch:
                pdf_hash_mismatches.append(pdf_mismatch)

        manifest = {
            "schema_version": RUN_SCHEMA_VERSION,
            "created_at_utc": _utc_now(),
            "source_archive": str(source_archive),
            "source_archive_sha256": archive_sha256,
            "source_metadata_files": source_metadata_files,
            "asset_mode": "copied_from_archive",
            "pdf_count": len(documents),
            "documents": documents,
            "kg_jsonl_files": list(KG_JSONL_FILES),
            "pdf_sha256_mismatches": pdf_hash_mismatches,
            "semantic_boundary": (
                "External agent-authored JSONL records were copied byte-for-byte; "
                "this importer performs only file, syntax, count, and hash checks."
            ),
        }
        _write_json(staging_dir / "input_manifest.json", manifest)
        _write_json(
            staging_dir / "run_status.json",
            {
                "schema_version": RUN_SCHEMA_VERSION,
                "created_at_utc": _utc_now(),
                "counts": {
                    "imported": len(documents),
                    "complete_packs": len(documents),
                    "pdf_sha256_match": len(documents) - len(pdf_hash_mismatches),
                    "pdf_sha256_mismatch": len(pdf_hash_mismatches),
                },
                "documents": run_documents,
            },
        )

    staging_dir.rename(target_dir)
    return manifest


def _document_ids(archive: zipfile.ZipFile) -> list[str]:
    document_ids = {
        parts[1]
        for info in archive.infolist()
        if not info.is_dir()
        for parts in [PurePosixPath(info.filename).parts]
        if len(parts) == 3 and parts[0] == "documents"
    }
    if not document_ids:
        raise ValueError("archive contains no documents/<doc_id>/ files")
    for doc_id in document_ids:
        if not doc_id or any(char not in DOC_ID_ALLOWED for char in doc_id):
            raise ValueError(f"unsupported document id in archive: {doc_id!r}")
    return sorted(document_ids, key=str.casefold)


def _require_document_members(archive: zipfile.ZipFile, document_ids: list[str]) -> None:
    members = set(archive.namelist())
    for doc_id in document_ids:
        required = [
            *(f"documents/{doc_id}/{name}" for name in KG_JSONL_FILES),
            f"documents/{doc_id}/doc_manifest.json",
            f"original_pdfs/{doc_id}.pdf",
        ]
        missing = [member for member in required if member not in members]
        if missing:
            raise ValueError(f"document {doc_id} missing archive members: {', '.join(missing)}")


def _copy_root_metadata(archive: zipfile.ZipFile, destination: Path) -> list[str]:
    copied: list[str] = []
    for info in archive.infolist():
        if info.is_dir() or len(PurePosixPath(info.filename).parts) != 1:
            continue
        target = destination / info.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(archive.read(info))
        copied.append(info.filename)
    return sorted(copied, key=str.casefold)


def _import_document(
    archive: zipfile.ZipFile,
    staging_dir: Path,
    doc_id: str,
    source_archive: Path,
    archive_sha256: str,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    pack_dir = staging_dir / "patents" / doc_id / "kg_pack"
    pack_dir.mkdir(parents=True)
    counts: dict[str, int] = {}
    for file_name in KG_JSONL_FILES:
        source_member = f"documents/{doc_id}/{file_name}"
        content = archive.read(source_member)
        counts[file_name.removesuffix(".jsonl")] = _validate_and_count_jsonl(content, source_member)
        (pack_dir / file_name).write_bytes(content)

    doc_manifest_member = f"documents/{doc_id}/doc_manifest.json"
    doc_manifest_bytes = archive.read(doc_manifest_member)
    doc_manifest = _json_object(doc_manifest_bytes, doc_manifest_member)
    source_manifest_target = staging_dir / "patents" / doc_id / "agent_outputs" / "source_doc_manifest.json"
    source_manifest_target.parent.mkdir(parents=True, exist_ok=True)
    source_manifest_target.write_bytes(doc_manifest_bytes)

    pdf_member = f"original_pdfs/{doc_id}.pdf"
    pdf_bytes = archive.read(pdf_member)
    pdf_sha256 = _sha256_bytes(pdf_bytes)
    pdf_target = staging_dir / "data" / "evidence_assets" / doc_id / "pdf" / "original.pdf"
    pdf_target.parent.mkdir(parents=True, exist_ok=True)
    pdf_target.write_bytes(pdf_bytes)

    declared_pdf_sha256 = str(doc_manifest.get("source_pdf_sha256") or "")
    pdf_mismatch: dict[str, str] | None = None
    if declared_pdf_sha256 and pdf_sha256.casefold() != declared_pdf_sha256.casefold():
        pdf_mismatch = {
            "patent_id": doc_id,
            "declared_source_pdf_sha256": declared_pdf_sha256,
            "archived_pdf_sha256": pdf_sha256,
        }

    pack_manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "patent_id": doc_id,
        "counts": counts,
        "source": {
            "archive": str(source_archive),
            "archive_sha256": archive_sha256,
            "document_dir": f"documents/{doc_id}",
            "doc_manifest": doc_manifest_member,
            "original_pdf": pdf_member,
            "archived_pdf_sha256": pdf_sha256,
            "declared_source_pdf_sha256": declared_pdf_sha256 or None,
        },
        "provenance_warnings": ["source_pdf_sha256_mismatch"] if pdf_mismatch else [],
        "semantic_boundary": "The nine JSONL files were copied from the external archive without record-level changes.",
    }
    _write_json(pack_dir / "manifest.json", pack_manifest)
    return (
        {
            "patent_id": doc_id,
            "source_pdf": str(pdf_target.resolve()),
            "source_pdf_name": f"{doc_id}.pdf",
            "source_pdf_archive_member": pdf_member,
            "source_doc_manifest_archive_member": doc_manifest_member,
            "archived_pdf_sha256": pdf_sha256,
            "declared_source_pdf_sha256": declared_pdf_sha256 or None,
        },
        pdf_mismatch,
    )


def _validate_and_count_jsonl(content: bytes, member: str) -> int:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 JSONL: {member}") from exc
    count = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL {member}:{line_no}: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL row is not an object: {member}:{line_no}")
        count += 1
    return count


def _json_object(content: bytes, member: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON object: {member}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {member}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an external agent-native KG archive without semantic rewriting.")
    parser.add_argument("archive", type=Path)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()

    manifest = import_package(args.archive, args.run_dir)
    print(
        json.dumps(
            {
                "run_dir": str(Path(args.run_dir).resolve()),
                "imported": manifest["pdf_count"],
                "pdf_sha256_mismatch": len(manifest["pdf_sha256_mismatches"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
