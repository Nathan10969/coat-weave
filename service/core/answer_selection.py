"""Source-bound answer sections; display limits never prune input evidence."""
from __future__ import annotations

import json
import re
from collections import OrderedDict

import evidence_packet as delivery
from demo_text import normalize_doc_id


def catalog(packet: dict, covered_objects: list[str] | None = None) -> dict:
    groups = OrderedDict()
    allowed = None if covered_objects is None else set(covered_objects)
    scope = packet.get("active_scope") or {}
    scope_ids = {value for doc in scope.get("doc_ids", []) if (value := normalize_doc_id(doc))} if scope.get("policy") == "hard" else None
    adjacent, exact, unmet, excluded, related = set(), set(), {}, set(), set()
    for observation in packet.get("tool_observations", []):
        result = observation.get("result") or {}
        if observation.get("tool") == "kg.expand_hyperedge_multihop":
            for warning in (result.get("semantic_guard") or {}).get("item_warnings", []):
                oid = str(warning.get("object_id"))
                if warning.get("facet_status") == "excluded":
                    excluded.add(oid)
                else:
                    related.add(oid)
                    unmet[oid] = [warning.get("reason") or "semantic_constraint_not_satisfied"]
        if observation.get("tool") != "kg.hybrid_search":
            continue
        exact.update(str(row.get("object_id")) for row in result.get("exact_items", result.get("items", [])))
        for entry in result.get("adjacent_items", []):
            candidate = entry.get("item") or entry
            adjacent.add(str(candidate.get("object_id")))
            unmet[str(candidate.get("object_id"))] = entry.get("unmet_constraints") or list(result.get("adjacent_relaxed_filters") or {})
    for row in delivery.expanded_items(packet):
        if str(row.get("object_id")) in excluded:
            continue
        if allowed is not None and row.get("object_id") not in allowed:
            continue
        if scope_ids is not None and normalize_doc_id(row.get("doc_id")) not in scope_ids:
            continue
        key = delivery.sample_key(row)
        if key[2] == "unresolved":
            continue
        groups.setdefault(key, []).append(row)
    result = {}
    for index, (key, rows) in enumerate(groups.items(), 1):
        result[f"S{index}"] = {
            "sample_key": list(key), "doc_id": rows[0]["doc_id"],
            "object_ids": list(dict.fromkeys(r["object_id"] for r in rows)),
            "facts_refs": list(dict.fromkeys(ref for r in rows for ref in r.get("facts_refs", []))),
            "evidence_refs": list(dict.fromkeys(ref for r in rows for ref in r.get("evidence_refs", []))),
            "match_scope": "adjacent" if any(str(r.get("object_id")) in (adjacent - exact) | related for r in rows) else "expanded",
            "unmet_constraints": list(dict.fromkeys(str(value) for r in rows for value in unmet.get(str(r.get("object_id")), []))),
        }
    return result


def instructions(limit: int) -> str:
    return (
        '\nThis call ONLY selects display samples, not the final customer answer. '
        'Return JSON only: {"selected_handles":["S1","S2","S3"],'
        '"coverage_note":"Explain your selection briefly"}. '
        f"Select {limit} distinct sample handles from answer_selection_catalog, prioritizing relevance; "
        "if fewer eligible handles exist, select all available handles. This is a display goal, not permission to broaden retrieval. "
        "All supplied evidence remains available; do not confuse input coverage with display count. "
        "Use the full evidence to rank relevance. The server will render original source fields and bound references. "
        "Do not claim a complete formulation from performance-only records. Do not mention other samples or patents "
        "inside a selected sample. Do not invent citations or numerical values. No cross-sample comparisons. "
        "If showing fewer samples than requested or available, explain why in coverage_note. "
        "Do not reproduce all reference IDs merely to prove you read them; cite the actual supporting records."
    )


def attach_source_views(choices: dict, prepared: dict) -> dict:
    """Build the customer view from complete source fields, not model paraphrases."""
    by_id = {row["object_id"]: row for row in delivery.expanded_items(prepared)}
    views = {}
    for handle, sample in choices.items():
        records = []
        for oid in sample["object_ids"]:
            row = by_id[oid]
            records.append({"object_id": oid, "summary": row.get("hyperedge_summary") or {},
                            "raw": row.get("hyperedge_raw") or {}})
        views[handle] = {"records": records, "facts": [], "evidence": []}
        for kind in ("facts", "evidence"):
            views[handle][kind] = [prepared.get("source_records", {}).get(kind, {})[ref]
                                   for ref in sample[kind + "_refs"]]
    return views


def _cell(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text.replace("|", "\\|").replace("\n", "<br>")


def render_source_view(view: dict) -> list[str]:
    lines = ["以下为已抽取的原始组分及测试记录，不宣称覆盖专利完整配方表。方法与结果按原记录分别列出，不额外推断关联。"]
    seen_materials = set()
    material_rows = []
    for entry in view["records"]:
        for material in entry["summary"].get("materials") or []:
            key = json.dumps(material, ensure_ascii=False, sort_keys=True)
            if key in seen_materials:
                continue
            seen_materials.add(key)
            material_rows.append(material)
    if material_rows:
        lines.append("已抽取材料槽位（可能不全；完整原文片段另列，未自动补读表格）：")
        lines += ["| 组分 | 用量（原值） | 角色 | 其他原始字段 |", "|---|---|---|---|"]
        for row in material_rows:
            if not isinstance(row, dict):
                lines.append(f"| {_cell(row)} | 未标注 | 未标注 | |")
                continue
            amount = row.get("amount", {k: row[k] for k in ("value", "unit", "value_text") if k in row})
            extra = {k: v for k, v in row.items() if k not in {"material", "amount", "role", "value", "unit", "value_text"}}
            lines.append(f"| {_cell(row.get('material', '未标注'))} | {_cell(amount)} | {_cell(row.get('role', '未标注'))} | {_cell(extra)} |")
    else:
        lines.append("没有可直接列成组分表的材料槽位；不能将性能记录称为完整配方。")
    for entry in view["records"]:
        lines.append("原始记录：" + entry["object_id"])
        # Preserve original field relationships; don't join a test from another HE.
        fields = {k: v for k, v in entry["summary"].items()
                  if k in {"property", "property_canonical_id", "test_method", "test_standard", "test_condition", "result", "process", "substrate", "baseline", "example_kind", "formulation"}
                  and v not in (None, "", [], {})}
        lines += ["| 原始字段 | 内容 |", "|---|---|"]
        lines.extend(f"| {_cell(key)} | {_cell(value)} |" for key, value in fields.items())
    if view["facts"]:
        lines.append("关联事实原始记录（保留原始样品、测试及引用关系；上下文补充不等于该样品的直接事实）：")
        lines += ["| 事实 ID | 组分或性能 | 原值及单位 | 上下文 | 证据 ID | 其他内容 |", "|---|---|---|---|---|---|"]
        for fact in view["facts"]:
            if not isinstance(fact, dict):
                continue
            displayed = {"fact_id", "material", "property", "value", "value_text", "unit", "context_id", "evidence_id", "evidence_ids"}
            # Suppress transport/QA decoration only in display, never in model input.
            extra = {k: v for k, v in fact.items() if k not in displayed | {
                "doc_id", "schema_version", "extraction_confidence", "qa_flags", "material_canonical_id"}
                and v not in (None, "", [], {})}
            values = {k: fact[k] for k in ("value", "value_text", "unit") if fact.get(k) is not None}
            cells = [fact.get("fact_id"), fact.get("material") or fact.get("property"), values,
                     fact.get("context_id"), fact.get("evidence_ids") or fact.get("evidence_id"), extra]
            lines.append("| " + " | ".join(_cell(value) for value in cells) + " |")
    for evidence in view["evidence"]:
        if not isinstance(evidence, dict):
            continue
        identifier = evidence.get("evidence_id") or evidence.get("id") or "未标注"
        location = {k: evidence[k] for k in ("page", "section", "table", "row_label") if k in evidence}
        lines.append(f"证据 {identifier}：{json.dumps(location, ensure_ascii=False)}")
        quote = evidence.get("quote")
        if quote:
            lines.append("\n".join("> " + line for line in str(quote).splitlines()))
    return lines


def validate(text: str, choices: dict, limit: int) -> dict:
    payload = json.loads(text)
    if isinstance(payload, dict) and set(payload) <= {"selected_handles", "coverage_note"}:
        handles = payload.get("selected_handles")
        if not isinstance(handles, list) or any(not isinstance(h, str) or h not in choices for h in handles):
            raise ValueError("answer_sample_identity")
        payload = {"coverage_note": payload.get("coverage_note", ""), "selected_samples": [
            {"sample_handle": h, "claims": [{"text": "Selected source sample",
                "facts_refs": choices[h]["facts_refs"], "evidence_refs": choices[h]["evidence_refs"]}]} for h in handles]}
    if not isinstance(payload, dict) or set(payload) - {"selected_samples", "coverage_note"}:
        raise ValueError("answer_unexpected_fields")
    rows = payload.get("selected_samples")
    if not isinstance(rows, list) or not rows or len(rows) != min(limit, len(choices)):
        actual = len(rows) if isinstance(rows, list) else "not_list"
        raise ValueError(f"answer_display_count:expected={min(limit, len(choices))}:actual={actual}")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"sample_handle", "claims"}:
            raise ValueError("answer_sample_shape")
        handle = row.get("sample_handle")
        if not isinstance(handle, str) or handle not in choices or handle in seen:
            raise ValueError("answer_sample_identity")
        seen.add(handle)
        sample = choices[handle]
        claims = row.get("claims")
        if not isinstance(claims, list) or not claims:
            raise ValueError("answer_empty_sample")
        for claim in claims:
            if not isinstance(claim, dict) or set(claim) != {"text", "facts_refs", "evidence_refs"}:
                raise ValueError("answer_claim_shape")
            if not isinstance(claim.get("text"), str) or not claim["text"].strip():
                raise ValueError("answer_empty_claim")
            for kind in ("facts", "evidence"):
                refs = claim.get(kind + "_refs")
                if not isinstance(refs, list) or any(not isinstance(r, str) or r not in sample[kind + "_refs"] for r in refs):
                    raise ValueError("answer_unbound_reference")
            if not claim["evidence_refs"]:
                raise ValueError("answer_missing_evidence")
            # Patent identifiers are deterministic syntax, not semantic routing.
            mentioned = re.findall(r"\b(?:WO|US|EP|CN|JP|AU|CA)\d{6,}[A-C]\d?\b", claim["text"], re.I)
            if any(not sample["doc_id"].upper().endswith(doc.upper()) for doc in mentioned):
                raise ValueError("answer_cross_patent_claim")
    note = payload.get("coverage_note", "")
    if not isinstance(note, str):
        raise ValueError("answer_coverage_note_shape")
    if len(rows) < min(limit, len(choices)) and not note.strip():
        raise ValueError("answer_missing_coverage_explanation")
    return payload


def render(payload: dict, choices: dict, source_views: dict | None = None) -> str:
    lines = []
    for row in payload["selected_samples"]:
        sample = choices[row["sample_handle"]]
        lines.append(f"### {sample['doc_id']} / {sample['sample_key'][-1]}")
        lines.append("样品标识：" + _cell(sample["doc_id"]) + " / " + _cell(sample["sample_key"][-1]))
        if sample["match_scope"] == "adjacent":
            lines.append("相邻/近似证据（未计入精确结果）。")
            lines.append("未满足的条件：" + _cell(sample["unmet_constraints"]))
        if source_views is not None:
            lines.extend(render_source_view(source_views[row["sample_handle"]]))
        else:
            # No raw source view means only an inventory may be rendered.
            lines.append("原始字段视图未提供，仅列出已展开样品与引用。")
        refs = list(dict.fromkeys(ref for c in row["claims"] for ref in c["evidence_refs"]))
        labels = [str(json.loads(ref)[-1]) for ref in refs]
        lines.append("证据引用：" + "、".join(labels))
    # Coverage prose is not rendered as evidence; coverage counts are authoritative.
    lines.append(f"本轮展示 {len(payload['selected_samples'])} 个不同样品；可用样品 {len(choices)} 个。")
    if len(payload["selected_samples"]) < len(choices):
        lines.append("其余样品未在本次回答中逐项展示；这不是全部配方清单或系统性比较。")
    return "\n\n".join(lines)


def fallback(choices: dict, limit: int) -> str:
    lines = ["本轮回答未通过样品及引用校验。以下仅列出已成功展开的样品，不将未验证内容作为配方结论："]
    for sample in list(choices.values())[:limit]:
        lines.append(f"- {sample['doc_id']} / {sample['sample_key'][-1]}")
    if not choices:
        lines.append("没有身份完整且已展开的样品可供展示。")
    return "\n".join(lines)
