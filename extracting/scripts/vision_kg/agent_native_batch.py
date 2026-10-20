"""Mechanical runner for resumable agent-native vision KG batches.

This module deliberately never interprets patent text, OCR, tables, or material
roles. Codex vision agents own all semantics and write one complete KG pack per
patent. The runner only prepares assets, records progress, validates JSONL, and
concatenates completed packs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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

RUN_SCHEMA_VERSION = "agent_native_vision_batch_v1"
PAGE_COUNT_RE = re.compile(r"^Pages:\s*(\d+)\s*$", re.MULTILINE)


def initialize_run(input_dir: Path, run_dir: Path, *, asset_mode: str = "hardlink") -> dict[str, Any]:
    """Create a run directory without interpreting the PDFs' contents."""

    source_dir = input_dir.resolve()
    target_dir = run_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"input directory does not exist: {source_dir}")
    if target_dir.exists():
        raise FileExistsError(f"refusing to overwrite run directory: {target_dir}")
    if asset_mode not in {"hardlink", "copy", "reference"}:
        raise ValueError(f"unsupported asset mode: {asset_mode}")

    documents = _discover_documents(source_dir)
    target_dir.mkdir(parents=True)
    (target_dir / "patents").mkdir()
    (target_dir / "data" / "evidence_assets").mkdir(parents=True)

    for document in documents:
        _prepare_document_workspace(target_dir, document, asset_mode)

    manifest = {
        "schema_version": RUN_SCHEMA_VERSION,
        "created_at_utc": _utc_now(),
        "input_dir": str(source_dir),
        "asset_mode": asset_mode,
        "pdf_count": len(documents),
        "documents": documents,
        "kg_jsonl_files": list(KG_JSONL_FILES),
        "semantic_boundary": "Codex agents write semantic KG records; this runner only manages assets and validation.",
    }
    _write_json(target_dir / "input_manifest.json", manifest)
    _write_json(
        target_dir / "run_status.json",
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "created_at_utc": _utc_now(),
            "counts": {"prepared": len(documents), "rendered": 0, "complete_packs": 0},
            "documents": {document["patent_id"]: "prepared" for document in documents},
        },
    )
    _write_agent_instruction(target_dir)
    return manifest


def render_run_pages(run_dir: Path, *, dpi: int = 300, patent_ids: Iterable[str] | None = None) -> dict[str, Any]:
    """Render page assets with Poppler, resuming only incomplete document folders."""

    if dpi <= 0:
        raise ValueError("dpi must be positive")
    root = run_dir.resolve()
    manifest = _load_json(root / "input_manifest.json")
    selected = {item.upper() for item in patent_ids} if patent_ids else None
    rendered = skipped = 0
    for document in manifest["documents"]:
        patent_id = document["patent_id"]
        if selected is not None and patent_id not in selected:
            continue
        asset_dir = root / "data" / "evidence_assets" / patent_id / "pages"
        render_manifest = asset_dir / "render_manifest.json"
        if _render_is_complete(render_manifest, dpi):
            skipped += 1
            continue
        page_count = _render_pdf_pages(Path(document["source_pdf"]), asset_dir, dpi)
        document["page_count"] = page_count
        document["page_asset_dir"] = str(asset_dir.relative_to(root).as_posix())
        rendered += 1

    _write_json(root / "input_manifest.json", manifest)
    status = _load_json(root / "run_status.json")
    for document in manifest["documents"]:
        patent_id = document["patent_id"]
        if selected is None or patent_id in selected:
            status["documents"][patent_id] = "rendered"
    status["counts"]["rendered"] = sum(
        1 for document in manifest["documents"] if (root / "data" / "evidence_assets" / document["patent_id"] / "pages" / "render_manifest.json").exists()
    )
    status["updated_at_utc"] = _utc_now()
    _write_json(root / "run_status.json", status)
    return {"rendered": rendered, "skipped": skipped, "run_dir": str(root)}


def merge_packs(patents_dir: Path, out_dir: Path) -> dict[str, Any]:
    """Concatenate complete agent-authored packs without creating semantic records."""

    source_dir = patents_dir.resolve()
    target_dir = out_dir.resolve()
    if not source_dir.is_dir():
        raise ValueError(f"patents directory does not exist: {source_dir}")
    if target_dir.exists():
        raise FileExistsError(f"refusing to overwrite merged KG directory: {target_dir}")

    pack_dirs = sorted(path for path in source_dir.glob("*/kg_pack") if path.is_dir())
    if not pack_dirs:
        raise ValueError(f"no kg_pack directories found under {source_dir}")

    rows_by_file: dict[str, list[str]] = {name: [] for name in KG_JSONL_FILES}
    node_ids: set[str] = set()
    node_id_files: dict[str, str] = {}
    canonical_entity_identity: dict[str, tuple[str, str, str]] = {}
    canonical_entity_deduplications: list[dict[str, str]] = []
    source_packs: list[dict[str, str]] = []
    for pack_dir in pack_dirs:
        patent_id = pack_dir.parent.name
        _validate_pack_files(pack_dir)
        source_packs.append({"patent_id": patent_id, "path": str(pack_dir)})
        for file_name in KG_JSONL_FILES:
            for row in _read_jsonl(pack_dir / file_name):
                node_id = row.get("node_id")
                if node_id:
                    if node_id in node_ids:
                        if (
                            file_name == "canonical_entities.jsonl"
                            and node_id_files.get(node_id) == "canonical_entities.jsonl"
                            and canonical_entity_identity.get(node_id)
                            == (
                                str(row.get("canonical_id") or ""),
                                str(row.get("entity_type") or ""),
                                str(row.get("canonical_name") or "").casefold(),
                            )
                        ):
                            canonical_entity_deduplications.append(
                                {"node_id": str(node_id), "source_patent_id": patent_id}
                            )
                            continue
                        raise ValueError(f"duplicate node_id across packs: {node_id}")
                    node_ids.add(node_id)
                    node_id_files[str(node_id)] = file_name
                    if file_name == "canonical_entities.jsonl":
                        canonical_entity_identity[str(node_id)] = (
                            str(row.get("canonical_id") or ""),
                            str(row.get("entity_type") or ""),
                            str(row.get("canonical_name") or "").casefold(),
                        )
                rows_by_file[file_name].append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))

    target_dir.mkdir(parents=True)
    line_counts: dict[str, int] = {}
    for file_name, rows in rows_by_file.items():
        (target_dir / file_name).write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
        line_counts[file_name] = len(rows)
    manifest = {
        "schema_version": "agent_native_vision_merged_pack_v1",
        "created_at_utc": _utc_now(),
        "patent_count": len(pack_dirs),
        "jsonl_files": list(KG_JSONL_FILES),
        "line_counts": line_counts,
        "source_packs": source_packs,
        "canonical_entity_deduplications": canonical_entity_deduplications,
        "semantic_boundary": "Merged rows are agent-authored; no semantic inference was performed during merge.",
    }
    _write_json(target_dir / "manifest.json", manifest)
    return manifest


def summarize_run(run_dir: Path) -> dict[str, Any]:
    """Return a state-only summary of prepared, rendered, and complete packs."""

    root = run_dir.resolve()
    manifest = _load_json(root / "input_manifest.json")
    documents = manifest["documents"]
    rendered = 0
    complete_packs = 0
    incomplete: dict[str, list[str]] = {}
    for document in documents:
        patent_id = document["patent_id"]
        page_manifest = root / "data" / "evidence_assets" / patent_id / "pages" / "render_manifest.json"
        rendered += int(page_manifest.exists())
        pack_dir = root / "patents" / patent_id / "kg_pack"
        missing = _missing_pack_files(pack_dir)
        if missing:
            incomplete[patent_id] = missing
        else:
            complete_packs += 1
    return {
        "run_dir": str(root),
        "pdf_count": len(documents),
        "rendered": rendered,
        "complete_packs": complete_packs,
        "incomplete_packs": incomplete,
    }


def _discover_documents(source_dir: Path) -> list[dict[str, str | int | None]]:
    documents: list[dict[str, str | int | None]] = []
    seen_ids: set[str] = set()
    for path in sorted(source_dir.glob("*.pdf"), key=lambda item: item.name.casefold()):
        patent_id = path.stem.strip().upper()
        if not patent_id:
            raise ValueError(f"empty patent id from file name: {path.name}")
        if patent_id in seen_ids:
            raise ValueError(f"duplicate patent id after normalization: {patent_id}")
        seen_ids.add(patent_id)
        documents.append(
            {
                "patent_id": patent_id,
                "source_pdf": str(path.resolve()),
                "source_pdf_name": path.name,
                "page_count": None,
            }
        )
    if not documents:
        raise ValueError(f"no PDFs found in {source_dir}")
    return documents


def _prepare_document_workspace(run_dir: Path, document: dict[str, str | int | None], asset_mode: str) -> None:
    patent_id = str(document["patent_id"])
    (run_dir / "patents" / patent_id / "agent_outputs").mkdir(parents=True)
    (run_dir / "patents" / patent_id / "kg_pack").mkdir(parents=True)
    asset_root = run_dir / "data" / "evidence_assets" / patent_id
    for name in ("pdf", "pages", "figures", "tables", "crops", "thumbnails"):
        (asset_root / name).mkdir(parents=True)
    if asset_mode == "reference":
        return
    source = Path(str(document["source_pdf"]))
    target = asset_root / "pdf" / "original.pdf"
    if asset_mode == "hardlink":
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    shutil.copy2(source, target)


def _render_pdf_pages(pdf_path: Path, output_dir: Path, dpi: int) -> int:
    pdfinfo = _require_command("pdfinfo")
    pdftoppm = _require_command("pdftoppm")
    page_count = _page_count(pdfinfo, pdf_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in output_dir.glob("page-*.png"):
        path.unlink()
    prefix = output_dir / "page"
    subprocess.run(
        [pdftoppm, "-r", str(dpi), "-png", str(pdf_path), str(prefix)],
        check=True,
        capture_output=True,
        text=True,
    )
    rendered_files = sorted(output_dir.glob("page-*.png"))
    if len(rendered_files) != page_count:
        raise RuntimeError(f"expected {page_count} rendered pages for {pdf_path.name}, found {len(rendered_files)}")
    _write_json(
        output_dir / "render_manifest.json",
        {
            "schema_version": "pdf_page_raster_manifest_v1",
            "source_pdf": str(pdf_path.resolve()),
            "page_count": page_count,
            "dpi": dpi,
            "files": [path.name for path in rendered_files],
        },
    )
    return page_count


def _page_count(pdfinfo: str, pdf_path: Path) -> int:
    result = subprocess.run([pdfinfo, str(pdf_path)], check=True, capture_output=True, text=True)
    match = PAGE_COUNT_RE.search(result.stdout)
    if not match:
        raise RuntimeError(f"pdfinfo did not report page count for {pdf_path}")
    return int(match.group(1))


def _render_is_complete(render_manifest: Path, dpi: int) -> bool:
    if not render_manifest.exists():
        return False
    try:
        data = _load_json(render_manifest)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    files = data.get("files")
    return data.get("dpi") == dpi and isinstance(files, list) and all(
        (render_manifest.parent / file_name).exists() for file_name in files
    )


def _validate_pack_files(pack_dir: Path) -> None:
    missing = _missing_pack_files(pack_dir)
    if missing:
        raise ValueError(f"pack {pack_dir} missing required files: {', '.join(missing)}")
    _load_json(pack_dir / "manifest.json")


def _missing_pack_files(pack_dir: Path) -> list[str]:
    required = [*KG_JSONL_FILES, "manifest.json"]
    return [file_name for file_name in required if not (pack_dir / file_name).is_file()]


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL {path}:{line_no}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"JSONL row is not an object: {path}:{line_no}")
        rows.append(row)
    return rows


def _require_command(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required command is not available on PATH: {name}")
    wrapper = Path(path)
    if wrapper.suffix.lower() not in {".cmd", ".bat"}:
        return path
    for parent in wrapper.parents:
        native_executable = parent / "native" / "poppler" / "Library" / "bin" / f"{name}.exe"
        if native_executable.is_file():
            return str(native_executable)
    return path


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON object required: {path}")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_agent_instruction(run_dir: Path) -> None:
    (run_dir / "AGENT_TASK.md").write_text(
        "# Agent-native Vision KG Task\n\n"
        "Codex agents must read rendered PDF page images and table crops, then write all "
        "nine KG JSONL files plus manifest.json in each patents/<patent_id>/kg_pack directory. "
        "Follow coating_kg/docs/AGENT_NATIVE_VISION_KG_RECONSTRUCTION.md. Scripts in this "
        "run may render, validate, and merge only; they must not infer semantic fields.\n",
        encoding="utf-8",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run mechanical stages for agent-native vision KG batches.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a new non-semantic batch run")
    init_parser.add_argument("--input-dir", type=Path, required=True)
    init_parser.add_argument("--run-dir", type=Path, required=True)
    init_parser.add_argument("--asset-mode", choices=("hardlink", "copy", "reference"), default="hardlink")

    render_parser = subparsers.add_parser("render", help="render PDF pages with Poppler")
    render_parser.add_argument("--run-dir", type=Path, required=True)
    render_parser.add_argument("--dpi", type=int, default=300)
    render_parser.add_argument("--patent-id", action="append", default=[])

    merge_parser = subparsers.add_parser("merge", help="concatenate complete agent-authored KG packs")
    merge_parser.add_argument("--patents-dir", type=Path, required=True)
    merge_parser.add_argument("--out-dir", type=Path, required=True)

    summary_parser = subparsers.add_parser("summary", help="report run completion state")
    summary_parser.add_argument("--run-dir", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "init":
        result = initialize_run(args.input_dir, args.run_dir, asset_mode=args.asset_mode)
    elif args.command == "render":
        result = render_run_pages(args.run_dir, dpi=args.dpi, patent_ids=args.patent_id)
    elif args.command == "merge":
        result = merge_packs(args.patents_dir, args.out_dir)
    else:
        result = summarize_run(args.run_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
