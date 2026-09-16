"""Call the configured text LLM to extract FactHyperedge JSON from a unit.

PoC-3：可选传 ``matched_paragraphs``（paragraph_matcher 的 per-unit 输出），
The model uses matched prose context to fill optional process / substrate /
test_method slots.

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
import os
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
from .evidence_ledger import format_evidence_ledger_for_prompt

logger = logging.getLogger(__name__)


_PROMPT_PATH = SETTINGS.project_root / "prompts" / "fact_extract.txt"
_DEFAULT_FACT_MAX_TOKENS = 65536


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default
    return max(1024, value)


def _env_nonnegative_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %d", name, raw, default)
        return default
    return max(0, value)


@dataclass
class ExtractionResult:
    facts: list[FactHyperedge] = field(default_factory=list)
    proposed_canonicals: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    cell_annotations: list[dict[str, Any]] = field(default_factory=list)
    extraction_plan: dict[str, Any] = field(default_factory=dict)
    sample_map: list[dict[str, Any]] = field(default_factory=list)
    context_assertions: list[dict[str, Any]] = field(default_factory=list)
    coverage_audit: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)
    schema_rejects: list[dict[str, Any]] = field(default_factory=list)
    repair_attempts: int = 0


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
        self.max_tokens = _env_int("QWEN_FACT_MAX_TOKENS", _DEFAULT_FACT_MAX_TOKENS)
        self._client: Any = None
        if _OPENAI_AVAILABLE and self.api_key:
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        self._prompt_template: str | None = None
        self.resolver = resolver or CanonicalResolver()
        self._canonical_blocks = _build_canonical_blocks(self.resolver)
        self.repair_attempts = _env_nonnegative_int("STAGE7_REPAIR_ATTEMPTS", 2)

    def extract(
        self,
        unit: FigureTableUnit,
        *,
        matched_paragraphs: list[dict[str, Any]] | None = None,
        ontology_version: str | None = None,
        table_html_override: str | None = None,
        table_html_source: str = "mineru_raw_html",
        evidence_ledger: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        if self._client is None:
            logger.warning(
                "FactExtractor has no OpenAI client (api_key=%s); returning empty result for unit=%s",
                bool(self.api_key), unit.unit_id,
            )
            return ExtractionResult()

        effective_table_html = table_html_override if table_html_override is not None else (
            unit.extracted_table_html or ""
        )
        prompt = self._render_prompt(
            unit,
            matched_paragraphs or [],
            table_html=effective_table_html,
            table_html_source=table_html_source,
            evidence_ledger=evidence_ledger,
        )
        raw = self._call_qwen(prompt)
        payload = self._parse_payload(raw, unit)
        if payload is None:
            return _parse_failed_result(unit.unit_id)

        result = self._build_result_from_payload(
            unit,
            payload,
            ontology_version=ontology_version,
            effective_table_html=effective_table_html,
            table_html_source=table_html_source,
        )
        result.validation = validate_stage7_result(
            unit_id=unit.unit_id,
            facts=result.facts,
            context_assertions=result.context_assertions,
            sample_map=result.sample_map,
            coverage=result.coverage,
            coverage_audit=result.coverage_audit,
            evidence_ledger=evidence_ledger,
            schema_rejects=result.schema_rejects,
        )
        if result.validation.get("retryable") and self.repair_attempts > 0:
            result = self._repair_extraction(
                unit,
                matched_paragraphs=matched_paragraphs or [],
                ontology_version=ontology_version,
                effective_table_html=effective_table_html,
                table_html_source=table_html_source,
                evidence_ledger=evidence_ledger,
                initial_result=result,
            )
        return result

    def _parse_payload(self, raw: str, unit: FigureTableUnit) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            from pathlib import Path

            dump_dir = Path("data/units") / unit.unit_id
            dump_dir.mkdir(parents=True, exist_ok=True)
            (dump_dir / "raw_llm_response.txt").write_text(raw, encoding="utf-8")
            cleaned = _permissive_json_clean(raw)
            try:
                payload = json.loads(cleaned)
                logger.warning(
                    "Permissive JSON cleanup recovered %s; original error was %s. "
                    "Raw saved to %s/raw_llm_response.txt",
                    unit.unit_id,
                    exc,
                    dump_dir,
                )
            except json.JSONDecodeError as exc2:
                logger.warning(
                    "Failed to parse fact-extraction JSON for unit=%s: %s "
                    "(recovery also failed: %s). Raw saved to %s/raw_llm_response.txt",
                    unit.unit_id,
                    exc,
                    exc2,
                    dump_dir,
                )
                return None
        if not isinstance(payload, dict):
            logger.warning("Fact extraction payload for unit=%s was not an object.", unit.unit_id)
            return None
        return payload

    def _build_result_from_payload(
        self,
        unit: FigureTableUnit,
        payload: dict[str, Any],
        *,
        ontology_version: str | None,
        effective_table_html: str,
        table_html_source: str,
    ) -> ExtractionResult:
        unit_ctx = payload.get("unit_context") or {}
        raw_facts = payload.get("facts", []) or []
        payload_coverage = payload.get("coverage") or {}
        raw_cell_annotations = payload.get("cell_annotations") or []
        raw_extraction_plan = payload.get("extraction_plan") or {}
        raw_sample_map = payload.get("sample_map") or []
        raw_context_assertions = payload.get("context_assertions") or []
        raw_coverage_audit = payload.get("coverage_audit") or {}
        raw_numeric_data_cells = _estimate_data_cells(effective_table_html)
        result = ExtractionResult()
        if isinstance(raw_extraction_plan, dict):
            result.extraction_plan = raw_extraction_plan
        if isinstance(raw_cell_annotations, list):
            result.cell_annotations = [
                row for row in raw_cell_annotations if isinstance(row, dict)
            ]
        if isinstance(raw_sample_map, list):
            result.sample_map = [row for row in raw_sample_map if isinstance(row, dict)]
        if isinstance(raw_context_assertions, list):
            result.context_assertions = [
                row for row in raw_context_assertions if isinstance(row, dict)
            ]
        if isinstance(raw_coverage_audit, dict):
            result.coverage_audit = raw_coverage_audit

        unit_region_type = "FIGURE" if unit.unit_type == "figure" else "TABLE"
        n_skipped_empty_cell = 0
        if not isinstance(raw_facts, list):
            raw_facts = []
        for i, raw_fact in enumerate(raw_facts):
            if not isinstance(raw_fact, dict):
                result.schema_rejects.append(
                    {"fact_index": i, "error": "fact is not an object", "raw_fact": raw_fact}
                )
                continue
            merged = _merge_unit_context(unit_ctx if isinstance(unit_ctx, dict) else {}, raw_fact)
            merged["fact_id"] = f"F_{unit.unit_id}_{i+1:03d}"
            merged.setdefault("ontology_version", ontology_version or SETTINGS.ontology_version)
            merged.setdefault("doc_id", unit.doc_id)
            merged.setdefault("human_validated", False)
            ep = merged.get("evidence_pointer") or {}
            if isinstance(ep, dict):
                ep.setdefault("doc_id", unit.doc_id)
                ep.setdefault("region_type", unit_region_type)
                ep.setdefault("region_id", unit.region_id or "")
                ep.setdefault("unit_id", unit.unit_id)
                if unit.bbox:
                    ep.setdefault("bbox", unit.bbox)
                merged["evidence_pointer"] = ep

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
                result.schema_rejects.append(
                    {
                        "fact_index": i,
                        "error": str(exc),
                        "raw_fact": _safe_json_obj(merged),
                    }
                )

        if n_skipped_empty_cell > 0:
            logger.info(
                "Skipped %d facts with empty cell + empty result_value_text on %s "
                "(non-data rows like headers / separators)",
                n_skipped_empty_cell,
                unit.unit_id,
            )

        result.coverage = _build_coverage(
            unit_id=unit.unit_id,
            payload_coverage=payload_coverage if isinstance(payload_coverage, dict) else {},
            cell_annotations_count=len(result.cell_annotations),
            raw_numeric_data_cells=raw_numeric_data_cells,
            raw_llm_fact_count=len(raw_facts),
            validated_fact_count=len(result.facts),
            table_html_source=table_html_source,
        )

        for prop in payload.get("proposed_canonicals", []) or []:
            if not isinstance(prop, dict):
                continue
            kind = prop.get("kind")
            sub_type = prop.get("sub_type")
            allowed = ALLOWED_SUB_TYPES.get(kind, set())
            if kind and sub_type and sub_type not in allowed:
                logger.info(
                    "proposed canonical %s with sub_type=%r not in allowed %s",
                    prop.get("proposed_id"),
                    sub_type,
                    sorted(allowed),
                )
            result.proposed_canonicals.append(prop)
        return result

    def _repair_extraction(
        self,
        unit: FigureTableUnit,
        *,
        matched_paragraphs: list[dict[str, Any]],
        ontology_version: str | None,
        effective_table_html: str,
        table_html_source: str,
        evidence_ledger: dict[str, Any] | None,
        initial_result: ExtractionResult,
    ) -> ExtractionResult:
        best = initial_result
        for attempt in range(1, self.repair_attempts + 1):
            prompt = self._render_repair_prompt(
                unit,
                matched_paragraphs,
                table_html=effective_table_html,
                table_html_source=table_html_source,
                evidence_ledger=evidence_ledger,
                validation=best.validation,
                attempt=attempt,
            )
            raw = self._call_qwen(prompt)
            payload = self._parse_payload(raw, unit)
            if payload is None:
                best.repair_attempts = attempt
                continue
            candidate = self._build_result_from_payload(
                unit,
                payload,
                ontology_version=ontology_version,
                effective_table_html=effective_table_html,
                table_html_source=table_html_source,
            )
            candidate.repair_attempts = attempt
            candidate.validation = validate_stage7_result(
                unit_id=unit.unit_id,
                facts=candidate.facts,
                context_assertions=candidate.context_assertions,
                sample_map=candidate.sample_map,
                coverage=candidate.coverage,
                coverage_audit=candidate.coverage_audit,
                evidence_ledger=evidence_ledger,
                schema_rejects=candidate.schema_rejects,
            )
            if _validation_score(candidate.validation) >= _validation_score(best.validation):
                best = candidate
            if best.validation.get("final_status") == "ok":
                break
        best.repair_attempts = max(best.repair_attempts, 1)
        return best

    def _render_repair_prompt(
        self,
        unit: FigureTableUnit,
        matched_paragraphs: list[dict[str, Any]],
        *,
        table_html: str | None,
        table_html_source: str,
        evidence_ledger: dict[str, Any] | None,
        validation: dict[str, Any],
        attempt: int,
    ) -> str:
        base = self._render_prompt(
            unit,
            matched_paragraphs,
            table_html=table_html,
            table_html_source=table_html_source,
            evidence_ledger=evidence_ledger,
        )
        compact_validation = json.dumps(validation, ensure_ascii=False, indent=2)[:12000]
        return (
            f"{base}\n\n"
            "STAGE 7C VALIDATION FAILED. This is Stage 7D repair attempt "
            f"{attempt}. Return a COMPLETE corrected JSON object using the same schema. "
            "Do not return a patch. Keep valid facts/sample_map/context_assertions, "
            "but repair only the failed evidence-ledger obligations below. If evidence "
            "cannot be bound, mark it in coverage_audit.unresolved_bindings instead of "
            "inventing a global unknown sample.\n\n"
            f"VALIDATION_JSON:\n{compact_validation}\n"
        )

    def _render_prompt(
        self,
        unit: FigureTableUnit,
        matched_paragraphs: list[dict[str, Any]],
        *,
        table_html: str | None = None,
        table_html_source: str = "mineru_raw_html",
        evidence_ledger: dict[str, Any] | None = None,
    ) -> str:
        if self._prompt_template is None:
            self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._prompt_template.format(
            doc_id=unit.doc_id,
            region_id=unit.region_id or "",
            page=unit.page or "",
            unit_type=unit.unit_type,
            vlm_description=unit.vlm_description or "",
            extracted_table_html=table_html if table_html is not None else (unit.extracted_table_html or ""),
            table_html_source=table_html_source,
            caption_footnote_text=unit.caption_footnote_text or "",
            tagged_entities=", ".join(unit.tagged_entities or []),
            matched_paragraphs=_format_matched(matched_paragraphs),
            evidence_ledger=format_evidence_ledger_for_prompt(evidence_ledger),
            **self._canonical_blocks,
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
    def _call_qwen(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=self.max_tokens,
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


def _coerce_nonnegative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _build_coverage(
    *,
    unit_id: str,
    payload_coverage: dict[str, Any],
    cell_annotations_count: int,
    raw_numeric_data_cells: int,
    raw_llm_fact_count: int,
    validated_fact_count: int,
    table_html_source: str,
) -> dict[str, Any]:
    """Build backwards-compatible coverage with semantic result-cell support."""
    semantic_expected = _coerce_nonnegative_int(
        _first_present(payload_coverage, "expected_result_cells", "expected_semantic_fact_cells")
    )
    semantic_extracted = _coerce_nonnegative_int(
        _first_present(payload_coverage, "result_cells_extracted", "emitted_fact_cells")
    )

    if semantic_expected is not None:
        expected = semantic_expected
        extracted = semantic_extracted if semantic_extracted is not None else validated_fact_count
        basis = "semantic_result_cells"
    else:
        expected = raw_numeric_data_cells
        extracted = raw_llm_fact_count
        basis = "raw_numeric_cells"

    coverage_pct: float | None = None
    if expected > 0:
        coverage_pct = round(100.0 * extracted / expected, 1)
        if coverage_pct < 60.0:
            logger.warning(
                "LOW COVERAGE on %s: extracted %d / expected %d (%s, %.0f%%).",
                unit_id, extracted, expected, basis, coverage_pct,
            )

    return {
        "expected_data_cells": expected,
        "extracted_facts": extracted,
        "coverage_pct": coverage_pct,
        "coverage_basis": basis,
        "table_html_source": table_html_source,
        "expected_result_cells": semantic_expected,
        "result_cells_extracted_reported": semantic_extracted,
        "cell_annotations_count": cell_annotations_count,
        "raw_numeric_data_cells": raw_numeric_data_cells,
        "raw_llm_facts": raw_llm_fact_count,
        "validated_facts": validated_fact_count,
        "role_counts": (
            payload_coverage.get("role_counts")
            or payload_coverage.get("cell_role_counts")
            or {}
        ),
        "coverage_notes": (
            payload_coverage.get("notes")
            or payload_coverage.get("coverage_notes")
            or ""
        ),
    }


def validate_stage7_result(
    *,
    unit_id: str,
    facts: list[FactHyperedge],
    context_assertions: list[dict[str, Any]],
    sample_map: list[dict[str, Any]],
    coverage: dict[str, Any],
    coverage_audit: dict[str, Any],
    evidence_ledger: dict[str, Any] | None,
    schema_rejects: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic Stage 7C gate for coverage, schema, and evidence binding."""
    del sample_map, coverage_audit
    expected = _coerce_nonnegative_int(
        _first_present(coverage, "expected_data_cells", "expected_result_cells")
    ) or 0
    reported_extracted = _coerce_nonnegative_int(
        _first_present(coverage, "extracted_facts", "result_cells_extracted_reported")
    )
    validated_extracted = len(facts)
    validated_coverage_pct: float | None = None
    if expected > 0:
        validated_coverage_pct = round(100.0 * validated_extracted / expected, 1)

    issues: list[dict[str, Any]] = []
    if expected > 0 and validated_extracted == 0:
        issues.append(
            _validation_issue(
                "zero_extraction",
                "error",
                True,
                f"Expected {expected} result/formulation cells but validated zero facts.",
            )
        )
    elif expected > 0 and validated_coverage_pct is not None and validated_coverage_pct < 80.0:
        issues.append(
            _validation_issue(
                "low_coverage",
                "error",
                True,
                f"Validated coverage {validated_coverage_pct}% is below 80% threshold.",
            )
        )

    if schema_rejects:
        issues.append(
            _validation_issue(
                "schema_rejects",
                "error",
                True,
                f"{len(schema_rejects)} raw facts could not be validated after normalizer coercion.",
                {"schema_reject_count": len(schema_rejects)},
            )
        )

    for assertion in context_assertions:
        if not isinstance(assertion, dict):
            continue
        if not (assertion.get("source_ledger_ids") or assertion.get("ledger_ids")):
            issues.append(
                _validation_issue(
                    "context_assertion_missing_ledger",
                    "error",
                    True,
                    "A context_assertion lacks source_ledger_ids.",
                    {"assertion_id": assertion.get("assertion_id")},
                )
            )

    signals = _ledger_context_signals(evidence_ledger)
    for context_field, ledger_ids in sorted(signals.items()):
        if not ledger_ids:
            continue
        if _has_stage7_context_field(facts, context_assertions, context_field):
            continue
        issues.append(
            _validation_issue(
                f"missing_context_assertion:{context_field}",
                "error",
                True,
                f"Evidence ledger indicates {context_field}, but Stage 7 emitted no fact field or context_assertion.",
                {"ledger_ids": ledger_ids[:8]},
            )
        )

    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    if any(issue["code"] == "zero_extraction" for issue in issues):
        final_status = "blocked_zero_extraction"
    elif error_count and any(issue.get("retryable") for issue in issues):
        final_status = "needs_repair"
    elif error_count:
        final_status = "failed_validation"
    elif warning_count:
        final_status = "warning"
    else:
        final_status = "ok"
    return {
        "schema_version": "stage7_validation_v1",
        "unit_id": unit_id,
        "final_status": final_status,
        "retryable": any(bool(issue.get("retryable")) for issue in issues),
        "expected_cells": expected,
        "reported_extracted_cells": reported_extracted,
        "validated_fact_count": validated_extracted,
        "validated_coverage_pct": validated_coverage_pct,
        "issue_counts": {"error": error_count, "warning": warning_count},
        "issues": issues,
        "schema_rejects": schema_rejects,
    }


def _validation_issue(
    code: str,
    severity: str,
    retryable: bool,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "code": code,
        "severity": severity,
        "retryable": retryable,
        "message": message,
    }
    if extra:
        row.update(extra)
    return row


def _parse_failed_result(unit_id: str) -> ExtractionResult:
    return ExtractionResult(
        validation={
            "schema_version": "stage7_validation_v1",
            "unit_id": unit_id,
            "final_status": "parse_failed",
            "retryable": True,
            "expected_cells": 0,
            "reported_extracted_cells": None,
            "validated_fact_count": 0,
            "validated_coverage_pct": None,
            "issue_counts": {"error": 1, "warning": 0},
            "issues": [
                _validation_issue(
                    "parse_failed",
                    "error",
                    True,
                    "Model response could not be parsed as JSON.",
                )
            ],
            "schema_rejects": [],
        }
    )


def _ledger_context_signals(evidence_ledger: dict[str, Any] | None) -> dict[str, list[str]]:
    signals = {"substrate": [], "process": [], "test_standard": []}
    if not isinstance(evidence_ledger, dict) or not isinstance(evidence_ledger.get("entries"), list):
        return signals
    for entry in evidence_ledger["entries"]:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "")
        ledger_id = str(entry.get("ledger_id") or "")
        if not ledger_id:
            continue
        low = text.casefold()
        if _SUBSTRATE_SIGNAL_RE.search(low):
            signals["substrate"].append(ledger_id)
        if _PROCESS_SIGNAL_RE.search(low):
            signals["process"].append(ledger_id)
        if _STANDARD_SIGNAL_RE.search(text):
            signals["test_standard"].append(ledger_id)
    return signals


def _has_stage7_context_field(
    facts: list[FactHyperedge],
    context_assertions: list[dict[str, Any]],
    field: str,
) -> bool:
    return _facts_have_context_field(facts, field) or _assertions_have_context_field(
        context_assertions, field
    )


def _facts_have_context_field(facts: list[FactHyperedge], field: str) -> bool:
    for fact in facts:
        if field == "substrate":
            substrate = getattr(fact, "substrate", None)
            if isinstance(substrate, dict) and substrate.get("tested") not in (None, "", "unknown"):
                return True
        elif field == "process":
            if getattr(fact, "process", None):
                return True
        elif field == "test_standard":
            condition = getattr(fact, "test_condition", None)
            if isinstance(condition, dict) and condition.get("standard_id"):
                return True
            if _standard_from_test_method(getattr(fact, "test_method", None)):
                return True
    return False


def _assertions_have_context_field(context_assertions: list[dict[str, Any]], field: str) -> bool:
    aliases = {
        "substrate": {"substrate", "tested_substrate"},
        "process": {"process", "preparation", "application_process"},
        "test_standard": {"test_standard", "standard", "standard_id", "test_condition"},
    }[field]
    for assertion in context_assertions:
        if not isinstance(assertion, dict):
            continue
        raw_field = str(assertion.get("field") or "").strip().casefold()
        if raw_field not in aliases:
            continue
        if field == "test_standard" and raw_field == "test_condition":
            value = assertion.get("value")
            if isinstance(value, dict) and not (
                value.get("standard_id") or value.get("standard") or value.get("test_standard")
            ):
                continue
        if assertion.get("value") in (None, "", [], {}, "unknown"):
            continue
        return True
    return False


def _validation_score(validation: dict[str, Any]) -> float:
    status_weight = {
        "ok": 1000.0,
        "warning": 850.0,
        "needs_repair": 450.0,
        "failed_validation": 250.0,
        "blocked_zero_extraction": 100.0,
        "parse_failed": 0.0,
    }.get(str(validation.get("final_status") or ""), 0.0)
    coverage = validation.get("validated_coverage_pct")
    try:
        coverage_score = float(coverage or 0.0)
    except (TypeError, ValueError):
        coverage_score = 0.0
    issue_counts = validation.get("issue_counts") if isinstance(validation.get("issue_counts"), dict) else {}
    errors = int(issue_counts.get("error") or 0)
    return status_weight + min(coverage_score, 100.0) - (errors * 10.0)


def _safe_json_obj(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _merge_unit_context(unit_ctx: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
    if not unit_ctx:
        return dict(fact)
    merged = dict(unit_ctx)
    if "test_condition" in unit_ctx and "test_condition" in fact and fact["test_condition"]:
        base_tc = unit_ctx.get("test_condition") if isinstance(unit_ctx.get("test_condition"), dict) else {}
        fact_tc = fact.get("test_condition") if isinstance(fact.get("test_condition"), dict) else {}
        merged_tc = dict(base_tc or {})
        merged_tc.update(fact_tc or {})
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
    _normalise_resin_system(fact)
    _normalise_substrate(fact)
    _normalise_test_standard(fact)
    _normalise_process_steps(fact)


def _normalise_resin_system(fact: dict[str, Any]) -> None:
    value = fact.get("resin_system")
    if not isinstance(value, list):
        return
    cleaned = [str(item).strip() for item in value if str(item or "").strip()]
    if not cleaned:
        fact.pop("resin_system", None)
        return
    fact["resin_system"] = cleaned[0]
    additives = [
        str(item).strip()
        for item in (fact.get("additives") or [])
        if str(item or "").strip()
    ]
    for item in cleaned[1:]:
        if item not in additives:
            additives.append(item)
    if additives:
        fact["additives"] = additives
    _record_schema_coercion(
        fact,
        "resin_system",
        "list_first_item_to_resin_system_rest_to_additives",
    )


def _record_schema_coercion(fact: dict[str, Any], field: str, action: str) -> None:
    coercions = fact.get("schema_coercions")
    if not isinstance(coercions, list):
        coercions = []
        fact["schema_coercions"] = coercions
    row = {"field": field, "action": action}
    if row not in coercions:
        coercions.append(row)


def _normalise_substrate(fact: dict[str, Any]) -> None:
    value = fact.get("substrate")
    if value in (None, "", {}, [], "unknown"):
        if value in ("", {}, [], "unknown"):
            fact.pop("substrate", None)
        return
    if isinstance(value, str):
        fact["substrate"] = {"tested": value, "claimed": []}
        _record_schema_coercion(fact, "substrate", "string_to_tested_substrate")
        return
    if isinstance(value, dict):
        if isinstance(value.get("claimed"), str):
            value = dict(value)
            value["claimed"] = [value["claimed"]]
            fact["substrate"] = value
            _record_schema_coercion(fact, "substrate", "claimed_string_to_list")
        return


def _normalise_test_standard(fact: dict[str, Any]) -> None:
    """Preserve standalone standard fields in the v1-compatible condition slot."""
    standard = (
        fact.pop("test_standard", None)
        or fact.pop("test_standard_id", None)
        or fact.pop("standard_id", None)
    )
    method_standard = _standard_from_test_method(fact.get("test_method"))
    if method_standard and not standard:
        standard = method_standard
    if method_standard and _is_standard_only_test_method(fact.get("test_method")):
        fact.pop("test_method", None)
    condition = fact.get("test_condition")
    if condition not in (None, "", [], {}, "unknown") and not isinstance(condition, dict):
        condition = {"description": str(condition)}
    elif not isinstance(condition, dict):
        condition = {}
    if standard and not condition.get("standard_id"):
        condition["standard_id"] = str(standard)
    if condition:
        fact["test_condition"] = condition
    elif "test_condition" in fact:
        fact.pop("test_condition", None)


_TEST_METHOD_STANDARD_RE = re.compile(r"(?:^|_)((?:ISO|ASTM|DIN|GB|JIS))_?([A-Z]?\d+[A-Z0-9]*)", re.I)
_SUBSTRATE_SIGNAL_RE = re.compile(
    r"\b("
    r"substrate|panel|steel|metal|alumini?um|iron phosphate|aged polyurethane|"
    r"polyurethane panel|wood|plywood|glass|plastic|polycarbonate|polypropylene"
    r")\b",
    re.I,
)
_PROCESS_SIGNAL_RE = re.compile(
    r"\b("
    r"thermal(?:ly)? cured?|ambient cured?|uv cured?|curing|baked?|drawn down|"
    r"drawdown|spray(?:ed|ing)?|laminat(?:ed|ion)|electrodeposition|ced|"
    r"printing|jetting|nir|mixed|mixing|dispers(?:ed|ion)"
    r")\b",
    re.I,
)
_STANDARD_SIGNAL_RE = re.compile(r"\b(?:ASTM|ISO|DIN|JIS)\s*[A-Z]?\s*\d+|\bGB\s*/?\s*T\s*\d+", re.I)


def _standard_from_test_method(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _TEST_METHOD_STANDARD_RE.search(value)
    if not match:
        return None
    return f"{match.group(1).upper()} {match.group(2).upper()}"


def _is_standard_only_test_method(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    body = value.removeprefix("TEST_")
    return bool(re.match(r"^(ISO|ASTM|DIN|GB|JIS)_?[A-Z]?\d+[A-Z0-9]*$", body, re.I))


def _normalise_process_steps(fact: dict[str, Any]) -> None:
    """Write process steps using canonical_id while tolerating legacy step."""
    steps = fact.get("process")
    if steps in (None, "", [], {}, "unknown"):
        if steps in ("", {}, "unknown"):
            fact.pop("process", None)
        return
    if not isinstance(steps, list):
        steps = [steps]
        _record_schema_coercion(fact, "process", "scalar_or_dict_to_process_list")
    normalised: list[dict[str, Any]] = []
    for raw in steps:
        if isinstance(raw, str):
            canonical = raw.strip()
            if canonical:
                normalised.append({"canonical_id": canonical})
            continue
        if not isinstance(raw, dict):
            continue
        canonical = raw.get("canonical_id") or raw.get("step") or raw.get("process")
        if not canonical:
            continue
        row = dict(raw)
        row["canonical_id"] = canonical
        row.pop("step", None)
        row.pop("process", None)
        normalised.append(row)
    if normalised:
        fact["process"] = normalised
    else:
        fact.pop("process", None)


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
