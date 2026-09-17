from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "vision_kg" / "repair_missing_hyperedge_doc_ids.py"
SPEC = importlib.util.spec_from_file_location("repair_missing_hyperedge_doc_ids", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _pack(tmp_path: Path) -> Path:
    kg_dir = tmp_path / "merged_kg_pack"
    kg_dir.mkdir()
    _write_jsonl(kg_dir / "evidence_units.jsonl", [{"evidence_id": "ev-1", "doc_id": "US8829076B2"}])
    _write_jsonl(kg_dir / "example_contexts.jsonl", [{"context_id": "ctx-1", "doc_id": "US8829076B2"}])
    _write_jsonl(
        kg_dir / "hyperedges.jsonl",
        [
            {"hyperedge_id": "he-1", "context_id": "ctx-1", "evidence_ids": ["ev-1"]},
            {"hyperedge_id": "he-2", "doc_id": "US-already", "evidence_ids": ["ev-1"]},
        ],
    )
    (kg_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
    return kg_dir


def test_repairs_only_missing_doc_id_with_unique_existing_references(tmp_path: Path) -> None:
    kg_dir = _pack(tmp_path)

    report = MODULE.repair_missing_hyperedge_doc_ids(kg_dir, expected_doc_id="US8829076B2")

    rows = [json.loads(line) for line in (kg_dir / "hyperedges.jsonl").read_text(encoding="utf-8").splitlines()]
    assert report["changed_rows"] == 1
    assert rows[0]["doc_id"] == "US8829076B2"
    assert rows[1]["doc_id"] == "US-already"
    assert (kg_dir.parent / "agent_outputs" / "hyperedges.before_doc_id_repair.jsonl").is_file()
    manifest = json.loads((kg_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_sha256"]["hyperedges.jsonl"] == report["repaired_hyperedges_sha256"]


def test_rejects_ambiguous_document_references(tmp_path: Path) -> None:
    kg_dir = _pack(tmp_path)
    _write_jsonl(
        kg_dir / "evidence_units.jsonl",
        [
            {"evidence_id": "ev-1", "doc_id": "US8829076B2"},
            {"evidence_id": "ev-2", "doc_id": "US-other"},
        ],
    )
    _write_jsonl(
        kg_dir / "hyperedges.jsonl",
        [{"hyperedge_id": "he-1", "evidence_ids": ["ev-1", "ev-2"]}],
    )

    with pytest.raises(ValueError, match="cannot uniquely resolve document"):
        MODULE.repair_missing_hyperedge_doc_ids(kg_dir)
