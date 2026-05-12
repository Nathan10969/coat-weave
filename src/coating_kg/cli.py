from __future__ import annotations

import json
import logging
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import click

from .config import SETTINGS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("coating_kg")


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
    4. VLM 描述（figure→Qwen-VL, table→Qwen-Plus）
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
    from .pipeline import unit_router  # ★ Stage 4.5 — three-way routing

    logger.info("ingest start: %s (dry_run=%s, skip_db=%s)", pdf_path, dry_run, skip_db)

    # 1. 版面 — MinerU 在线 API（缺 token 或无结果时 raise MinerUError）
    layout = pdf_layout.parse_pdf(pdf_path, SETTINGS.mineru_output_dir)
    doc_id = layout["doc_id"]
    # Re-ingesting a PDF should replace its stage 4.5 pending Layer 1 figures,
    # not append another copy for the same doc, even if the metadata gate exits.
    unit_router.clear_pending_figures(SETTINGS.project_root, doc_id)

    # ★ V1.2.5 task #4b + C：早期 IPC 闸门 + section_split_mode 落 patent_meta。
    # MinerU 后立刻抽 metadata 判 is_coating_patent，非涂料早跳过省 LLM 钱
    # （~¥0.5/篇 × 30% imposter ≈ ¥45/348 篇）。
    # section_split mode (found/fallback) 也写进 patent_meta 供批跑可观测。
    from .pipeline import patent_metadata_extractor as _pme
    from .pipeline.section_split import split_examples_section as _split_examples
    from .pipeline.paragraph_extractor import examples_page_range as _examples_page_range

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
            full_text = "\n".join(
                (b.get("text") or "") for b in blocks_for_split if isinstance(b, dict)
            )
            section_span = _split_examples(full_text)
            meta["section_split_mode"] = "found" if section_span is not None else "fallback"
            ex_range = _examples_page_range(layout)
            meta["examples_page_range"] = list(ex_range) if ex_range else None

            _pme.save_patent_metadata(SETTINGS.project_root, doc_id, meta)
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
    mineru_doc_dir = Path(layout["source"]).parent if layout.get("source") else SETTINGS.mineru_output_dir / doc_id
    units = list(
        unit_extractor.iter_figure_table_units(
            layout,
            examples_only=True,
            image_root=mineru_doc_dir,
        )
    )
    logger.info("found %d figure/table units in Examples section", len(units))

    # 3. 每 unit 物化到 data/units/<unit_id>/
    units_root = SETTINGS.project_root / "data" / "units"
    for unit in units:
        materialize_unit(unit, units_root, mineru_root=SETTINGS.mineru_output_dir)
    logger.info("materialised %d unit folders under %s", len(units), units_root)

    if dry_run:
        logger.info("dry-run: stopping after stage 3 (no LLM, no DB).")
        return

    # PoC-2 候选段落 — 每篇 doc 算一次
    page_range = examples_page_range(layout)
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

            # 4. VLM 描述 — 失败不致命
            desc: dict | None = None
            try:
                if unit.unit_type == "figure" and unit.image_path:
                    desc = vlm_client.describe_figure(unit.image_path, candidate_ids)
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
                    )
                n_units_layer1 += 1
                continue  # skip stages 5/7/8; stage 9.5 will pick up the figure

            # decision.route == "extract_facts" — fall through to original flow

            # 5. 实体兜底打标 — 只在 DB 连通时跑（需 alias 行）
            if conn is not None:
                text_pool = " ".join(filter(None, [
                    unit.vlm_description, unit.caption_footnote_text, unit.extracted_table_html,
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
                )
                facts = ex_result.facts

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

    logger.info(
        "ingest done: %d units ok (extract_facts), %d units to layer1, "
        "%d units deleted, %d units failed, %d facts (db_writes=%s)",
        n_units_ok, n_units_layer1, n_units_deleted, n_units_failed,
        n_facts_total, not skip_db,
    )


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


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
