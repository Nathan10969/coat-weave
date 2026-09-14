"""Command line entry point for the local coating KG pipeline."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import SETTINGS
from .pipeline import pdf_layout
from .pipeline.patent_metadata_extractor import (
    extract_patent_metadata,
    find_content_list,
    save_patent_metadata,
)
from .pipeline.unit_extractor import iter_figure_table_units


logger = logging.getLogger("coating_kg.cli")


@dataclass
class StageResult:
    stage: str
    status: str
    artifact: str | None = None
    metrics: dict[str, Any] | None = None
    note: str | None = None


def infer_doc_id(pdf_path: Path) -> str:
    parent = pdf_path.parent.name
    if parent.startswith("WO"):
        return parent
    stem = pdf_path.stem
    if stem.endswith("_origin"):
        return stem.removesuffix("_origin")
    return stem


def _copy_cached_mineru_doc(doc_id: str, cache_root: Path, local_root: Path) -> Path | None:
    src = cache_root / doc_id
    if not src.exists():
        return None
    dst = local_root / doc_id
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() == dst.resolve():
        return dst
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def _load_layout_from_content_list(content_list_path: Path, doc_id: str) -> dict[str, Any]:
    data = json.loads(content_list_path.read_text(encoding="utf-8"))
    return {"doc_id": doc_id, "source": str(content_list_path), "data": data}


def _stage2_units_path(doc_id: str) -> Path:
    out_dir = SETTINGS.paths.data_dir / "stage2"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{doc_id}__units.json"


def _manifest_path(doc_id: str) -> Path:
    out_dir = SETTINGS.paths.data_dir / "runs" / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "run-manifest.json"


def run_ingest(
    pdf: Path,
    *,
    mineru_cache: Path | None = None,
    use_online_mineru: bool = False,
    use_agent_mineru: bool = False,
    agent_page_range: str | None = None,
    examples_only: bool = True,
) -> list[StageResult]:
    pdf = pdf.resolve()
    if not pdf.exists():
        raise FileNotFoundError(pdf)

    doc_id = infer_doc_id(pdf)
    repo_root = SETTINGS.paths.repo_root
    local_mineru_root = SETTINGS.paths.data_dir / "mineru_output"
    external_cache = mineru_cache or SETTINGS.paths.mineru_output_dir

    results: list[StageResult] = [
        StageResult(
            stage="0",
            status="pass",
            artifact=str(pdf),
            metrics={"doc_id": doc_id},
            note="PDF accepted",
        )
    ]

    content_list = None
    if use_online_mineru and use_agent_mineru:
        raise ValueError("--online-mineru and --agent-mineru are mutually exclusive")

    if use_online_mineru:
        if not SETTINGS.mineru.token:
            raise RuntimeError(
                "Online MinerU submission requires MINERU_TOKEN (Bearer API Token). "
                "Access Key ID / Secret Access Key are not sufficient for /api/v4/file-urls/batch."
            )
        online_doc_dir = local_mineru_root / doc_id
        if online_doc_dir.exists():
            shutil.rmtree(online_doc_dir)
        layout = pdf_layout.parse_pdf_as_doc_id(pdf, local_mineru_root, doc_id)
        content_list = find_content_list(local_mineru_root, doc_id)
    elif use_agent_mineru:
        online_doc_dir = local_mineru_root / doc_id
        if online_doc_dir.exists():
            shutil.rmtree(online_doc_dir)
        layout = pdf_layout.parse_pdf_agent_markdown(pdf, local_mineru_root, doc_id, page_range=agent_page_range)
        content_list = find_content_list(local_mineru_root, doc_id)
    else:
        _copy_cached_mineru_doc(doc_id, external_cache, local_mineru_root)
        content_list = find_content_list(local_mineru_root, doc_id)
        if content_list is not None:
            layout = _load_layout_from_content_list(content_list, doc_id)
        else:
            raise RuntimeError(
                f"No cached MinerU content_list found for {doc_id}; "
                "pass --online-mineru to submit the PDF."
            )

    if content_list is None and layout.get("source"):
        content_list = Path(layout["source"])

    blocks = layout.get("data") or []
    if isinstance(blocks, list):
        metrics = {
            "blocks": len(blocks),
            "text_blocks": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "text"),
            "table_blocks": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "table"),
            "image_blocks": sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "image"),
        }
    else:
        metrics = {"blocks": None, "text_blocks": None, "table_blocks": None, "image_blocks": None}
    results.append(StageResult(
        stage="1",
        status="pass",
        artifact=str(content_list) if content_list else layout.get("source"),
        metrics=metrics,
        note="MinerU layout loaded",
    ))

    if content_list is None:
        raise RuntimeError("Stage 1.5 needs a MinerU content_list JSON.")
    meta = extract_patent_metadata(content_list, doc_id)
    meta_path = save_patent_metadata(repo_root, doc_id, meta)
    results.append(StageResult(
        stage="1.5",
        status="pass" if meta.get("is_coating_patent") else "review",
        artifact=str(meta_path),
        metrics={
            "is_coating_patent": meta.get("is_coating_patent"),
            "is_coating_reason": meta.get("is_coating_reason"),
            "ipc_codes": meta.get("ipc_codes"),
            "title": meta.get("title"),
        },
        note="Patent metadata extracted and coating gate evaluated",
    ))

    units = list(iter_figure_table_units(layout, examples_only=examples_only, image_root=content_list.parent))
    units_path = _stage2_units_path(doc_id)
    units_path.write_text(
        json.dumps([u.model_dump(mode="json", exclude_none=True) for u in units], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    results.append(StageResult(
        stage="2",
        status="pass",
        artifact=str(units_path),
        metrics={
            "units": len(units),
            "figures": sum(1 for u in units if u.unit_type == "figure"),
            "tables": sum(1 for u in units if u.unit_type == "table"),
            "examples_only": examples_only,
        },
        note="Figure/table units extracted",
    ))

    manifest_path = _manifest_path(doc_id)
    manifest_path.write_text(
        json.dumps({"doc_id": doc_id, "pdf": str(pdf), "stages": [asdict(r) for r in results]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    results.append(StageResult(stage="manifest", status="pass", artifact=str(manifest_path), note="Run manifest written"))
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coating_kg")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="Run Stage 0-2 for one PDF")
    ingest.add_argument("pdf", type=Path)
    ingest.add_argument("--mineru-cache", type=Path, default=None)
    ingest.add_argument("--online-mineru", action="store_true", help="Submit to MinerU if cache is missing")
    ingest.add_argument("--agent-mineru", action="store_true", help="Use MinerU no-token Agent API and convert Markdown to a simple layout")
    ingest.add_argument("--agent-page-range", default=None, help="Page range for MinerU Agent API, e.g. 1-20")
    ingest.add_argument("--whole-doc", action="store_true", help="Do not restrict Stage 2 to Examples")
    ingest.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(levelname)s:%(name)s:%(message)s")
    if args.command == "ingest":
        results = run_ingest(
            args.pdf,
            mineru_cache=args.mineru_cache,
            use_online_mineru=args.online_mineru,
            use_agent_mineru=args.agent_mineru,
            agent_page_range=args.agent_page_range,
            examples_only=not args.whole_doc,
        )
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
