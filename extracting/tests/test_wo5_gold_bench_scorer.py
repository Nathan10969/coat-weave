from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_scorer():
    path = Path(__file__).resolve().parents[1] / "audit" / "wo5_gold_bench" / "scripts" / "score_wo5_bench.py"
    spec = importlib.util.spec_from_file_location("score_wo5_bench", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scorer = _load_scorer()


def test_cell_match_accepts_containment_and_partial_overlap() -> None:
    exact = scorer.cell_match("GB/T 4893.1; water:4", "chemical_resistance_water@Water:4; GB/T 4893.1")
    assert exact.status == "exact"
    assert exact.score == 1.0

    partial = scorer.cell_match("ASTM D1475; ASTM D1331; ASTM D2369", "ASTM D1475; ASTM D2369")
    assert partial.status == "partial"
    assert partial.score == 0.5


def test_choose_candidate_prefers_context_then_example() -> None:
    gold = {"patent_id": "WO1", "example_id": "E1", "context_id": "CTX_WO1_E1"}
    rows = [
        {"patent_id": "WO1", "example_id": "E1", "context_id": "CTX_WO1_other"},
        {"patent_id": "WO1", "example_id": "E2", "context_id": "CTX_WO1_E1"},
    ]

    assert scorer.choose_candidate(gold, rows)["context_id"] == "CTX_WO1_E1"


def test_classify_failure_separates_projection_from_extraction() -> None:
    assert scorer.classify_failure("missing", "kg_present") == "CSV projection loss_or_distortion"
    assert scorer.classify_failure("missing", "kg_missing") == "KG extraction miss"
    assert scorer.classify_failure("exact", "kg_missing") == ""
