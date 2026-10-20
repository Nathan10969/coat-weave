from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load_batch_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "vision_kg"
        / "agent_native_batch.py"
    )
    spec = importlib.util.spec_from_file_location("agent_native_batch", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


batch = _load_batch_module()


def _write_jsonl(path: Path, row: dict[str, str]) -> None:
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def _write_pack(root: Path, patent_id: str, *, node_suffix: str = "") -> Path:
    pack = root / patent_id / "kg_pack"
    pack.mkdir(parents=True)
    for name in batch.KG_JSONL_FILES:
        _write_jsonl(
            pack / name,
            {
                "record_type": "TestRecord",
                "node_id": f"{name}_{patent_id}{node_suffix}",
                "patent_id": patent_id,
                "doc_id": patent_id,
            },
        )
    (pack / "manifest.json").write_text(
        json.dumps({"patent_id": patent_id}),
        encoding="utf-8",
    )
    return pack


def test_initialize_run_supports_non_demo_batch_size_and_reference_assets(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "US1234567A.pdf").write_bytes(b"pdf-a")
    (source / "WO2007128739A3.pdf").write_bytes(b"pdf-b")

    run_dir = tmp_path / "run"
    manifest = batch.initialize_run(source, run_dir, asset_mode="reference")

    assert manifest["pdf_count"] == 2
    assert [row["patent_id"] for row in manifest["documents"]] == [
        "US1234567A",
        "WO2007128739A3",
    ]
    assert (run_dir / "input_manifest.json").exists()
    assert (run_dir / "patents" / "US1234567A" / "agent_outputs").is_dir()
    assert not (run_dir / "data" / "evidence_assets" / "US1234567A" / "pdf" / "original.pdf").exists()


def test_render_run_pages_resumes_completed_assets_without_semantic_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "US1234567A.pdf").write_bytes(b"pdf-a")
    run_dir = tmp_path / "run"
    batch.initialize_run(source, run_dir, asset_mode="reference")

    calls: list[tuple[Path, Path, int]] = []

    def fake_render(pdf_path: Path, output_dir: Path, dpi: int) -> int:
        calls.append((pdf_path, output_dir, dpi))
        (output_dir / "page-1.png").write_bytes(b"png")
        batch._write_json(
            output_dir / "render_manifest.json",
            {"dpi": dpi, "files": ["page-1.png"], "page_count": 1},
        )
        return 1

    monkeypatch.setattr(batch, "_render_pdf_pages", fake_render)

    first = batch.render_run_pages(run_dir, dpi=300, patent_ids=["us1234567a"])
    second = batch.render_run_pages(run_dir, dpi=300, patent_ids=["US1234567A"])
    summary = batch.summarize_run(run_dir)

    assert first["rendered"] == 1
    assert second == {"rendered": 0, "skipped": 1, "run_dir": str(run_dir.resolve())}
    assert len(calls) == 1
    assert summary["rendered"] == 1
    assert summary["complete_packs"] == 0
    assert "hyperedges.jsonl" in summary["incomplete_packs"]["US1234567A"]


def test_command_resolution_prefers_native_poppler_executable_for_cmd_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependencies = tmp_path / "dependencies"
    wrapper = dependencies / "bin" / "override" / "pdfinfo.cmd"
    executable = dependencies / "native" / "poppler" / "Library" / "bin" / "pdfinfo.exe"
    wrapper.parent.mkdir(parents=True)
    executable.parent.mkdir(parents=True)
    wrapper.write_text("@echo off\n", encoding="utf-8")
    executable.write_bytes(b"exe")
    monkeypatch.setattr(batch.shutil, "which", lambda _: str(wrapper))

    assert batch._require_command("pdfinfo") == str(executable)


def test_merge_packs_writes_exactly_nine_jsonl_and_counts_records(tmp_path: Path) -> None:
    packs = tmp_path / "patents"
    _write_pack(packs, "US1234567A")
    _write_pack(packs, "WO2007128739A3")

    out_dir = tmp_path / "merged"
    manifest = batch.merge_packs(packs, out_dir)

    assert manifest["patent_count"] == 2
    assert set(manifest["jsonl_files"]) == set(batch.KG_JSONL_FILES)
    assert all(manifest["line_counts"][name] == 2 for name in batch.KG_JSONL_FILES)
    assert {path.name for path in out_dir.glob("*.jsonl")} == set(batch.KG_JSONL_FILES)


def test_merge_packs_rejects_incomplete_or_duplicate_node_ids(tmp_path: Path) -> None:
    packs = tmp_path / "patents"
    incomplete = _write_pack(packs, "US1234567A")
    (incomplete / "edges.jsonl").unlink()

    with pytest.raises(ValueError, match="missing required files"):
        batch.merge_packs(packs, tmp_path / "merged-incomplete")

    for path in (packs / "US1234567A").rglob("*"):
        if path.is_file():
            path.unlink()
    (packs / "US1234567A" / "kg_pack").rmdir()
    (packs / "US1234567A").rmdir()
    _write_pack(packs, "US1234567A")
    _write_pack(packs, "WO2007128739A3")
    duplicate = packs / "WO2007128739A3" / "kg_pack" / "facts.jsonl"
    _write_jsonl(
        duplicate,
        {
            "record_type": "TestRecord",
            "node_id": "facts.jsonl_US1234567A",
            "patent_id": "WO2007128739A3",
            "doc_id": "WO2007128739A3",
        },
    )

    with pytest.raises(ValueError, match="duplicate node_id"):
        batch.merge_packs(packs, tmp_path / "merged-duplicate")


def test_merge_packs_deduplicates_matching_canonical_entity_nodes(tmp_path: Path) -> None:
    packs = tmp_path / "patents"
    first = _write_pack(packs, "US1234567A")
    second = _write_pack(packs, "WO2007128739A3")
    shared_entity = {
        "node_id": "APP_COATING_APPLICATION",
        "canonical_id": "APP_COATING_APPLICATION",
        "entity_type": "application",
        "canonical_name": "Coating application",
    }
    _write_jsonl(first / "canonical_entities.jsonl", shared_entity)
    _write_jsonl(second / "canonical_entities.jsonl", shared_entity)

    manifest = batch.merge_packs(packs, tmp_path / "merged")

    assert manifest["line_counts"]["canonical_entities.jsonl"] == 1
    assert manifest["canonical_entity_deduplications"] == [
        {"node_id": "APP_COATING_APPLICATION", "source_patent_id": "WO2007128739A3"}
    ]
