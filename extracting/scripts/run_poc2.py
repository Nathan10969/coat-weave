"""PoC-2 driver — describe-then-match for one cached MinerU run.

End-to-end on a PDF that's already gone through MinerU + materialise:

  1. Load cached content_list.json from data/mineru_output/<doc_id>/
  2. Re-run unit_extractor (Examples-scoped) to get the units
  3. Build the candidate-paragraph list (text blocks within the Examples
     page range)
  4. For each unit:
       a. describe via vlm_describe (qwen-plus for tables, qwen-vl-plus
          for figures)
       b. match its description against the paragraph list (qwen-plus)
       c. write vlm_description.json + matched_paragraphs.json into the
          unit folder under data/units/<unit_id>/

Usage:
    python scripts/run_poc2.py WO2026077939A1
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
from coating_kg.pipeline import unit_extractor  # noqa: E402
from coating_kg.pipeline.paragraph_extractor import (  # noqa: E402
    examples_page_range,
    gather_examples_paragraphs,
)
from coating_kg.pipeline.paragraph_matcher import ParagraphMatcher, ParagraphMatcherError  # noqa: E402
from coating_kg.pipeline.pdf_layout import _load_layout_json  # noqa: E402
from coating_kg.pipeline.unit_materializer import materialize_unit  # noqa: E402
from coating_kg.pipeline.vlm_describe import QwenVLClient, VLMError  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("poc2")


# Helpers moved to coating_kg.pipeline.paragraph_extractor; thin shims kept
# only for the script's existing internal call sites.
_examples_page_range = examples_page_range
_gather_paragraphs = gather_examples_paragraphs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id", help="doc_id (PDF stem) — must already be in data/mineru_output/")
    ap.add_argument("--top-k", type=int, default=10)
    args = ap.parse_args()

    layout = _load_layout_json(SETTINGS.mineru_output_dir, args.doc_id)
    log.info("loaded layout: doc_id=%s, blocks=%d", layout["doc_id"], len(layout["data"]))

    # 1. units (re-extracted; we already have folders on disk from PoC-1)
    units = list(unit_extractor.iter_figure_table_units(layout, examples_only=True))
    log.info("got %d units in Examples", len(units))
    if not units:
        log.error("no units found — aborting")
        return 1

    # 2. paragraph candidates (page-based filter to dodge stage-2 cursor bug)
    page_range = _examples_page_range(layout)
    if page_range is None:
        log.error("could not detect Examples page range — aborting")
        return 1
    log.info("Examples pages: %d..%d", *page_range)
    paragraphs = _gather_paragraphs(layout, page_range)
    log.info("collected %d candidate paragraphs", len(paragraphs))

    # 3. clients
    vlm = QwenVLClient()
    matcher = ParagraphMatcher()

    # 4. per-unit materialise + describe + match
    units_root = SETTINGS.project_root / "data" / "units"
    for unit in units:
        # PoC-1 inputs: meta.json + caption.txt + image.png/table.html
        folder = materialize_unit(unit, units_root, mineru_root=SETTINGS.mineru_output_dir)
        log.info("---- unit %s (%s p%s) ----", unit.unit_id, unit.unit_type, unit.page)

        # 4a. describe
        t0 = time.time()
        try:
            if unit.unit_type == "figure" and unit.image_path:
                desc = vlm.describe_figure(unit.image_path, candidate_canonical_ids=[])
            else:
                desc = vlm.describe_table(
                    unit.extracted_table_html or "",
                    unit.caption_footnote_text or "",
                    candidate_canonical_ids=[],
                )
        except VLMError as exc:
            log.error("  describe FAILED: %s", exc)
            (folder / "error.log").write_text(f"describe: {exc}\n", encoding="utf-8")
            continue
        log.info("  describe OK (%.1fs); subtype=%s", time.time() - t0, desc.get("subtype"))
        (folder / "vlm_description.json").write_text(
            json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        # 4b. match
        description_text = desc.get("description") or ""
        if not description_text.strip():
            log.warning("  empty description — skipping match")
            continue
        t1 = time.time()
        try:
            matches = matcher.match(
                description_text, paragraphs,
                top_k=args.top_k,
                region_label=unit.region_id,
            )
        except ParagraphMatcherError as exc:
            log.error("  match FAILED: %s", exc)
            (folder / "error.log").write_text(f"match: {exc}\n", encoding="utf-8")
            continue
        log.info(
            "  match OK (%.1fs); %d paragraph(s) returned",
            time.time() - t1, len(matches),
        )
        (folder / "matched_paragraphs.json").write_text(
            json.dumps(
                {"top_k": args.top_k, "matches": matches},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )

    log.info("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
