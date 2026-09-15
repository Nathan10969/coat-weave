from __future__ import annotations

import json
import logging
import shutil
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import click

from .config import SETTINGS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("coating_kg")


_TABLE_IMAGE_NAMES = (
    "table.png",
    "table.jpg",
    "table.jpeg",
    "table.webp",
)


def _find_table_image(folder: Path) -> Path | None:
    for name in _TABLE_IMAGE_NAMES:
        candidate = folder / name
        if candidate.exists():
            return candidate
    return None


def _extract_corrected_table_text(desc: dict[str, Any] | None) -> tuple[str | None, str, str | None]:
    """Return corrected table text, source label, and artifact filename."""

    if not desc:
        return None, "mineru_raw_html", None

    for key in ("corrected_table_html", "table_corrected_html", "corrected_html"):
        value = desc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), "qwen_table_ocr_html", "table_corrected.html"

    for key in ("table_markdown_exact", "corrected_table_markdown", "table_markdown"):
        value = desc.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), "qwen_table_ocr_markdown", "table_corrected.md"

    return None, "mineru_raw_html", None


def _relative_artifact(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base)).replace("\\", "/")
    except ValueError:
        return str(path)


def _write_table_ocr_artifacts(
    folder: Path,
    desc: dict[str, Any],
    *,
    table_image: Path | None,
    corrected_text: str | None,
    corrected_filename: str | None,
    effective_source: str,
) -> str | None:
    corrected_path: Path | None = None
    if corrected_text and corrected_filename:
        corrected_path = folder / corrected_filename
        corrected_path.write_text(corrected_text, encoding="utf-8")

    metadata_keys = (
        "table_type",
        "table_subject",
        "orientation",
        "sample_headers",
        "row_headers",
        "key_columns",
        "key_units",
        "units",
        "footnotes",
        "merged_cell_notes",
        "blank_cell_policy",
        "html_conflict_notes",
        "ocr_confidence",
        "correction_confidence",
        "correction_notes",
        "critical_uncertainties",
        "needs_human_review",
    )
    table_ocr = {
        "effective_table_source": effective_source,
        "used_for_fact_extraction": effective_source != "mineru_raw_html",
        "table_image_path": (
            _relative_artifact(table_image, SETTINGS.project_root) if table_image else None
        ),
        "raw_table_html_path": (
            _relative_artifact(folder / "table.html", SETTINGS.project_root)
            if (folder / "table.html").exists() else None
        ),
        "corrected_table_path": (
            _relative_artifact(corrected_path, SETTINGS.project_root)
            if corrected_path else None
        ),
        "metadata": {key: desc.get(key) for key in metadata_keys if key in desc},
    }
    (folder / "table_ocr.json").write_text(
        json.dumps(table_ocr, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return corrected_path.read_text(encoding="utf-8") if corrected_path else None


def _write_stage2_observability(
    *,
    pdf_path: Path,
    doc_id: str,
    units: list[Any],
    meta: dict[str, Any] | None,
    doc_profile: dict[str, Any] | None,
    examples_span: Any,
    page_range: tuple[int, int] | None,
) -> tuple[Path, Path]:
    stage2_dir = SETTINGS.project_root / "data" / "stage2"
    run_dir = SETTINGS.project_root / "data" / "runs" / doc_id
    stage2_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    units_path = stage2_dir / f"{doc_id}__units.json"
    units_path.write_text(
        json.dumps(
            [unit.model_dump(mode="json", exclude_none=True) for unit in units],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    manifest = {
        "doc_id": doc_id,
        "pdf": str(pdf_path),
        "stage2_units_path": _relative_artifact(units_path, SETTINGS.project_root),
        "counts": {
            "units": len(units),
            "figures": sum(1 for unit in units if unit.unit_type == "figure"),
            "tables": sum(1 for unit in units if unit.unit_type == "table"),
            "equations_as_figures": sum(
                1 for unit in units
                if unit.unit_type == "figure"
                and (
                    getattr(unit.figure_subtype, "value", unit.figure_subtype) == "structure"
                )
            ),
        },
        "examples": {
            "split_mode": getattr(examples_span, "mode", None),
            "split_confidence": getattr(examples_span, "confidence", None),
            "start_block": getattr(examples_span, "start_block", None),
            "end_block": getattr(examples_span, "end_block", None),
            "page_range": list(page_range) if page_range else None,
        },
        "metadata": {
            "path": f"data/patents/{doc_id}__patent_meta.json" if meta else None,
            "coating_gate_status": (meta or {}).get("coating_gate_status"),
            "is_coating_reason": (meta or {}).get("is_coating_reason"),
        },
        "doc_profile": {
            "path": f"data/patents/{doc_id}__coating_profile.json" if doc_profile else None,
            "coating_relevance": (doc_profile or {}).get("coating_relevance"),
            "application_family": (doc_profile or {}).get("application_family"),
        },
    }
    manifest_path = run_dir / "run-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return units_path, manifest_path


@click.group()
def cli() -> None:
    """coating_kg V1.2.2 ingest CLI。"""


# ---------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------
@cli.command("ingest")
@click.argument("pdf_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", is_flag=True, help="切完 unit 即停，不调 LLM，不写 DB。")
@click.option("--skip-db", is_flag=True, help="走完 LLM 流程但跳过 DB 写入（PoC 模式）。")
def ingest_cmd(pdf_path: Path, dry_run: bool, skip_db: bool) -> None:
    """单 PDF 端到端 ingest。

    1. MinerU 解析版面
    2. 切 Examples 章节 + 抽 figure/table units
    3. unit 物化到 data/units/<unit_id>/
    4. VLM/LLM 描述（figure/table image→VL, table text fallback→text LLM）
    5. 实体补打（DB 节点 ∪ VLM identified_entities）
    6. Examples 段落匹配每个 unit
    7. fact 抽取（带 matched_paragraphs 作上下文）
    8. polarity + comparison_group 后处理
    9. 写 DB（--skip-db 时跳过）

    每 unit 目录产出: meta.json, caption.txt, image.png|table.html,
    vlm_description.json, matched_paragraphs.json, facts.json。
    """
    from .pipeline import pdf_layout, unit_extractor, vlm_describe, entity_tagger
    from .pipeline.fact_extractor import FactExtractor
    from .pipeline.polarity import classify_polarity
    from .pipeline.comparison_group import resolve_comparison_group
    from .pipeline.unit_materializer import materialize_unit
    from .pipeline.paragraph_extractor import (
        examples_page_range, gather_examples_paragraphs,
    )
    from .pipeline.paragraph_matcher import ParagraphMatcher
    from .pipeline.context_inheritance import apply_context_inheritance
    from .pipeline.doc_profile import extract_doc_profile, save_doc_profile
    from .pipeline.structure_resolver import resolve_structures_for_doc
    from .pipeline import unit_router  # ★ Stage 4.5 — three-way routing

    logger.info("ingest start: %s (dry_run=%s, skip_db=%s)", pdf_path, dry_run, skip_db)

    # 1. 版面 — MinerU 在线 API（缺 token 或无结果时 raise MinerUError）
    layout = pdf_layout.parse_pdf(pdf_path, SETTINGS.mineru_output_dir)
    doc_id = layout["doc_id"]
    # Re-ingesting a PDF should replace its stage 4.5 pending Layer 1 figures,
    # not append another copy for the same doc, even if the metadata gate exits.
    unit_router.clear_pending_figures(SETTINGS.project_root, doc_id)
    units_root = SETTINGS.project_root / "data" / "units"
    _clear_existing_doc_units(units_root, doc_id)

    # ★ V1.2.5 task #4b + C：早期 IPC 闸门 + section_split_mode 落 patent_meta。
    # MinerU 后立刻抽 metadata 判 is_coating_patent，非涂料早跳过省 LLM 钱
    # （~¥0.5/篇 × 30% imposter ≈ ¥45/348 篇）。
    # section_split mode (found/fallback) 也写进 patent_meta 供批跑可观测。
    from .pipeline import patent_metadata_extractor as _pme
    from .pipeline.section_split import locate_examples_blocks as _locate_examples_blocks
    from .pipeline.paragraph_extractor import examples_page_range as _examples_page_range

    meta: dict[str, Any] | None = None
    doc_profile_data: dict[str, Any] | None = None
    examples_span: Any = None
    ex_range: tuple[int, int] | None = None
    cl_path = _pme.find_content_list(SETTINGS.mineru_output_dir, doc_id)
    if cl_path is not None:
        try:
            meta = _pme.extract_patent_metadata(cl_path, doc_id)

            # V1.2.5 task C: 判断 section_split mode
            data = layout.get("data") or []
            blocks_for_split = (
                data if isinstance(data, list)
                else (data.get("para_blocks", []) if isinstance(data, dict) else [])
            )
            examples_span = _locate_examples_blocks(blocks_for_split)
            meta["section_split_mode"] = getattr(examples_span, "mode", None) or "fallback"
            meta["section_split_confidence"] = getattr(examples_span, "confidence", None)
            ex_range = _examples_page_range(layout)
            meta["examples_page_range"] = list(ex_range) if ex_range else None

            _pme.save_patent_metadata(SETTINGS.project_root, doc_id, meta)
            doc_profile_data = extract_doc_profile(layout, doc_id, meta)
            save_doc_profile(SETTINGS.project_root, doc_id, doc_profile_data)
            if not meta.get("is_coating_patent", True):
                logger.warning(
                    "EARLY GATE: skipping non-coating patent %s. "
                    "reason=%s, title=%r, ipc_codes=%s",
                    doc_id,
                    meta.get("is_coating_reason"),
                    (meta.get("title") or "")[:80],
                    meta.get("ipc_codes"),
                )
                return  # 早退，避开 Stage 3/6 的 LLM 调用
        except Exception as exc:
            logger.warning(
                "metadata extraction failed for %s (%s); continuing without IPC gate",
                doc_id, exc,
            )

    # 2. Examples 内的 units
    mineru_doc_dir = (
        Path(layout["source"]).parent
        if layout.get("source")
        else SETTINGS.mineru_output_dir / doc_id
    )
    units = list(
        unit_extractor.iter_figure_table_units(
            layout,
            examples_only=True,
            image_root=mineru_doc_dir,
        )
    )
    logger.info("found %d figure/table units in Examples section", len(units))
    _write_stage2_observability(
        pdf_path=pdf_path,
        doc_id=doc_id,
        units=units,
        meta=meta,
        doc_profile=doc_profile_data,
        examples_span=examples_span,
        page_range=ex_range,
    )

    # 3. 每 unit 物化到 data/units/<unit_id>/
    for unit in units:
        materialize_unit(unit, units_root, mineru_root=SETTINGS.mineru_output_dir)
    logger.info("materialised %d unit folders under %s", len(units), units_root)

    if dry_run:
        logger.info("dry-run: stopping after stage 3 (no LLM, no DB).")
        return

    # PoC-2 候选段落 — 每篇 doc 算一次
    page_range = ex_range or examples_page_range(layout)
    paragraphs = gather_examples_paragraphs(layout, page_range) if page_range else []
    logger.info("Examples page range = %s; %d candidate paragraphs", page_range, len(paragraphs))

    vlm_client = vlm_describe.QwenVLClient()
    matcher = ParagraphMatcher()
    extractor = FactExtractor()
    # doc_id 上面已设（V1.2.5 早期闸门用过）

    # ★ Stage 4.5 — open the audit log (single file across the whole batch)
    audit_log = unit_router.RouteAuditLog(
        SETTINGS.project_root / "output" / "unit_routing_audit.csv",
    )

    def run_paragraph_match(unit: Any, folder: Path) -> list[dict[str, Any]]:
        matched: list[dict[str, Any]] = []
        if paragraphs and unit.vlm_description:
            try:
                matched = matcher.match(
                    unit.vlm_description, paragraphs,
                    top_k=10, region_label=unit.region_id,
                )
                (folder / "matched_paragraphs.json").write_text(
                    json.dumps({"top_k": 10, "matches": matched},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("matcher failed for %s: %s", unit.unit_id, exc)
        return matched

    n_units_ok = n_units_failed = 0
    n_units_layer1 = n_units_deleted = 0
    n_facts_total = 0

    # DB 连接可选；--skip-db 时用 nullcontext(None) 让 with 块统一。
    if skip_db:
        conn_cm: Any = nullcontext(None)
    else:
        from .db.connection import get_conn
        conn_cm = get_conn()

    with conn_cm as conn:
        if conn is not None:
            from .db.insert import (
                insert_patent, insert_figure_table_unit, bulk_insert_facts,
            )
            from .db.models import Patent
            from .db.query import list_canonical_ids

            candidate_ids = list_canonical_ids(conn)
            insert_patent(conn, Patent(doc_id=doc_id))
            conn.commit()
        else:
            candidate_ids: list[str] = []

        for unit in units:
            folder = units_root / unit.unit_id
            effective_table_html = unit.extracted_table_html or ""
            table_html_source = "mineru_raw_html"

            # 4. VLM 描述 — 失败不致命
            desc: dict | None = None
            try:
                if unit.unit_type == "figure" and unit.image_path:
                    desc = vlm_client.describe_figure(unit.image_path, candidate_ids)
                elif unit.unit_type == "table":
                    table_image = _find_table_image(folder)
                    if table_image is None and unit.image_path:
                        source_image = Path(unit.image_path)
                        if source_image.exists():
                            table_image = source_image
                    if table_image is not None:
                        desc = vlm_client.describe_table_image(
                            table_image,
                            unit.extracted_table_html or "",
                            unit.caption_footnote_text or "",
                            candidate_ids,
                        )
                    else:
                        desc = vlm_client.describe_table(
                            unit.extracted_table_html or "",
                            unit.caption_footnote_text or "",
                            candidate_ids,
                        )

                    corrected_text, detected_source, corrected_filename = _extract_corrected_table_text(desc)
                    if corrected_text:
                        written_text = _write_table_ocr_artifacts(
                            folder,
                            desc,
                            table_image=table_image,
                            corrected_text=corrected_text,
                            corrected_filename=corrected_filename,
                            effective_source=detected_source,
                        )
                        effective_table_html = written_text or corrected_text
                        table_html_source = detected_source
                    elif table_image is not None:
                        _write_table_ocr_artifacts(
                            folder,
                            desc,
                            table_image=table_image,
                            corrected_text=None,
                            corrected_filename=None,
                            effective_source="mineru_raw_html",
                        )
                else:
                    desc = vlm_client.describe_table(
                        unit.extracted_table_html or "",
                        unit.caption_footnote_text or "",
                        candidate_ids,
                    )
                unit.vlm_description = desc.get("description")
                unit.tagged_entities = desc.get("identified_entities", [])
                unit.figure_subtype = desc.get("subtype")
                (folder / "vlm_description.json").write_text(
                    json.dumps(desc, ensure_ascii=False, indent=2), encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("VLM describe failed for %s: %s", unit.unit_id, exc)

            # 4.5 ★ unit router (three-way) — pure-rule based on stage 4 output.
            # Truncates downstream stage 6/7 LLM calls for figures that have
            # no quantitative data (reaction schemes, process flows, SEM,
            # apparatus, etc.) and physically removes pure noise (dedup tables,
            # decoration units that VLM couldn't identify).
            decision = unit_router.classify_unit_route(unit, desc)
            audit_log.write(unit.unit_id, doc_id, decision, desc)
            unit_router.write_route_decision(
                SETTINGS.project_root, unit.unit_id, doc_id, decision, desc,
            )

            if decision.route == "delete":
                logger.info(
                    "stage 4.5: DELETE unit %s (reason=%s)",
                    unit.unit_id, decision.reason,
                )
                unit_router.delete_unit_folder(SETTINGS.project_root, unit.unit_id)
                n_units_deleted += 1
                continue  # skip stages 5/6/7/8 entirely

            if decision.route == "register_layer1":
                logger.info(
                    "stage 4.5: REGISTER_LAYER1 unit %s (reason=%s)",
                    unit.unit_id, decision.reason,
                )
                matched = run_paragraph_match(unit, folder)
                if desc:
                    unit_router.write_pending_figure(
                        SETTINGS.project_root, doc_id, unit, desc,
                        related_paragraphs=matched,
                        route_confidence=decision.confidence,
                    )
                n_units_layer1 += 1
                continue  # skip stages 5/7/8; stage 9.5 will pick up the figure

            # decision.route == "extract_facts" — fall through to original flow

            # 5. 实体兜底打标 — 只在 DB 连通时跑（需 alias 行）
            if conn is not None:
                text_pool = " ".join(filter(None, [
                    unit.vlm_description, unit.caption_footnote_text, effective_table_html,
                ]))
                extra_tags = entity_tagger.tag_entities(
                    text_pool, candidate_ids=candidate_ids, conn=conn,
                )
                unit.tagged_entities = list({*(unit.tagged_entities or []), *extra_tags})

            # 6. 段落匹配 (PoC-2)
            matched = run_paragraph_match(unit, folder)

            # 7-8. 每 unit 一个事务：figure_table_unit + 它的 facts 一起成功或一起回滚。
            # 坏 unit 不会污染整批。
            try:
                if conn is not None:
                    insert_figure_table_unit(conn, unit)

                ex_result = extractor.extract(
                    unit,
                    matched_paragraphs=matched,
                    ontology_version=SETTINGS.ontology_version,
                    table_html_override=effective_table_html,
                    table_html_source=table_html_source,
                )
                facts = ex_result.facts
                apply_context_inheritance(
                    facts,
                    unit=unit,
                    matched_paragraphs=matched,
                    doc_profile=doc_profile_data,
                )

                # LLM 提议的 canonical 写到 facts 旁，curation aggregator 后续收集。
                if ex_result.proposed_canonicals:
                    (folder / "proposed_canonicals.json").write_text(
                        json.dumps(
                            {"proposed_canonicals": ex_result.proposed_canonicals},
                            ensure_ascii=False, indent=2,
                        ),
                        encoding="utf-8",
                    )

                # Tier-0 Fix 3: 落 coverage，让 Stage 9 区分"真没有" vs "抽取截断"。
                if ex_result.coverage:
                    (folder / "coverage.json").write_text(
                        json.dumps(ex_result.coverage, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )

                for f in facts:
                    pol = classify_polarity(
                        f.evidence_pointer.row or "", vlm_hint=str(f.polarity_hint),
                    )
                    if str(f.polarity_hint) == "unknown" and pol != "unknown":
                        f.polarity_hint = pol  # type: ignore[assignment]

                # comparison_group 绑定 — 同 unit 的 facts 共 region_id，
                # 传 unit 自己的 facts 等价于跨 unit 分组。
                for f in facts:
                    if f.comparison_group is None:
                        f.comparison_group = resolve_comparison_group(f, facts)

                # 总是写 facts.json，PoC 模式无 DB 也可审。
                (folder / "facts.json").write_text(
                    json.dumps(
                        {"facts": [f.model_dump(mode="json", exclude_none=True) for f in facts]},
                        ensure_ascii=False, indent=2,
                    ),
                    encoding="utf-8",
                )

                if conn is not None:
                    n_facts_total += bulk_insert_facts(conn, facts)  # 内部 commit
                else:
                    n_facts_total += len(facts)
                n_units_ok += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("unit %s failed, rolling back: %s", unit.unit_id, exc)
                if conn is not None:
                    conn.rollback()
                n_units_failed += 1

    try:
        resolve_structures_for_doc(SETTINGS.project_root, doc_id, enable_pubchem=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stage 4.6 structure resolution failed for %s: %s", doc_id, exc)

    logger.info(
        "ingest done: %d units ok (extract_facts), %d units to layer1, "
        "%d units deleted, %d units failed, %d facts (db_writes=%s)",
        n_units_ok, n_units_layer1, n_units_deleted, n_units_failed,
        n_facts_total, not skip_db,
    )


def _clear_existing_doc_units(units_root: Path, doc_id: str) -> None:
    """Remove stale generated unit folders for one doc before re-ingest."""
    if not units_root.exists():
        return
    prefix = f"U_{doc_id}_"
    root = units_root.resolve()
    for child in units_root.iterdir():
        if not child.is_dir() or not child.name.startswith(prefix):
            continue
        resolved = child.resolve()
        if root not in resolved.parents:
            raise RuntimeError(f"refusing to remove unit outside {root}: {resolved}")
        shutil.rmtree(resolved)


# ---------------------------------------------------------------------
# setup-db
# ---------------------------------------------------------------------
@cli.command("setup-db")
def setup_db_cmd() -> None:
    """通过 psql 跑 schema.sql + 所有 seed_*.sql。"""
    db_dir = SETTINGS.project_root / "db"
    files = [
        "schema.sql",
        "seed_property_directionality.sql",
        "seed_canonical_starter.sql",
        "seed_forbidden_merge_starter.sql",
        "seed_must_merge_starter.sql",
    ]
    for fname in files:
        path = db_dir / fname
        logger.info("applying %s", path)
        env = {
            "PGPASSWORD": SETTINGS.db.password,
        }
        cmd = [
            "psql", "-h", SETTINGS.db.host, "-p", str(SETTINGS.db.port),
            "-U", SETTINGS.db.user, "-d", SETTINGS.db.name,
            "-v", "ON_ERROR_STOP=1", "-f", str(path),
        ]
        subprocess.run(cmd, check=True, env={**env})


# ---------------------------------------------------------------------
# dev: tag a string
# ---------------------------------------------------------------------
@cli.command("tag")
@click.argument("text")
def tag_cmd(text: str) -> None:
    """对本地 DB 跑 entity tagger，打印 canonical ID。"""
    from .db.connection import get_conn
    from .pipeline.entity_tagger import tag_entities
    with get_conn() as conn:
        tags = tag_entities(text, conn=conn)
    click.echo("\n".join(tags) if tags else "(none)")


@cli.command("resolve-structures")
@click.argument("doc_ids", nargs=-1)
@click.option("--pubchem", is_flag=True, help="Enable PubChem lookups with SQLite cache.")
def resolve_structures_cmd(doc_ids: tuple[str, ...], pubchem: bool) -> None:
    """Run Stage 4.6 structure sidecar for one or more docs."""
    from .pipeline.structure_resolver import resolve_structures_for_doc

    targets = list(doc_ids)
    if not targets:
        layer1_dir = SETTINGS.project_root / "data" / "layer1"
        targets = sorted(p.name for p in layer1_dir.glob("*") if p.is_dir())
    total = 0
    for doc_id in targets:
        total += len(resolve_structures_for_doc(SETTINGS.project_root, doc_id, enable_pubchem=pubchem))
    click.echo(f"Stage 4.6 structure facts: {total}")


@cli.command("canonical-merge-queue")
@click.option("--out", type=click.Path(path_type=Path), default=None)
def canonical_merge_queue_cmd(out: Path | None) -> None:
    """Build proposed-canonical merge review queue."""
    from .pipeline.canonical_merge_queue import build_merge_queue, save_merge_queue

    out_path = out or SETTINGS.project_root / "data" / "canonical_merge_queue.json"
    queue = build_merge_queue(SETTINGS.project_root / "data" / "units")
    save_merge_queue(queue, out_path)
    click.echo(f"canonical merge queue: {len(queue['items'])} items -> {out_path}")


@cli.command("build-kg-export")
@click.option("--out-dir", type=click.Path(path_type=Path), default=None)
def build_kg_export_cmd(out_dir: Path | None) -> None:
    """Build explicit KG JSONL nodes and edges from pipeline artifacts."""
    from .pipeline.kg_export import build_kg_export

    summary = build_kg_export(SETTINGS.project_root, out_dir=out_dir)
    click.echo(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
