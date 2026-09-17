from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest


def _load_import_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "vision_kg"
        / "import_external_agent_package.py"
    )
    spec = importlib.util.spec_from_file_location("import_external_agent_package", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


importer = _load_import_module()


def _write_archive(path: Path, *, declared_pdf_sha256: str) -> tuple[bytes, bytes]:
    pdf_bytes = b"pdf-original-bytes"
    jsonl_bytes = b'{"doc_id":"US1234567A","record_type":"external"}\r\n'
    doc_manifest = {
        "doc_id": "US1234567A",
        "source_pdf_sha256": declared_pdf_sha256,
        "extraction_method": "codex_direct_visual_pdf",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("PACKAGE_README.md", b"source package\n")
        archive.writestr("run_manifest.json", b'{"source":"external"}\n')
        archive.writestr("original_pdfs/US1234567A.pdf", pdf_bytes)
        archive.writestr("documents/US1234567A/doc_manifest.json", json.dumps(doc_manifest).encode("utf-8"))
        for file_name in importer.KG_JSONL_FILES:
            archive.writestr(f"documents/US1234567A/{file_name}", jsonl_bytes)
    return pdf_bytes, jsonl_bytes


def test_import_copies_external_jsonl_and_original_assets_byte_for_byte(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    pdf_bytes, jsonl_bytes = _write_archive(archive, declared_pdf_sha256=hashlib.sha256(b"pdf-original-bytes").hexdigest())

    run_dir = tmp_path / "imported"
    manifest = importer.import_package(archive, run_dir)

    pack = run_dir / "patents" / "US1234567A" / "kg_pack"
    assert manifest["pdf_count"] == 1
    assert manifest["pdf_sha256_mismatches"] == []
    assert (pack / "facts.jsonl").read_bytes() == jsonl_bytes
    assert (run_dir / "data" / "evidence_assets" / "US1234567A" / "pdf" / "original.pdf").read_bytes() == pdf_bytes
    assert (run_dir / "source_package" / "PACKAGE_README.md").read_bytes() == b"source package\n"
    assert (run_dir / "patents" / "US1234567A" / "agent_outputs" / "source_doc_manifest.json").is_file()

    pack_manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    assert pack_manifest["counts"] == {name.removesuffix(".jsonl"): 1 for name in importer.KG_JSONL_FILES}
    status = json.loads((run_dir / "run_status.json").read_text(encoding="utf-8"))
    assert status["counts"] == {
        "imported": 1,
        "complete_packs": 1,
        "pdf_sha256_match": 1,
        "pdf_sha256_mismatch": 0,
    }


def test_import_records_declared_pdf_hash_mismatch_without_rewriting_source(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    _write_archive(archive, declared_pdf_sha256="0" * 64)

    manifest = importer.import_package(archive, tmp_path / "imported")

    assert manifest["pdf_sha256_mismatches"] == [
        {
            "patent_id": "US1234567A",
            "declared_source_pdf_sha256": "0" * 64,
            "archived_pdf_sha256": hashlib.sha256(b"pdf-original-bytes").hexdigest(),
        }
    ]
    pack_manifest = json.loads(
        (tmp_path / "imported" / "patents" / "US1234567A" / "kg_pack" / "manifest.json").read_text(encoding="utf-8")
    )
    assert pack_manifest["provenance_warnings"] == ["source_pdf_sha256_mismatch"]


def test_import_refuses_existing_target_directory(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    _write_archive(archive, declared_pdf_sha256=hashlib.sha256(b"pdf-original-bytes").hexdigest())
    target = tmp_path / "existing"
    target.mkdir()

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        importer.import_package(archive, target)
