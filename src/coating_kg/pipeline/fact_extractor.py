"""调 Qwen-Plus 从 FigureTableUnit 抽 FactHyperedge JSON。

PoC-3：可选传 ``matched_paragraphs``（paragraph_matcher 的 per-unit 输出），
让 qwen-plus 从 prose 上下文补 process / substrate / test_method 等可选槽位。

Tier-0 修复 (2026-05-06):
  - Fix 1: ``unit_context`` 共享字段块（LLM 一次性输出 process/substrate 等，
    后处理合并到每条 fact）。修 Table-2 token 截断悄悄丢 24/36 cells 的 bug。
  - Fix 2: ``max_tokens=8192`` 显式设到 _call_qwen。
  - Fix 3: per-unit ``coverage`` 自报（基于 extracted_table_html cell 数），
    coverage < 60% 时打 warning。
  - fact_id 改全局唯一 ``F_<unit_id>_<seq:03d>``。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from tenacity import retry, stop_after_attempt, wait_exponential

try:
    from openai import OpenAI  # type: ignore[import-not-found]
    _OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_AVAILABLE = False

from ..config import SETTINGS
from ..db.models import FactHyperedge, FigureTableUnit
from .canonical_resolver import ALLOWED_SUB_TYPES, CanonicalResolver, by_subtype

logger = logging.getLogger(__name__)


_PROMPT_PATH = SETTINGS.project_root / "prompts" / "fact_extract.txt"


@dataclass
class ExtractionResult:
    facts: list[FactHyperedge] = field(default_factory=list)
    proposed_canonicals: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)


class FactExtractor:
    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        resolver: CanonicalResolver | None = None,
    ) -> None:
        self.model = model or SETTINGS.models.qwen_text
        self.api_key = api_key or SETTINGS.openai.api_key
        self.base_url = base_url or SETTINGS.openai.base_url
        self._client: Any = None
        if _OPENAI_AVAILABLE and self.api_key:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        self._prompt_template: str | None = None
        self.resolver = resolver or CanonicalResolver()
        self._canonical_blocks = _build_canonical_blocks(self.resolver)

    def extract(
        self,
        unit: FigureTableUnit,
        *,
        matched_paragraphs: list[dict[str, Any]] | None = None,
        ontology_version: str | None = None,
    ) -> ExtractionResult:
        if self._client is None:
            logger.warning(
                "FactExtractor has no OpenAI client (api_key=%s); returning empty result for unit=%s",
                bool(self.api_key), unit.unit_id,
            )
            return ExtractionResult()

        prompt = self._render_prompt(unit, matched_paragraphs or [])
        raw = self._call_qwen(prompt)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Tier-0 debug: 落 raw LLM 输出方便看 malformed 在哪
            # （多半是 // 注释、trailing commas、或 schema 里 ``...`` 被抄出来）
            from pathlib import Path
            dump_dir = Path("data/units") / unit.unit_id
            dump_dir.mkdir(parents=True, exist_ok=True)
            (dump_dir / "raw_llm_response.txt").write_text(raw, encoding="utf-8")
            # 宽松恢复：去掉 // 注释 + trailing commas
            cleaned = _permissive_json_clean(raw)
            try:
                payload = json.loads(cleaned)
                logger.warning(
                    "Permissive JSON cleanup recovered %s; original error was %s. "
                    "Raw saved to %s/raw_llm_response.txt",
                    unit.unit_id, exc, dump_dir,
                )
            except json.JSONDecodeError as exc2:
                logger.warning(
                    "Failed to parse fact-extraction JSON for unit=%s: %s "
                    "(recovery also failed: %s). Raw saved to %s/raw_llm_response.txt",
                    unit.unit_id, exc, exc2, dump_dir,
                )
                return ExtractionResult()

        unit_ctx = payload.get("unit_context") or {}
        raw_facts = payload.get("facts", []) or []

        expected = _estimate_data_cells(unit.extracted_table_html or "")
        extracted = len(raw_facts)
        coverage_pct: float | None = None
        if expected > 0:
            coverage_pct = round(100.0 * extracted / expected, 1)
            if coverage_pct < 60.0:
                logger.warning(
                    "LOW COVERAGE on %s: extracted %d / expected %d data cells (%.0f%%) - "
                    "likely token truncation or LLM skipped rows.",
                    unit.unit_id, extracted, expected, coverage_pct,
                )

        result = ExtractionResult(
            coverage={
                "expected_data_cells": expected,
                "extracted_facts": extracted,
                "coverage_pct": coverage_pct,
            },
        )

        # Tier-0: 这些字段不让 LLM 出，pipeline 端自己 set。
        unit_region_type = "FIGURE" if unit.unit_type == "figure" else "TABLE"
        n_skipped_empty_cell = 0
        for i, raw_fact in enumerate(raw_facts):
            merged = _merge_unit_context(unit_ctx, raw_fact)
            # pipeline 控制的 identity / 默认值
            merged["fact_id"] = f"F_{unit.unit_id}_{i+1:03d}"
            merged.setdefault("ontology_version", ontology_version or SETTINGS.ontology_version)
            merged.setdefault("doc_id", unit.doc_id)
            merged.setdefault("human_validated", False)
            # 回填 evidence_pointer 里 LLM 不再出的冗余字段。
            # V1.2.5: 也回填 unit_id (spec §7.2) + bbox（V1 demo 高亮原文用）。
            # unit_id 隐含在 fact_id 里；bbox 在 unit 自己上。
            ep = merged.get("evidence_pointer") or {}
            if isinstance(ep, dict):
                ep.setdefault("doc_id", unit.doc_id)
                ep.setdefault("region_type", unit_region_type)
                ep.setdefault("region_id", unit.region_id or "")
                ep.setdefault("unit_id", unit.unit_id)        # ★ V1.2.5
                if unit.bbox:                                  # ★ V1.2.5
                    ep.setdefault("bbox", unit.bbox)
                merged["evidence_pointer"] = ep

            # ★ V1.2.5 task #3: 空 cell 守门。
            # 抽样发现 14% null result_value 的 fact 是 cell 也空——LLM 把
            # 表头 / 分隔行 / 空行也 emit 成 fact 了。这些是 noise 不是测量。
            cell = (ep.get("cell") or "").strip() if isinstance(ep, dict) else ""
            _normalise_fact_for_schema(merged)
            rv_text = (merged.get("result_value_text") or "").strip()
            if not cell and not rv_text:
                n_skipped_empty_cell += 1
                continue

            try:
                result.facts.append(FactHyperedge.model_validate(merged))
            except Exception as exc:
                logger.warning("Skipping malformed fact #%d for %s: %s", i, unit.unit_id, exc)

        if n_skipped_empty_cell > 0:
            logger.info(
                "Skipped %d facts with empty cell + empty result_value_text on %s "
                "(non-data rows like headers / separators)",
                n_skipped_empty_cell, unit.unit_id,
            )

        for prop in payload.get("proposed_canonicals", []) or []:
            kind = prop.get("kind")
            sub_type = prop.get("sub_type")
            allowed = ALLOWED_SUB_TYPES.get(kind, set())
            if kind and sub_type and sub_type not in allowed:
                logger.info(
                    "proposed canonical %s with sub_type=%r not in allowed %s",
                    prop.get("proposed_id"), sub_type, sorted(allowed),
                )
            result.proposed_canonicals.append(prop)

        return result

    def _render_prompt(
        self, unit: FigureTableUnit, matched_paragraphs: list[dict[str, Any]],
    ) -> str:
        if self._prompt_template is None:
            self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template.format(
            doc_id=unit.doc_id,
            region_id=unit.region_id or "",
            page=unit.page or "",
            unit_type=unit.unit_type,
            vlm_description=unit.vlm_description or "",
            extracted_table_html=unit.extracted_table_html or "",
            caption_footnote_text=unit.caption_footnote_text or "",
            tagged_entities=", ".join(unit.tagged_entities or []),
            matched_paragraphs=_format_matched(matched_paragraphs),
            **self._canonical_blocks,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def _call_qwen(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=8192,
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("empty content from model")
        return content


_NUMERIC_CELL_RE = re.compile(
    r"""^\s*
        -?\d+(?:[.,]\d+)?
        (?:\s*[a-zA-Zµ%°/.\-]+)?
        \s*$""",
    re.VERBOSE,
)
_TD_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _permissive_json_clean(text: str) -> str:
    """尽力清理 LLM JSON 输出常见错误。

    去掉:
      - // 行注释（LLM 有时从 schema 文档抄出来）
      - /* 块注释 */
      - } 或 ] 前的 trailing comma
      - markdown ``` / ```json 围栏
    """
    # 去 markdown 围栏
    text = re.sub(r"^```(?:json)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    # 去 // 行注释（不区分字符串内外，naive 但够用）
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"(?<=[\s,\{\[\]\}])//[^\n]*", "", text)
    # 去 /* ... */ 块注释
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # 去 ] 或 } 前的 trailing comma
    text = re.sub(r",(\s*[\]\}])", r"\1", text)
    return text


def _estimate_data_cells(table_html: str) -> int:
    if not table_html:
        return 0
    n = 0
    for raw in _TD_RE.findall(table_html):
        text = _TAG_RE.sub("", raw).strip()
        if not text:
            continue
        if _NUMERIC_CELL_RE.match(text):
            n += 1
        elif text in {"-", "n.d.", "n/a", "—", "N/A", "n.m."}:
            n += 1
    return n


def _merge_unit_context(unit_ctx: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
    if not unit_ctx:
        return dict(fact)
    merged = dict(unit_ctx)
    if "test_condition" in unit_ctx and "test_condition" in fact and fact["test_condition"]:
        merged_tc = dict(unit_ctx.get("test_condition") or {})
        merged_tc.update(fact["test_condition"] or {})
        fact = dict(fact)
        fact["test_condition"] = merged_tc
    for k, v in fact.items():
        if v is None:
            continue
        merged[k] = v
    return merged


def _normalise_fact_for_schema(fact: dict[str, Any]) -> None:
    """Coerce harmless LLM nulls so one missing label does not drop a fact."""
    if fact.get("application") in {None, ""}:
        fact["application"] = "unknown"


def _format_matched(matched_paragraphs: list[dict[str, Any]]) -> str:
    if not matched_paragraphs:
        return "(none)"
    lines: list[str] = []
    for m in matched_paragraphs:
        page = m.get("page")
        page_tag = f" p{page}" if page is not None else ""
        score = m.get("score")
        score_tag = f" score={score:.2f}" if isinstance(score, (int, float)) else ""
        text = (m.get("text") or "").strip().replace("\n", " ")
        lines.append(f"[#{m.get('para_id', '?')}{page_tag}{score_tag}] {text}")
    return "\n".join(lines)


def _build_canonical_blocks(resolver: CanonicalResolver) -> dict[str, str]:
    def fmt_hierarchical(kind: str) -> str:
        grouped = by_subtype(resolver, kind)
        all_subs = ALLOWED_SUB_TYPES.get(kind, set())
        lines: list[str] = []
        for sub in sorted(all_subs):
            items = grouped.get(sub, [])
            lines.append(f"  [{sub}]")
            if items:
                for cid, name in items:
                    lines.append(f"    - {cid}  ({name})")
            else:
                lines.append("    (no seeded ID - propose if encountered)")
        return "\n".join(lines)

    def fmt_flat(kind: str) -> str:
        rows = sorted(
            (cid, n.get("name", ""))
            for cid, n in resolver.nodes_by_id.items() if n["type"] == kind
        )
        if not rows:
            return "  (none seeded)"
        return "\n".join(f"  - {cid}  ({name})" for cid, name in rows)

    return {
        "canonical_app":  fmt_hierarchical("APP"),
        "canonical_mat":  fmt_hierarchical("MAT"),
        "canonical_prop": fmt_hierarchical("PROP"),
        "canonical_sub":  fmt_hierarchical("SUB"),
        "canonical_proc": fmt_flat("PROC"),
        "canonical_test": fmt_flat("TEST"),
    }
