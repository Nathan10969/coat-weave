from __future__ import annotations

import json
import time
from typing import Any

from demo_config import (
    KG_HYBRID_DEFAULT_CANDIDATE_K,
    KG_HYBRID_DEFAULT_TOP_K,
    SESSION_ID,
    TOOL_OBSERVATIONS,
)
from demo_storage import append_jsonl, now_iso
from demo_text import (
    extract_doc_ids,
    int_or_default,
    normalize_doc_id,
    normalize_kg_aggregate_filters,
    normalize_kg_search_filters,
    normalize_object_ids,
    normalize_string_list,
    stable_id,
)
from graph_store import graph_edge, graph_node, upsert_graph_records
from routing import (
    APPLICATION_FAMILY_EXACT_MATCH_EXEMPT,
    CORROSION_EXPANSION_TERMS,
    analyze_kg_query_semantics,
    analyze_query_facets,
    build_kg_search_queries,
    infer_aggregate_intent,
    infer_aggregate_target,
    load_kg_filter_vocab,
    route_tools,
)
from scope_state import _app_value
from tool_clients import (
    kg_doc_field_scan,
    kg_expand_hyperedge_multihop,
    kg_hybrid_search,
    kg_sql_aggregate,
)


def record_tool_observation(
    question: str,
    result: dict[str, Any],
    *,
    error: str | None = None,
    tool: str | None = None,
    turn_id: str | None = None,
    route_id: str | None = None,
) -> dict[str, Any]:
    tool_name = tool or result.get("tool") or "external.tool"
    obs_id = f"tool_{int(time.time() * 1000)}_{stable_id('obs', tool_name)[4:]}"
    # Surface "empty" instead of flattening it to "ok" — an empty aggregate is
    # not a successful count, and downstream must be able to tell the
    # difference. Other tool statuses stay mapped to "ok" so the observation
    # status enum remains {ok, empty, error} for prompt rules and the API.
    status = "error" if error else ("empty" if result.get("status") == "empty" else "ok")
    row = {
        "id": obs_id,
        "session_id": _app_value("SESSION_ID", SESSION_ID),
        "turn_id": turn_id,
        "route_id": route_id,
        "question_hash": stable_id("question", question),
        "tool": tool_name,
        "status": status,
        "question": question,
        "result": result,
        "error": error,
        "created_at": now_iso(),
    }
    append_jsonl(_app_value("TOOL_OBSERVATIONS", TOOL_OBSERVATIONS), row)

    nodes = [
        graph_node(tool_name, "tool", obs_id, "tool_observation"),
    ]
    edges = []
    if tool_name == "kg.hybrid_search":
        query_target = f"Coating KG hybrid search: {result.get('query', question)[:60]}"
        nodes.append(graph_node(query_target, "search_result", obs_id, "tool_observation"))
        edges.append(
            graph_edge(
                tool_name,
                "returns",
                query_target,
                obs_id,
                "tool_observation",
                "kg_hybrid_search_ranked_candidates",
            )
        )
        for item in (result or {}).get("items", [])[:5]:
            object_id = item.get("object_id") or item.get("hyperedge_id")
            if not object_id:
                continue
            candidate_name = f"candidate {object_id}"
            candidate_node = graph_node(candidate_name, "hyperedge_candidate", obs_id, "tool_observation")
            candidate_node["object_id"] = object_id
            candidate_node["doc_id"] = item.get("doc_id")
            candidate_node["rank"] = item.get("rank")
            candidate_node["score"] = item.get("score")
            candidate_node["score_parts"] = item.get("score_parts") or {}
            candidate_node["channels"] = item.get("channels") or []
            candidate_node["metadata"] = item.get("metadata") or {}
            candidate_node["text_preview"] = item.get("text_preview")
            nodes.append(candidate_node)
            edges.append(
                graph_edge(
                    query_target,
                    "found",
                    candidate_name,
                    obs_id,
                    "tool_observation",
                    "kg_hybrid_search_candidate",
                )
            )
    if tool_name == "kg.expand_hyperedge_multihop":
        nodes.append(graph_node("Coating KG multihop evidence pack", "tool_result", obs_id, "tool_observation"))
        edges.append(
            graph_edge(
                tool_name,
                "returns",
                "Coating KG multihop evidence pack",
                obs_id,
                "tool_observation",
                "kg_expand_compact_pack",
            )
        )
        for item in (result or {}).get("items", [])[:5]:
            object_id = item.get("object_id") or "unknown_object_id"
            hyperedge_id = item.get("hyperedge_id") or object_id
            doc_id = item.get("doc_id")
            hyperedge_name = f"hyperedge {hyperedge_id}"
            hyperedge_node = graph_node(hyperedge_name, "hyperedge", obs_id, "tool_observation")
            hyperedge_node["object_id"] = object_id
            hyperedge_node["doc_id"] = doc_id
            hyperedge_node["summary"] = item.get("hyperedge_summary") or {}
            hyperedge_node["unresolved"] = item.get("unresolved") or {}
            nodes.append(hyperedge_node)
            edges.append(
                graph_edge(
                    "Coating KG multihop evidence pack",
                    "expands",
                    hyperedge_name,
                    obs_id,
                    "tool_observation",
                    "kg_expand_object_id",
                )
            )
            if doc_id:
                doc_name = f"patent {doc_id}"
                patent_node = graph_node(doc_name, "patent", obs_id, "tool_observation")
                patent_node["patent"] = item.get("patent") or {}
                nodes.append(patent_node)
                edges.append(
                    graph_edge(
                        hyperedge_name,
                        "belongs_to",
                        doc_name,
                        obs_id,
                        "tool_observation",
                        "kg_expand_patent_context",
                    )
                )
            for evidence in (item.get("evidence") or [])[:5]:
                evidence_id = evidence.get("evidence_id") or "unknown_evidence"
                page = evidence.get("page")
                table = evidence.get("table") or evidence.get("section") or ""
                evidence_name = f"evidence {evidence_id} p{page} {table}".strip()
                evidence_node = graph_node(evidence_name, "evidence", obs_id, "tool_observation")
                evidence_node["evidence"] = {
                    "evidence_id": evidence_id,
                    "page": page,
                    "section": evidence.get("section"),
                    "table": evidence.get("table"),
                    "row_label": evidence.get("row_label"),
                    "quote": evidence.get("quote"),
                    "source_image": evidence.get("source_image"),
                }
                nodes.append(evidence_node)
                edges.append(
                    graph_edge(
                        hyperedge_name,
                        "supported_by",
                        evidence_name,
                        obs_id,
                        "tool_observation",
                        "kg_expand_evidence_trace",
                    )
                )
    if tool_name == "kg.sql_aggregate":
        interpretation = result.get("query_interpretation") if isinstance(result, dict) else {}
        target = (interpretation or {}).get("target") or "aggregate"
        aggregate_name = f"Coating KG aggregate: {target}"
        nodes.append(graph_node(aggregate_name, "aggregate_result", obs_id, "tool_observation"))
        edges.append(
            graph_edge(
                tool_name,
                "returns",
                aggregate_name,
                obs_id,
                "tool_observation",
                "kg_sql_aggregate_result",
            )
        )
        for item in (result or {}).get("items", [])[:8]:
            value = item.get("value") or item.get("unit")
            if value is None:
                continue
            value_name = f"{target}: {value}"
            value_node = graph_node(value_name[:120], "aggregate_value", obs_id, "tool_observation")
            value_node["count"] = item.get("count")
            value_node["doc_count"] = item.get("doc_count")
            value_node["assignee_count"] = item.get("assignee_count")
            value_node["assignees"] = item.get("assignees") or []
            value_node["canonical_id"] = item.get("canonical_id")
            value_node["examples"] = item.get("examples") or []
            nodes.append(value_node)
            edges.append(
                graph_edge(
                    aggregate_name,
                    "has_bucket",
                    value_name[:120],
                    obs_id,
                    "tool_observation",
                    "kg_sql_aggregate_bucket",
                )
            )
    if tool_name == "kg.doc_field_scan":
        doc_ids = result.get("doc_ids") or []
        scan_name = "Coating KG doc field scan"
        if doc_ids:
            scan_name = f"Coating KG doc field scan: {', '.join(str(doc_id) for doc_id in doc_ids[:3])}"
        nodes.append(graph_node(scan_name, "tool_result", obs_id, "tool_observation"))
        edges.append(
            graph_edge(
                tool_name,
                "returns",
                scan_name,
                obs_id,
                "tool_observation",
                "kg_doc_field_scan_result",
            )
        )
        for item in (result or {}).get("items", [])[:8]:
            if not isinstance(item, dict):
                continue
            object_id = str(item.get("object_id") or item.get("hyperedge_id") or "").strip()
            if not object_id:
                continue
            nodes.append(graph_node(object_id, "hyperedge", obs_id, "tool_observation"))
            edges.append(
                graph_edge(
                    scan_name,
                    "matched",
                    object_id,
                    obs_id,
                    "tool_observation",
                    "kg_doc_field_scan_item",
                )
            )
    upsert_graph_records(nodes, edges)
    return row

def semantic_text_from_expand_item(item: dict[str, Any]) -> str:
    pieces = [
        str(item.get("object_id") or ""),
        str(item.get("doc_id") or ""),
        str(item.get("hyperedge_id") or ""),
        json.dumps(item.get("hyperedge_summary") or {}, ensure_ascii=False),
        json.dumps(item.get("facts") or [], ensure_ascii=False),
        json.dumps(item.get("evidence") or [], ensure_ascii=False),
        json.dumps(item.get("patent") or {}, ensure_ascii=False),
    ]
    return " ".join(pieces).lower()

def _compact_semantic_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).lower() if isinstance(value, (dict, list)) else str(value or "").lower()

def _item_channels(item: dict[str, Any]) -> set[str]:
    channels = item.get("channels")
    if isinstance(channels, list):
        return {str(channel).strip() for channel in channels if str(channel).strip()}
    return set()

def _slot_text_from_item(item: dict[str, Any], slot_keys: tuple[str, ...]) -> str:
    pieces: list[str] = []
    summary = item.get("hyperedge_summary") if isinstance(item.get("hyperedge_summary"), dict) else {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    for container in (item, summary, metadata):
        for key in slot_keys:
            if isinstance(container, dict) and key in container:
                pieces.append(_compact_semantic_text(container.get(key)))
    for fact in item.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        for key in slot_keys:
            if key in fact:
                pieces.append(_compact_semantic_text(fact.get(key)))
    return " ".join(pieces)

def _contains_any_text(text: str, terms: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms)

CFRP_TERMS = (
    "cfrp",
    "carbon fiber reinforced",
    "carbon fibre reinforced",
    "carbon-fiber reinforced",
    "carbon-fibre reinforced",
    "carbon fiber composite",
    "carbon fibre composite",
    "碳纤维复合",
    "碳纤维增强",
)
CARBON_FIBER_TERMS = ("carbon fiber", "carbon fibre", "碳纤维")
ZINC_MATERIAL_TERMS = (
    "zinc powder",
    "zinc dust",
    "zinc pigment",
    "zinc metal",
    "metallic zinc",
    "锌粉",
    "富锌",
)
EPOXY_TERMS = ("epoxy", "epoxide", "环氧")
PRIMER_TERMS = ("primer", "底漆", "底涂")
ANTIFOULING_CONFLICT_TERMS = ("antifouling", "foul-release", "foul release", "防污")

def _has_cfrp_substrate_slot(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    matched = metadata.get("matched_substrate_facts")
    if isinstance(matched, list) and matched:
        return True
    if "substrate_fact" in _item_channels(item):
        return True
    substrate_text = _slot_text_from_item(item, ("substrate", "substrates", "substrate_canonical_id"))
    if _contains_any_text(substrate_text, CFRP_TERMS):
        return True
    evidence_text = " ".join(_compact_semantic_text(row) for row in item.get("evidence") or [])
    return _contains_any_text(evidence_text, CFRP_TERMS) and _contains_any_text(evidence_text, ("substrate", "基底", "基材"))

def _has_carbon_fiber_material_not_substrate(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    matched_materials = metadata.get("matched_material_facts")
    if isinstance(matched_materials, list) and matched_materials:
        mat_text = _compact_semantic_text(matched_materials)
        if _contains_any_text(mat_text, CARBON_FIBER_TERMS):
            return True
    material_text = _slot_text_from_item(
        item,
        ("material", "materials", "pigments", "fillers", "additives", "resin", "binder"),
    )
    return _contains_any_text(material_text, CARBON_FIBER_TERMS)

def _item_has_zinc_material(item: dict[str, Any]) -> bool:
    haystack = semantic_text_from_expand_item(item)
    return _contains_any_text(haystack, ZINC_MATERIAL_TERMS)

def _item_has_epoxy_or_primer_system(item: dict[str, Any]) -> bool:
    haystack = semantic_text_from_expand_item(item)
    return _contains_any_text(haystack, EPOXY_TERMS) and _contains_any_text(haystack, PRIMER_TERMS)

def _evaluate_hard_facet_item(question: str, item: dict[str, Any]) -> tuple[str, str | None]:
    hard = analyze_query_facets(question).get("hard", {})
    if hard.get("substrates"):
        if _has_cfrp_substrate_slot(item):
            return "direct_match", None
        if _has_carbon_fiber_material_not_substrate(item):
            return "related_match", "carbon_fiber_material_not_substrate"
        return "excluded", "missing_cfrp_substrate"
    if hard.get("materials") and hard.get("coating_types") and hard.get("systems"):
        haystack = semantic_text_from_expand_item(item)
        if _contains_any_text(haystack, ANTIFOULING_CONFLICT_TERMS):
            return "excluded", "missing_zinc_material" if not _item_has_zinc_material(item) else "missing_epoxy_or_primer_system"
        if not _item_has_zinc_material(item):
            return "excluded", "missing_zinc_material"
        if not _item_has_epoxy_or_primer_system(item):
            return "excluded", "missing_epoxy_or_primer_system"
        return "direct_match", None
    return "direct_match", None

def semantic_guard_for_expand_result(question: str, expand_result: dict[str, Any]) -> dict[str, Any]:
    semantics = analyze_kg_query_semantics(question)
    required_terms = [str(term) for term in semantics.get("required_terms", []) if str(term).strip()]
    facets = semantics.get("query_facets") if isinstance(semantics.get("query_facets"), dict) else {}
    hard_facets = facets.get("hard") if isinstance(facets.get("hard"), dict) else {}
    has_hard_facets = any(bool(values) for values in hard_facets.values())
    guard = {
        "enabled": bool(semantics.get("guard")),
        "operator": semantics.get("operator"),
        "concepts": semantics.get("concepts", []),
        "outcomes": semantics.get("outcomes", []),
        "required_terms": required_terms,
        "query_facets": facets,
        "direct_match": True,
        "related_match": False,
        "item_warnings": [],
    }
    if not guard["enabled"]:
        return guard
    items = [item for item in (expand_result or {}).get("items", []) if isinstance(item, dict)]
    direct_items = 0
    related_items = 0
    warnings: list[dict[str, Any]] = []
    for item in items:
        if has_hard_facets:
            facet_status, reason = _evaluate_hard_facet_item(question, item)
            if facet_status == "direct_match":
                direct_items += 1
                continue
            if facet_status == "related_match":
                related_items += 1
            warnings.append(
                {
                    "object_id": item.get("object_id") or item.get("hyperedge_id"),
                    "doc_id": item.get("doc_id"),
                    "facet_status": facet_status,
                    "reason": reason or "hard_facet_not_satisfied",
                    "query_facets": hard_facets,
                }
            )
            continue
        haystack = semantic_text_from_expand_item(item)
        matched = [term for term in required_terms if term.lower() in haystack]
        if matched:
            direct_items += 1
            continue
        warnings.append(
            {
                "object_id": item.get("object_id") or item.get("hyperedge_id"),
                "doc_id": item.get("doc_id"),
                "reason": "missing_required_semantic_terms",
                "required_terms": required_terms[:8],
            }
        )
    guard["item_warnings"] = warnings
    guard["direct_match"] = bool(items) and direct_items > 0
    guard["related_match"] = related_items > 0
    return guard

def kg_expand_selection_score(item: dict[str, Any], question: str) -> float:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    haystack = " ".join(
        [
            str(item.get("object_id") or ""),
            str(item.get("doc_id") or ""),
            str(item.get("text_preview") or ""),
            json.dumps(metadata, ensure_ascii=False),
        ]
    ).lower()
    score = 0.0
    constraints: list[tuple[bool, tuple[str, ...], float]] = [
        ("电泳" in question or "electrodeposit" in question.lower(), ("electrodeposit", "electrocoat"), 5.0),
        ("冷轧钢" in question or "cold rolled" in question.lower(), ("cold rolled steel",), 5.0),
        ("富锌" in question or "锌" in question or "zinc" in question.lower(), ("zinc",), 2.0),
        ("盐雾" in question or "salt spray" in question.lower(), ("salt spray", "salt_fog", "astm b117"), 4.0),
        ("防腐" in question or "腐蚀" in question or "corrosion" in question.lower(), ("galvanic", "corrosion"), 3.0),
    ]
    for active, terms, weight in constraints:
        if not active:
            continue
        if any(term in haystack for term in terms):
            score += weight
        else:
            score -= min(weight, 2.0)
    semantics = analyze_kg_query_semantics(question)
    if semantics.get("guard"):
        required_terms = [str(term).lower() for term in semantics.get("required_terms", []) if str(term).strip()]
        matched_required = [term for term in required_terms if term in haystack]
        if matched_required:
            score += 8.0 + min(len(matched_required), 4)
        else:
            score -= 8.0
        outcome_terms = [term.lower() for term in CORROSION_EXPANSION_TERMS]
        if any(term in haystack for term in outcome_terms):
            score += 2.0
        else:
            score -= 1.0
    hard_facets = analyze_query_facets(question).get("hard", {})
    channels = _item_channels(item)
    if hard_facets.get("substrates"):
        if "substrate_fact" in channels or metadata.get("matched_substrate_facts"):
            score += 18.0
        elif _contains_any_text(haystack, CFRP_TERMS) and _contains_any_text(haystack, ("substrate", "基底", "基材")):
            score += 10.0
        elif "material_fact" in channels and _contains_any_text(haystack, CARBON_FIBER_TERMS):
            score -= 14.0
        else:
            score -= 6.0
    if hard_facets.get("materials") and hard_facets.get("systems") and hard_facets.get("coating_types"):
        has_zinc = _contains_any_text(haystack, ZINC_MATERIAL_TERMS)
        has_epoxy = _contains_any_text(haystack, EPOXY_TERMS)
        has_primer = _contains_any_text(haystack, PRIMER_TERMS)
        if has_zinc and has_epoxy and has_primer:
            score += 18.0
        else:
            if not has_zinc:
                score -= 10.0
            if not (has_epoxy and has_primer):
                score -= 10.0
        if _contains_any_text(haystack, ANTIFOULING_CONFLICT_TERMS):
            score -= 12.0
    if hard_facets.get("numeric_constraints"):
        if "numeric_fact" in channels or metadata.get("matched_numeric_facts"):
            score += 16.0
        else:
            score -= 5.0
    return score

def select_expand_object_ids(search_result: dict[str, Any], limit: int, question: str = "") -> list[str]:
    if limit <= 0:
        return []
    out: list[str] = []
    seen: set[str] = set()
    items = [item for item in (search_result or {}).get("items", []) if isinstance(item, dict)]
    target = min(limit, len(items))
    ranked_items = sorted(
        items,
        key=lambda item: (
            -kg_expand_selection_score(item, question),
            int_or_default(item.get("rank"), 999999),
            str(item.get("object_id") or ""),
        ),
    )
    per_doc_counts: dict[str, int] = {}
    distinct_docs = {item_doc_id(item) for item in ranked_items}
    distinct_docs.discard("")
    first_pass_doc_target = min(target, max(1, min(4, len(distinct_docs)))) if distinct_docs else 0

    def maybe_add(item: dict[str, Any], *, per_doc_limit: int | None = None) -> None:
        if len(out) >= target:
            return
        object_id = str(item.get("object_id") or "").strip() if isinstance(item, dict) else ""
        if not object_id or object_id in seen:
            return
        doc_id = item_doc_id(item)
        if per_doc_limit is not None and doc_id:
            if per_doc_counts.get(doc_id, 0) >= per_doc_limit:
                return
        seen.add(object_id)
        out.append(object_id)
        if doc_id:
            per_doc_counts[doc_id] = per_doc_counts.get(doc_id, 0) + 1

    if first_pass_doc_target:
        for item in ranked_items:
            doc_id = item_doc_id(item)
            if not doc_id or per_doc_counts.get(doc_id, 0):
                continue
            maybe_add(item, per_doc_limit=1)
            if len(out) >= first_pass_doc_target:
                break

    for item in ranked_items:
        maybe_add(item, per_doc_limit=2)
        if len(out) >= target:
            break
    for item in ranked_items:
        maybe_add(item)
        if len(out) >= target:
            break
    return out

def item_doc_id(item: dict[str, Any]) -> str:
    raw_doc_id = str(item.get("doc_id") or "").strip()
    doc_id = normalize_doc_id(raw_doc_id)
    if doc_id:
        return doc_id
    if raw_doc_id:
        return raw_doc_id
    object_ids = extract_doc_ids(str(item.get("object_id") or item.get("hyperedge_id") or ""))
    return object_ids[0] if object_ids else ""

def filter_search_result_by_doc_scope(search_result: dict[str, Any], filters: dict[str, Any] | None) -> dict[str, Any]:
    normalized_filters = normalize_kg_search_filters(filters)
    allowed_doc_ids = {normalize_doc_id(doc_id) for doc_id in normalized_filters.get("doc_ids", [])}
    allowed_doc_ids.discard("")
    if not allowed_doc_ids:
        return search_result
    items = [item for item in (search_result or {}).get("items", []) if isinstance(item, dict)]
    filtered_items = [item for item in items if item_doc_id(item) in allowed_doc_ids]
    if len(filtered_items) == len(items):
        return search_result
    scoped = dict(search_result or {})
    scoped["items"] = filtered_items
    scoped["status"] = scoped.get("status") if filtered_items else "empty"
    summary = dict(scoped.get("summary") or {})
    summary["scope_filtered_from"] = len(items)
    summary["scope_filtered_to"] = len(filtered_items)
    scoped["summary"] = summary
    warnings = list(scoped.get("warnings") or [])
    warnings.append(
        "local_scope_guard_removed_candidates_outside_doc_ids:"
        + ",".join(sorted(allowed_doc_ids))
    )
    scoped["warnings"] = warnings
    return scoped

def merge_kg_search_results(results: list[dict[str, Any]], query_variants: list[str]) -> dict[str, Any]:
    if not results:
        return {"tool": "kg.hybrid_search", "status": "empty", "items": [], "semantic_query_variants": query_variants}
    merged = dict(results[0])
    by_object_id: dict[str, dict[str, Any]] = {}
    for result_index, result in enumerate(results):
        for item in [item for item in (result or {}).get("items", []) if isinstance(item, dict)]:
            object_id = str(item.get("object_id") or "").strip()
            if not object_id:
                continue
            current = by_object_id.get(object_id)
            rank = int_or_default(item.get("rank"), 999999)
            if current is None:
                copied = dict(item)
                copied["rank"] = rank
                copied["matched_query_variants"] = [result_index]
                by_object_id[object_id] = copied
                continue
            current["rank"] = min(int_or_default(current.get("rank"), 999999), rank)
            current["matched_query_variants"] = sorted(
                set(current.get("matched_query_variants", [])) | {result_index}
            )
            try:
                current["score"] = max(float(current.get("score", 0) or 0), float(item.get("score", 0) or 0))
            except (TypeError, ValueError):
                pass
            channels = set(current.get("channels") or [])
            channels.update(item.get("channels") or [])
            if channels:
                current["channels"] = sorted(channels)
    items = sorted(
        by_object_id.values(),
        key=lambda item: (
            int_or_default(item.get("rank"), 999999),
            str(item.get("object_id") or ""),
        ),
    )
    merged["items"] = items
    merged["status"] = "ok" if items else "empty"
    merged["semantic_query_variants"] = query_variants
    summary = dict(merged.get("summary") or {})
    summary["query_variant_count"] = len(query_variants)
    summary["merged_returned"] = len(items)
    merged["summary"] = summary
    warnings: list[str] = []
    for result in results:
        warnings.extend([str(w) for w in (result.get("warnings") or [])])
    if warnings:
        merged["warnings"] = warnings
    return merged

def run_kg_hybrid_search_for_call(call: dict[str, Any], question: str, query: str) -> dict[str, Any]:
    # One expanded query defines one stable 100-item ranking pool. Fan-out
    # variants were merged only after pagination and could reintroduce objects
    # from page one on later pages.
    query_variants = build_kg_search_queries(query, question, max_variants=1)
    results = [
        _app_value("kg_hybrid_search", kg_hybrid_search)(
            variant,
            top_k=int_or_default(call.get("top_k"), KG_HYBRID_DEFAULT_TOP_K),
            candidate_k=int_or_default(
                call.get("candidate_k"),
                KG_HYBRID_DEFAULT_CANDIDATE_K,
            ),
            offset=max(0, int_or_default(call.get("offset"), 0)),
            filters=call.get("filters"),
        )
        for variant in query_variants
    ]
    if len(results) == 1:
        result = dict(results[0])
        result["semantic_query_variants"] = query_variants
        return result
    return merge_kg_search_results(results, query_variants)


DOC_FIELD_SCAN_NO_HYPEREDGES_WARNING = "structured_doc_scan_no_hyperedges"


def hybrid_fallback_call_from_doc_scan(
    call: dict[str, Any],
    question: str,
    *,
    doc_ids: list[str],
    reason: str,
) -> dict[str, Any]:
    query = str(call.get("query") or question).strip() or question
    filters = normalize_kg_search_filters(call.get("filters"))
    filters["doc_ids"] = doc_ids
    return {
        "tool": "kg.hybrid_search",
        "query": query,
        "top_k": int_or_default(call.get("top_k"), KG_HYBRID_DEFAULT_TOP_K),
        "candidate_k": int_or_default(call.get("candidate_k"), KG_HYBRID_DEFAULT_CANDIDATE_K),
        "filters": filters,
        "expand_top_k": 0,
        "reason": reason,
    }


def doc_field_scan_structured_empty(result: dict[str, Any]) -> bool:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return not result.get("items") and int_or_default(summary.get("hyperedges_scanned"), -1) == 0


def append_tool_warning(result: dict[str, Any], warning: str) -> None:
    warnings = normalize_string_list(result.get("warnings"))
    if warning not in warnings:
        warnings.append(warning)
    result["warnings"] = warnings


AGGREGATE_EMPTY_FILTER_MISS_WARNING = (
    "aggregate_filters_returned_empty: the applied filters matched nothing; "
    "this is a filter miss, not proof that the KG has no such records"
)
AGGREGATE_EMPTY_TRUE_ZERO_WARNING = (
    "aggregate_empty_under_vocabulary_valid_filters: all filter values are known "
    "vocabulary; treat the zero as a true negative for this exact scope"
)


def aggregate_filters_in_use(filters: dict[str, Any]) -> bool:
    return any(value for value in (filters or {}).values() if isinstance(value, list))


def retry_empty_aggregate(
    aggregate_client: Any,
    empty_result: dict[str, Any],
    *,
    intent: str,
    target: str,
    filters: dict[str, Any],
    group_by: list[str],
    limit: int,
    include_examples: bool,
) -> dict[str, Any]:
    """An empty aggregate under non-empty filters is a filter miss until proven
    otherwise (2026-06-12 trace: application_family=['optical fiber'] → 0 while
    the same terms matched 31 patents via substrates). Mirrors the
    doc_field_scan empty-fallback design, which sql_aggregate lacked.

    Only application_family values OUTSIDE the exported vocabulary are demoted
    to substrates — those can never match the backend's exact matcher, so the
    empty is provably a dimension mismatch. Vocabulary-valid values stay put:
    their empty intersection is a true negative and must not be rewritten into
    a different (union-ish) scope. Without a vocabulary file every value is
    treated as a potential miss (the incident path stays covered).

    The unfiltered total is fetched only when the result stays empty, so the
    answer can say "按口径未命中,全库共N" — a successful retry must not carry
    the all-KG number back into the answer context.
    """
    final = empty_result
    known = load_kg_filter_vocab().get("application_family") or set()
    family_values = list(filters.get("application_family") or [])
    if known:
        moved = [
            value
            for value in family_values
            if str(value).strip().lower() not in APPLICATION_FAMILY_EXACT_MATCH_EXEMPT
            and str(value).strip().lower() not in known
        ]
    else:
        moved = family_values
    if moved:
        retry_filters = dict(filters)
        retry_filters["application_family"] = [value for value in family_values if value not in moved]
        retry_filters["substrates"] = normalize_string_list(list(filters.get("substrates") or []) + moved)
        retried = aggregate_client(
            intent=intent,
            target=target,
            filters=retry_filters,
            group_by=group_by,
            limit=limit,
            include_examples=include_examples,
        )
        if retried.get("status") != "empty":
            retried["fallback_note"] = {
                "reason": "application_family values outside the vocabulary matched nothing; retried as substrates",
                "original_filters": filters,
            }
            append_tool_warning(retried, "application_family_filter_miss_retried_as_substrates")
            final = retried
    if final.get("status") == "empty":
        if moved or not known:
            append_tool_warning(final, AGGREGATE_EMPTY_FILTER_MISS_WARNING)
        else:
            append_tool_warning(final, AGGREGATE_EMPTY_TRUE_ZERO_WARNING)
        try:
            unfiltered = aggregate_client(
                intent=intent,
                target=target,
                filters=None,
                group_by=[],
                limit=1,
                include_examples=False,
            )
            total = (unfiltered.get("summary") or {}).get("distinct_count")
            if total is not None:
                final["unfiltered_total_distinct_count"] = total
        except Exception:  # noqa: BLE001 — the total is diagnostic context; never lose the primary result over it
            pass
    return final


def mark_doc_scan_no_structured_coverage(result: dict[str, Any]) -> None:
    result["coverage_status"] = "no_structured_hyperedges_for_doc_ids"
    result["coverage_note"] = (
        "docs_scanned only counts requested doc_ids; hyperedges_scanned=0 means "
        "the live structured KG did not provide evidence for those doc_ids and must not "
        "be treated as proof that the patent exists in the live KG."
    )


def maybe_run_tools(question: str, *, turn_id: str | None = None, route_id: str | None = None) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    route = _app_value("route_tools", route_tools)(question)
    route_id = route_id or f"route_{int(time.time() * 1000)}_{stable_id('route', question)[6:]}"
    for call in route.get("calls", []):
        tool = call.get("tool")
        query = call.get("query") or question
        fallback_hybrid_call: dict[str, Any] | None = None
        try:
            if tool == "kg.expand_hyperedge_multihop":
                object_ids = normalize_object_ids(call.get("object_ids"), query)
                if not object_ids:
                    continue
                result = _app_value("kg_expand_hyperedge_multihop", kg_expand_hyperedge_multihop)(
                    object_ids,
                    max_context_facts=int_or_default(call.get("max_context_facts"), 20),
                    max_evidence_per_item=int_or_default(call.get("max_evidence_per_item"), 5),
                )
            elif tool == "kg.hybrid_search":
                result = run_kg_hybrid_search_for_call(call, question, query)
                result = filter_search_result_by_doc_scope(result, call.get("filters"))
                result["routing_reason"] = call.get("reason", "")
                search_observation = _app_value("record_tool_observation", record_tool_observation)(
                    question,
                    result,
                    tool=tool,
                    turn_id=turn_id,
                    route_id=route_id,
                )
                observations.append(search_observation)
                expand_object_ids = select_expand_object_ids(result, int_or_default(call.get("expand_top_k"), 0), question)
                if expand_object_ids:
                    expand_result = _app_value("kg_expand_hyperedge_multihop", kg_expand_hyperedge_multihop)(
                        expand_object_ids,
                        max_context_facts=int_or_default(call.get("max_context_facts"), 20),
                        max_evidence_per_item=int_or_default(call.get("max_evidence_per_item"), 5),
                    )
                    expand_result["routing_reason"] = "v3 planner follow-up from kg.hybrid_search"
                    expand_result["planner_parent_tool_observation_id"] = search_observation.get("id")
                    expand_result["semantic_guard"] = semantic_guard_for_expand_result(question, expand_result)
                    observations.append(
                        _app_value("record_tool_observation", record_tool_observation)(
                            question,
                            expand_result,
                            tool="kg.expand_hyperedge_multihop",
                            turn_id=turn_id,
                            route_id=route_id,
                        )
                    )
                continue
            elif tool == "kg.sql_aggregate":
                aggregate_client = _app_value("kg_sql_aggregate", kg_sql_aggregate)
                aggregate_intent = str(call.get("intent") or infer_aggregate_intent(question))
                aggregate_target = str(call.get("target") or infer_aggregate_target(question))
                aggregate_filters = normalize_kg_aggregate_filters(call.get("filters"))
                aggregate_group_by = normalize_string_list(call.get("group_by"))
                aggregate_limit = int_or_default(call.get("limit"), 50)
                aggregate_examples = bool(call.get("include_examples", True))
                result = aggregate_client(
                    intent=aggregate_intent,
                    target=aggregate_target,
                    filters=aggregate_filters,
                    group_by=aggregate_group_by,
                    limit=aggregate_limit,
                    include_examples=aggregate_examples,
                )
                if result.get("status") == "empty" and aggregate_filters_in_use(aggregate_filters):
                    result = retry_empty_aggregate(
                        aggregate_client,
                        result,
                        intent=aggregate_intent,
                        target=aggregate_target,
                        filters=aggregate_filters,
                        group_by=aggregate_group_by,
                        limit=aggregate_limit,
                        include_examples=aggregate_examples,
                    )
            elif tool == "kg.doc_field_scan":
                doc_ids = normalize_string_list(call.get("doc_ids"))
                if not doc_ids:
                    fallback_hybrid_call = hybrid_fallback_call_from_doc_scan(
                        call,
                        question,
                        doc_ids=[],
                        reason="kg.doc_field_scan had no doc_ids; fell back to open KG search",
                    )
                    result = run_kg_hybrid_search_for_call(fallback_hybrid_call, question, query)
                    result = filter_search_result_by_doc_scope(result, fallback_hybrid_call.get("filters"))
                    result["routing_reason"] = fallback_hybrid_call["reason"]
                    observations.append(
                        _app_value("record_tool_observation", record_tool_observation)(
                            question,
                            result,
                            tool="kg.hybrid_search",
                            turn_id=turn_id,
                            route_id=route_id,
                        )
                    )
                    continue
                result = _app_value("kg_doc_field_scan", kg_doc_field_scan)(
                    doc_ids=doc_ids,
                    query=query,
                    field_groups=normalize_string_list(call.get("field_groups")),
                    limit=int_or_default(call.get("limit"), 200),
                    include_evidence=bool(call.get("include_evidence", True)),
                )
                if doc_field_scan_structured_empty(result):
                    append_tool_warning(result, DOC_FIELD_SCAN_NO_HYPEREDGES_WARNING)
                    mark_doc_scan_no_structured_coverage(result)
                    fallback_hybrid_call = hybrid_fallback_call_from_doc_scan(
                        call,
                        question,
                        doc_ids=doc_ids,
                        reason=(
                            "kg.doc_field_scan scanned zero structured hyperedges; "
                            "fallback scoped KG search checks raw retrieval evidence"
                        ),
                    )
            else:
                continue
            result["routing_reason"] = call.get("reason", "")
            observation = _app_value("record_tool_observation", record_tool_observation)(
                question,
                result,
                tool=tool,
                turn_id=turn_id,
                route_id=route_id,
            )
            observations.append(observation)
            if fallback_hybrid_call is not None:
                fallback_result = run_kg_hybrid_search_for_call(fallback_hybrid_call, question, query)
                fallback_result = filter_search_result_by_doc_scope(
                    fallback_result,
                    fallback_hybrid_call.get("filters"),
                )
                fallback_result["routing_reason"] = fallback_hybrid_call["reason"]
                fallback_result["planner_parent_tool_observation_id"] = observation.get("id")
                observations.append(
                    _app_value("record_tool_observation", record_tool_observation)(
                        question,
                        fallback_result,
                        tool="kg.hybrid_search",
                        turn_id=turn_id,
                        route_id=route_id,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            observations.append(
                _app_value("record_tool_observation", record_tool_observation)(
                    question,
                    {},
                    error=str(exc),
                    tool=tool,
                    turn_id=turn_id,
                    route_id=route_id,
                )
            )
    return observations
