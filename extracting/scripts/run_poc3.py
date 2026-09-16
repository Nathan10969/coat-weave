"""PoC-3 driver — fact_extractor with vs without matched_paragraphs.

Runs each unit twice (A: no paragraphs, B: with paragraphs) and writes
``facts_no_paragraphs.json`` and ``facts.json`` into the unit folder so
you can diff them. NO DB writes — canonical_resolver is still W2.4 TODO,
so the resulting fact rows have ungrounded application / property IDs.

Usage:
    python scripts/run_poc3.py WO2026077939A1
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from coating_kg.config import SETTINGS  # noqa: E402
from coating_kg.db.models import FigureTableUnit  # noqa: E402
from coating_kg.pipeline.fact_extractor import FactExtractor  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("poc3")


_OPTIONAL_SLOTS = (
    "substrate", "resin_system", "additives", "process",
    "test_method", "test_condition", "result_value", "result_value_text",
    "baseline_value", "baseline_value_text", "comparison_group", "dosage",
)


def _load_unit(folder: Path) -> tuple[FigureTableUnit, list[dict] | None]:
    """Load FigureTableUnit + matched_paragraphs from a unit folder."""
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    # vlm_description.json was written by run_poc2 — fold it back into the unit
    vlm_path = folder / "vlm_description.json"
    if vlm_path.exists():
        vlm = json.loads(vlm_path.read_text(encoding="utf-8"))
        meta["vlm_description"] = vlm.get("description") or json.dumps(vlm, ensure_ascii=False)
        meta["tagged_entities"] = vlm.get("identified_entities") or meta.get("tagged_entities")

    unit = FigureTableUnit.model_validate(meta)

    matched: list[dict] | None = None
    matched_path = folder / "matched_paragraphs.json"
    if matched_path.exists():
        matched = json.loads(matched_path.read_text(encoding="utf-8")).get("matches", [])
    return unit, matched


def _slot_count(facts: list[dict]) -> dict[str, int]:
    counts = {s: 0 for s in _OPTIONAL_SLOTS}
    for f in facts:
        for s in _OPTIONAL_SLOTS:
            v = f.get(s)
            if v is not None and v != "" and v != [] and v != {}:
                counts[s] += 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id")
    args = ap.parse_args()

    units_root = SETTINGS.project_root / "data" / "units"
    folders = sorted(p for p in units_root.iterdir() if p.is_dir() and p.name.startswith(f"U_{args.doc_id}_"))
    if not folders:
        log.error("no unit folders for doc_id=%s under %s", args.doc_id, units_root)
        return 1

    extractor = FactExtractor()
    log.info("model=%s base_url=%s api_key_set=%s",
             extractor.model, extractor.base_url, bool(extractor.api_key))

    for folder in folders:
        log.info("==== unit %s ====", folder.name)
        unit, matched = _load_unit(folder)

        # A: baseline — no paragraphs
        log.info("  A) baseline (no paragraphs)")
        t0 = time.time()
        result_a = extractor.extract(unit, matched_paragraphs=None)
        facts_a = result_a.facts
        log.info("     %d facts (%.1fs)", len(facts_a), time.time() - t0)
        (folder / "facts_no_paragraphs.json").write_text(
            json.dumps(
                {"facts": [f.model_dump(mode="json", exclude_none=True) for f in facts_a]},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

        # B: with matched paragraphs
        log.info("  B) with %d matched paragraphs", len(matched or []))
        t1 = time.time()
        result_b = extractor.extract(unit, matched_paragraphs=matched)
        facts_b = result_b.facts
        log.info("     %d facts (%.1fs)", len(facts_b), time.time() - t1)
        (folder / "facts.json").write_text(
            json.dumps(
                {"facts": [f.model_dump(mode="json", exclude_none=True) for f in facts_b]},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        if result_b.proposed_canonicals:
            (folder / "proposed_canonicals.json").write_text(
                json.dumps(
                    {"proposed_canonicals": result_b.proposed_canonicals},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )

        # Tier-0 Fix 3: persist coverage so stage-9 CSV can label empty
        # cells as "truly absent" vs "extractor truncated".
        if result_b.coverage:
            (folder / "coverage.json").write_text(
                json.dumps(result_b.coverage, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        # Quick A/B diff
        a_dicts = [f.model_dump(mode="json", exclude_none=True) for f in facts_a]
        b_dicts = [f.model_dump(mode="json", exclude_none=True) for f in facts_b]
        a_slots = _slot_count(a_dicts)
        b_slots = _slot_count(b_dicts)
        log.info("  --- slot fill A vs B ---")
        for slot in _OPTIONAL_SLOTS:
            a, b = a_slots[slot], b_slots[slot]
            mark = "↑" if b > a else ("=" if b == a else "↓")
            log.info("     %-20s  A=%2d  B=%2d  %s", slot, a, b, mark)

    log.info("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
