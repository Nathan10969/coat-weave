from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _load_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "vision_kg"
        / "normalize_legacy_hyperedge_ids.py"
    )
    spec = importlib.util.spec_from_file_location("normalize_legacy_hyperedge_ids", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_module()


def test_normalize_renames_only_the_legacy_identifier_and_keeps_an_audit_backup(tmp_path: Path) -> None:
    pack = tmp_path / "US1234567A" / "kg_pack"
    pack.mkdir(parents=True)
    original = (
        b'{"id":"H-1","context_id":"CTX-1","evidence_ids":["E-1"],"formulation":{"resin":["R"]}}\n'
        b'{"hyperedge_id":"H-2","context_id":"CTX-2","evidence_ids":["E-2"]}\n'
    )
    (pack / "hyperedges.jsonl").write_bytes(original)

    report = migration.normalize_hyperedge_ids(pack)

    rows = [json.loads(line) for line in (pack / "hyperedges.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {
            "hyperedge_id": "H-1",
            "context_id": "CTX-1",
            "evidence_ids": ["E-1"],
            "formulation": {"resin": ["R"]},
        },
        {"hyperedge_id": "H-2", "context_id": "CTX-2", "evidence_ids": ["E-2"]},
    ]
    assert (pack.parent / "agent_outputs" / "hyperedges.before_hyperedge_id_migration.jsonl").read_bytes() == original
    assert report["changed_rows"] == 1
    assert report["original_sha256"] == hashlib.sha256(original).hexdigest()
    assert report["normalized_sha256"] == hashlib.sha256((pack / "hyperedges.jsonl").read_bytes()).hexdigest()


def test_normalize_rejects_conflicting_legacy_and_normalized_identifiers(tmp_path: Path) -> None:
    pack = tmp_path / "US1234567A" / "kg_pack"
    pack.mkdir(parents=True)
    (pack / "hyperedges.jsonl").write_text(
        '{"id":"H-1","hyperedge_id":"H-2"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conflicting id fields"):
        migration.normalize_hyperedge_ids(pack)

    assert not (pack.parent / "agent_outputs" / "hyperedges.before_hyperedge_id_migration.jsonl").exists()
