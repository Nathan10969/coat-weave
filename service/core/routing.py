from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from answering import build_provider_body, extract_provider_text, provider_config, provider_headers, provider_messages_url
from demo_config import (
    KG_HYBRID_DEFAULT_CANDIDATE_K,
    KG_HYBRID_DEFAULT_TOP_K,
    RAW_TURNS,
    SESSION_ID,
    TOOL_ROUTING,
)
from demo_storage import append_jsonl, now_iso, read_jsonl
from demo_text import (
    clamp_int,
    extract_doc_ids,
    extract_hyperedge_object_ids,
    int_or_default,
    normalize_kg_aggregate_filters,
    normalize_kg_search_filters,
    normalize_object_ids,
    normalize_string_list,
)
from scope_state import (
    GLOBAL_SCOPE_TERMS,
    PREVIOUS_SCOPE_TERMS,
    ambiguous_global_comparison,
    document_local_question,
    has_any,
    latest_user_turn_id,
    load_scope_state,
    make_doc_scope,
    make_scope_resolution,
    normalize_scope_state,
    remember_scope_resolution,
    scope_applies_to_kg_search,
)
def _app_value(name: str, default: Any) -> Any:
    app_module = sys.modules.get("app")
    return getattr(app_module, name, default) if app_module is not None else default

def coating_scope_topic(question: str) -> bool:
    return wants_coating_kg_search(question) or has_any(
        question,
        ["涂料", "涂层", "配方", "树脂", "颜料", "填料", "coating", "paint", "formulation", "resin", "pigment", "filler"],
    )

def resolve_scope(question: str, recent_turns: list[dict[str, Any]], current_scope: dict[str, Any]) -> dict[str, Any]:
    state = normalize_scope_state(current_scope)
    explicit_doc_ids = extract_doc_ids(question)
    active = state.get("active_doc_scope")
    history = state.get("scope_history", [])
    source_turn_id = latest_user_turn_id(recent_turns)

    if has_any(question, GLOBAL_SCOPE_TERMS) and not explicit_doc_ids:
        previous = active
        state["active_doc_scope"] = None
        if previous:
            history = [{**previous, "status": "superseded", "superseded_at": now_iso()}, *history]
            state["scope_history"] = history[:10]
        resolution = make_scope_resolution("clear_global", reason="user requested global/all-patent scope")
        remember_scope_resolution(state, resolution)
        return resolution

    if has_any(question, PREVIOUS_SCOPE_TERMS) and history:
        doc_ids = normalize_string_list(history[0].get("doc_ids"))
        resolution = make_scope_resolution("use_history", doc_ids=doc_ids, reason="user explicitly referred to previous patent")
        remember_scope_resolution(state, resolution)
        return resolution

    if explicit_doc_ids:
        if active and normalize_string_list(active.get("doc_ids")) != explicit_doc_ids:
            history = [{**active, "status": "superseded", "superseded_at": now_iso()}, *history]
        state["active_doc_scope"] = make_doc_scope(
            explicit_doc_ids,
            source_turn_id=source_turn_id,
            reason="explicit doc_id in current question",
        )
        state["scope_history"] = history[:10]
        resolution = make_scope_resolution("set_new", doc_ids=explicit_doc_ids, reason="explicit doc_id in current question")
        remember_scope_resolution(state, resolution)
        return resolution

    recovered_doc_ids = recent_doc_ids_for_doc_followup(question, recent_turns)
    if recovered_doc_ids:
        if active and normalize_string_list(active.get("doc_ids")) != recovered_doc_ids:
            history = [{**active, "status": "superseded", "superseded_at": now_iso()}, *history]
        state["active_doc_scope"] = make_doc_scope(
            recovered_doc_ids,
            source_turn_id=source_turn_id,
            reason="doc_id recovered from recent document-local turn",
        )
        state["scope_history"] = history[:10]
        resolution = make_scope_resolution(
            "use_history",
            doc_ids=recovered_doc_ids,
            reason="doc_id recovered from recent document-local turn",
        )
        remember_scope_resolution(state, resolution)
        return resolution

    if active:
        doc_ids = normalize_string_list(active.get("doc_ids"))
        if document_local_question(question):
            resolution = make_scope_resolution("use_active", doc_ids=doc_ids, reason="document-local wording under active scope")
            remember_scope_resolution(state, resolution)
            return resolution
        if ambiguous_global_comparison(question) and coating_scope_topic(question):
            resolution = make_scope_resolution("clarify", doc_ids=doc_ids, reason="ambiguous comparison under active doc scope")
            remember_scope_resolution(state, resolution)
            return resolution
        if _wants_new_patent(question):
            state["active_doc_scope"] = None
            history = [{**active, "status": "superseded", "superseded_at": now_iso()}, *history]
            state["scope_history"] = history[:10]
            resolution = make_scope_resolution(
                "clear_global",
                reason="user explicitly requested a new patent while under active scope",
            )
            remember_scope_resolution(state, resolution)
            return resolution
        if coating_scope_topic(question):
            resolution = make_scope_resolution("use_active", doc_ids=doc_ids, reason="coating KG question under active scope")
            remember_scope_resolution(state, resolution)
            return resolution

    resolution = make_scope_resolution("none", reason="no active document scope applies")
    remember_scope_resolution(state, resolution)
    return resolution

def doc_field_scan_to_hybrid_search_call(call: dict[str, Any], *, reason: str) -> dict[str, Any]:
    query = str(call.get("query") or "").strip()
    filters = normalize_kg_search_filters(call.get("filters"))
    filters["doc_ids"] = normalize_string_list(call.get("doc_ids"))
    call_reason = str(call.get("reason") or "").strip()
    merged_reason = f"{call_reason}; {reason}" if call_reason else reason
    return {
        "tool": "kg.hybrid_search",
        "query": build_kg_search_query(query or "coating KG search", query)[:500],
        "top_k": int_or_default(call.get("top_k"), KG_HYBRID_DEFAULT_TOP_K),
        "candidate_k": int_or_default(call.get("candidate_k"), KG_HYBRID_DEFAULT_CANDIDATE_K),
        "filters": filters,
        "expand_top_k": kg_expand_top_k_for_question(query),
        "reason": merged_reason,
    }

def apply_scope_to_route(route: dict[str, Any], scope_resolution: dict[str, Any]) -> dict[str, Any]:
    scoped = {**route, "scope_resolution": scope_resolution}
    calls: list[dict[str, Any]] = []
    for call in route.get("calls", []):
        if not isinstance(call, dict):
            continue
        updated = dict(call)
        if updated.get("tool") in {"kg.hybrid_search", "kg.sql_aggregate"}:
            if updated.get("tool") == "kg.sql_aggregate":
                filters = normalize_kg_aggregate_filters(updated.get("filters"))
            else:
                filters = normalize_kg_search_filters(updated.get("filters"))
            if scope_applies_to_kg_search(scope_resolution):
                filters["doc_ids"] = normalize_string_list(scope_resolution.get("doc_ids"))
                updated["scope_policy"] = scope_resolution.get("scope_policy", "hard")
            elif scope_resolution.get("scope_action") == "clear_global":
                filters["doc_ids"] = []
            updated["filters"] = filters
        elif updated.get("tool") == "kg.doc_field_scan":
            if scope_applies_to_kg_search(scope_resolution):
                updated["doc_ids"] = normalize_string_list(scope_resolution.get("doc_ids"))
                updated["scope_policy"] = scope_resolution.get("scope_policy", "hard")
            elif scope_resolution.get("scope_action") == "clear_global":
                updated["doc_ids"] = []
            if not normalize_string_list(updated.get("doc_ids")):
                updated = doc_field_scan_to_hybrid_search_call(
                    updated,
                    reason="kg.doc_field_scan requires doc_ids; using open KG search after scope clear",
                )
        calls.append(updated)
    scoped["calls"] = calls
    scoped["plan_type"] = infer_plan_type(calls)
    scoped["needs_tools"] = bool(calls)
    return scoped

def clarify_scope_route(question: str, scope_resolution: dict[str, Any]) -> dict[str, Any]:
    doc_ids = ", ".join(scope_resolution.get("doc_ids") or [])
    return {
        "router": "scope_resolver",
        "needs_tools": False,
        "calls": [],
        "plan_type": "clarify_scope",
        "confidence": 1.0,
        "answer_directly_reason": f"Scope is ambiguous under active document scope {doc_ids}",
        "question": question,
        "scope_resolution": scope_resolution,
    }

def scoped_kg_search_route(question: str, scope_resolution: dict[str, Any]) -> dict[str, Any]:
    route = {
        "router": "scope_guard_fallback",
        "needs_tools": True,
        "calls": [
            {
                "tool": "kg.hybrid_search",
                "query": build_kg_search_query(question),
                "top_k": KG_HYBRID_DEFAULT_TOP_K,
                "candidate_k": KG_HYBRID_DEFAULT_CANDIDATE_K,
                "filters": kg_search_filters_for_question(question),
                "expand_top_k": kg_expand_top_k_for_question(question),
                "reason": "active document scope requires scoped KG search",
            }
        ],
        "plan_type": "kg_search_then_expand",
        "confidence": 0.8,
        "answer_directly_reason": "",
    }
    return apply_scope_to_route(route, scope_resolution)

DOC_FIELD_SCAN_GROUPS = ["test_method", "test_condition", "property", "result", "facts", "materials", "evidence"]

DOC_SCOPE_CONTROL_TERMS = [
    "only look",
    "focus on",
    "switch to",
    "just this patent",
    "only this patent",
    "lock to",
    "锁定",
    "锁定到",
    "只看这篇",
    "只看",
    "只针对",
    "接下来针对",
    "换到",
]

DOC_FACT_INTENT_TERMS = [
    "?",
    "？",
    "have",
    "has",
    "what",
    "which",
    "where",
    "find",
    "show",
    "list",
    "protocol",
    "test",
    "method",
    "condition",
    "result",
    "panel",
    "layer",
    "pigment",
    "filler",
    "wt%",
    "有没有",
    "是什么",
    "有哪些",
    "哪个",
    "哪里",
    "列出",
    "查找",
    "测试",
    "实验",
    "条件",
    "结果",
]

def doc_scope_only_statement(question: str) -> bool:
    residue = question
    for doc_id in extract_doc_ids(question):
        residue = residue.replace(doc_id, "")
    lower = residue.lower()
    if not any(term.lower() in lower for term in DOC_SCOPE_CONTROL_TERMS):
        return False
    return not any(term.lower() in lower for term in DOC_FACT_INTENT_TERMS)


def recent_doc_ids_for_doc_followup(question: str, recent_turns: list[dict[str, Any]]) -> list[str]:
    text = str(question or "").strip()
    if not text or len(text) > 40:
        return []
    is_doc_followup = (
        doc_scope_only_statement(text)
        or document_local_question(text)
        or kg_followup_request(text)
        or any(term in text for term in ("这篇", "本篇", "该专利", "这个专利", "重新", "需要啊", "好", "可以"))
    )
    if not is_doc_followup:
        return []
    for turn in reversed(recent_turns[-12:] if recent_turns else []):
        if turn.get("role") != "user":
            continue
        content = str(turn.get("content") or "").strip()
        if not content or content == text:
            continue
        doc_ids = extract_doc_ids(content)
        if not doc_ids:
            continue
        if document_local_question(content) or wants_coating_kg_search(content) or patent_explanation_request(content):
            return doc_ids
    return []


def unresolved_doc_local_reference(question: str, scope_resolution: dict[str, Any]) -> bool:
    if scope_applies_to_kg_search(scope_resolution) or extract_doc_ids(question):
        return False
    if kg_global_container_question(question):
        return False
    lower = question.lower()
    if ("里面" in question or "里" in question) and not any(term in question for term in DOC_LOCAL_REFERENCE_TERMS):
        return False
    reference_terms = ["this patent", "this doc", "this document", "这篇", "这个专利", "该专利", "里面"]
    return any(term in lower or term in question for term in reference_terms)

def should_use_doc_field_scan(question: str, scope_resolution: dict[str, Any]) -> bool:
    if not scope_applies_to_kg_search(scope_resolution):
        return False
    if not document_local_question(question):
        return False
    if extract_hyperedge_object_ids(question):
        return False
    if wants_kg_aggregate(question) and not document_local_question(question):
        return False
    if scope_resolution.get("scope_action") == "set_new" and doc_scope_only_statement(question):
        return False
    return True

def doc_field_scan_route(question: str, scope_resolution: dict[str, Any]) -> dict[str, Any]:
    doc_ids = normalize_string_list(scope_resolution.get("doc_ids"))
    route = {
        "router": "doc_scope_field_scan_guard",
        "needs_tools": True,
        "calls": [
            {
                "tool": "kg.doc_field_scan",
                "query": question,
                "doc_ids": doc_ids,
                "field_groups": DOC_FIELD_SCAN_GROUPS,
                "limit": 200,
                "include_evidence": True,
                "reason": "active document scope uses doc-level field scan before open retrieval",
            }
        ],
        "plan_type": "kg_doc_field_scan",
        "confidence": 0.9,
        "answer_directly_reason": "",
    }
    return apply_scope_to_route(route, scope_resolution)

def scope_update_route(question: str, scope_resolution: dict[str, Any]) -> dict[str, Any]:
    return {
        "router": "scope_resolver",
        "needs_tools": False,
        "calls": [],
        "plan_type": "scope_update",
        "confidence": 1.0,
        "answer_directly_reason": "Document scope updated without a KG lookup",
        "question": question,
        "scope_resolution": scope_resolution,
    }

def missing_doc_scope_route(question: str) -> dict[str, Any]:
    return {
        "router": "scope_resolver",
        "needs_tools": False,
        "calls": [],
        "plan_type": "clarify_scope",
        "confidence": 1.0,
        "answer_directly_reason": "Document-local wording requires a doc_id or active document scope",
        "question": question,
        "scope_resolution": {
            "scope_action": "clarify",
            "doc_ids": [],
            "scope_policy": "hard",
            "reason": "document-local wording without active document scope",
            "resolved_at": now_iso(),
        },
    }

def should_force_scoped_kg_search(question: str, scope_resolution: dict[str, Any]) -> bool:
    if not scope_applies_to_kg_search(scope_resolution):
        return False
    return document_local_question(question) or wants_coating_kg_search(question)

KG_QUERY_REWRITE_CONCEPTS: dict[str, dict[str, tuple[str, ...]]] = {
    "zinc_rich": {
        "triggers": ("富锌",),
        "terms": ("zinc rich", "zinc-rich"),
    },
    "zinc_powder": {
        "triggers": ("锌粉",),
        "terms": ("zinc dust", "zinc powder"),
    },
    "electrocoat": {
        "triggers": ("电泳涂料", "电泳"),
        "terms": ("electrodepositable coating", "electrocoat", "electrodeposition"),
    },
    "cold_rolled_steel": {
        "triggers": ("冷轧钢",),
        "terms": ("cold rolled steel", "bare cold rolled steel"),
    },
    "steel": {
        "triggers": ("钢",),
        "terms": ("steel",),
    },
    "marine": {
        "triggers": (
            "船舶",
            "船用",
            "海洋",
            "海工",
            "海上",
            "船体",
            "船壳",
            "压载舱",
            "海水",
            "甲板",
            "外板",
            "水线区",
            "油舱",
            "货舱",
            "船坞",
            "坞修",
            "船厂",
            "浪溅区",
            "防海生物附着",
            "海生物附着",
            "海洋生物附着",
            "海洋生物污损",
            "marine",
            "ship",
            "vessel",
            "offshore",
            "deck",
            "cargo hold",
            "cargo tank",
            "dry dock",
            "dry-dock",
            "shipyard",
            "splash zone",
        ),
        "terms": (
            "marine coating",
            "ship coating",
            "vessel hull",
            "offshore coating",
            "seawater",
            "saltwater",
            "ballast tank",
            "deck coating",
            "ballast tank coating",
            "cargo hold coating",
            "cargo tank lining",
            "shipyard repair",
            "dry-dock repair",
            "splash zone coating",
            "marine immersion",
        ),
    },
    "zinc_rich_primer": {
        "triggers": (
            "环氧富锌",
            "富锌",
            "锌粉",
            "富锌底漆",
            "富锌底涂",
            "zinc-rich",
            "zinc rich",
            "zinc dust",
            "zinc powder",
            "zinc pigment",
        ),
        "terms": (
            "epoxy zinc-rich primer",
            "zinc-rich primer",
            "zinc dust",
            "zinc powder",
            "epoxy primer",
            "steel primer",
            "anti-corrosion primer",
        ),
    },
    "silicone": {
        "triggers": (
            "有机硅",
            "聚硅氧烷",
            "硅氧烷",
            "硅树脂",
            "硅烷",
            "silicone",
            "polysiloxane",
            "siloxane",
            "organosilicon",
            "silane",
        ),
        "terms": (
            "silicone coating",
            "polysiloxane coating",
            "silicone resin",
            "siloxane resin",
            "organosilicon coating",
            "silane crosslinker",
            "low surface energy coating",
        ),
    },
    "marine_antifouling": {
        "triggers": (
            "防污",
            "防污涂料",
            "防生物",
            "防生物附着",
            "抗生物附着",
            "防海生物附着",
            "生物污损",
            "污损生物",
            "antifouling",
            "anti-fouling",
            "foul release",
            "biofouling",
            "biological fouling",
        ),
        "terms": (
            "antifouling",
            "anti-fouling",
            "fouling control",
            "foul release",
            "biofouling",
            "biological fouling",
            "biofouling control",
        ),
    },
    "marine_immersion_water": {
        "triggers": ("海水浸泡", "长期浸水", "耐水", "起泡", "吸水", "白化", "浸泡后附着力", "水线区", "seawater immersion", "water resistance", "blistering", "wet adhesion"),
        "terms": ("seawater immersion", "water resistance", "water uptake", "blistering resistance", "water whitening", "immersion durability", "wet adhesion"),
    },
    "marine_adhesion_system": {
        "triggers": ("附着力", "拉开", "划格", "层间附着", "底漆配套", "旧漆重涂", "潮湿基材附着", "pull-off adhesion", "cross-cut adhesion", "intercoat adhesion", "overcoat adhesion"),
        "terms": ("pull-off adhesion", "cross-cut adhesion", "intercoat adhesion", "wet adhesion", "overcoat adhesion", "primer compatibility", "coating system"),
    },
    "marine_mechanical_durability": {
        "triggers": ("耐磨", "抗冲击", "抗刮擦", "抗划伤", "抗碰撞", "耐货物磨损", "耐冰", "锚链区", "靠泊摩擦", "abrasion resistance", "impact resistance", "scratch resistance"),
        "terms": ("abrasion resistance", "wear resistance", "impact resistance", "scratch resistance", "chipping resistance", "ice abrasion", "cargo hold abrasion"),
    },
    "marine_flex_crack": {
        "triggers": ("柔韧性", "柔韧", "不开裂", "开裂", "弯曲", "冷热循环", "焊缝", "边角", "结构变形", "低温不开裂", "flexibility", "crack resistance", "thermal cycling"),
        "terms": ("flexibility", "crack resistance", "bend test", "elongation", "thermal cycling", "low-temperature flexibility", "fatigue resistance"),
    },
    "marine_weathering_uv": {
        "triggers": ("耐候", "耐晒", "抗紫外", "保光", "保色", "粉化", "黄变", "上层建筑", "甲板面漆", "UV resistance", "weathering resistance", "gloss retention", "color retention"),
        "terms": ("UV resistance", "weathering resistance", "gloss retention", "color retention", "chalking resistance", "yellowing resistance", "QUV", "xenon arc"),
    },
    "marine_deck_antislip": {
        "triggers": ("防滑", "止滑", "甲板防滑", "湿态防滑", "耐踩踏", "行走面", "坡道", "直升机甲板", "anti-slip", "non-skid", "slip resistance"),
        "terms": ("anti-slip", "non-skid", "slip resistance", "slip-resistant deck coating", "wet slip resistance", "walkable deck coating", "helideck coating"),
    },
    "marine_repair_constructability": {
        "triggers": ("快干", "低温固化", "潮湿表面施工", "水下修补", "带锈施工", "表面容忍", "厚膜一次成型", "无气喷涂", "适用期", "坞修", "船坞", "dry dock", "fast cure", "surface tolerant"),
        "terms": ("fast cure", "low-temperature curing", "humid cure", "underwater repair", "surface tolerant", "rust tolerant", "high-build", "airless spray", "pot life", "dry-dock repair", "recoat window"),
    },
    "marine_tank_chemical": {
        "triggers": ("耐油", "耐柴油", "耐燃油", "耐液压油", "耐酸碱", "耐清洗剂", "货舱化学品", "溶剂擦拭", "油舱", "货舱", "fuel resistance", "chemical resistance", "cargo tank"),
        "terms": ("chemical resistance", "fuel resistance", "diesel resistance", "hydraulic fluid resistance", "acid resistance", "alkali resistance", "solvent resistance", "solvent rub resistance", "cargo tank lining", "phenolic epoxy"),
    },
    "marine_corrosion": {
        "triggers": ("防腐", "耐腐蚀", "盐雾", "防腐蚀", "corrosion", "salt spray"),
        "terms": ("corrosion protection", "corrosion resistance", "anti-corrosion", "salt spray", "ISO 12944", "ASTM B117", "ISO 9227"),
    },
    "industrial_steel": {
        "triggers": ("钢结构", "重防腐", "工业防腐", "steel substrate", "protective coating"),
        "terms": ("steel substrate", "industrial protective coating", "heavy-duty protective coating"),
    },
    "adhesion": {
        "triggers": ("附着力",),
        "terms": ("adhesion",),
    },
    "hardness": {
        "triggers": ("硬度",),
        "terms": ("hardness",),
    },
    "gloss": {
        "triggers": ("光泽",),
        "terms": ("gloss",),
    },
    "weathering": {
        "triggers": ("耐候",),
        "terms": ("weathering",),
    },
    "test": {
        "triggers": ("测试", "试验"),
        "terms": ("test method", "test result"),
    },
    "formulation_detail": {
        "triggers": (
            "配方",
            "配方结构",
            "配方比例",
            "配比",
            "比例",
            "组成",
            "成分",
            "用量",
            "重量份",
            "含量",
            "formulation",
            "formula",
            "recipe",
            "composition",
        ),
        "terms": (
            "formulation",
            "recipe",
            "composition",
            "formulation table",
            "component amount",
            "parts by weight",
            "wt%",
        ),
    },
}

KG_QUERY_REWRITE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (trigger, concept["terms"])
    for concept in KG_QUERY_REWRITE_CONCEPTS.values()
    for trigger in concept["triggers"]
)

MARINE_CONTEXT_REQUIRED_REWRITE_CONCEPTS = {
    "marine_immersion_water",
    "marine_adhesion_system",
    "marine_mechanical_durability",
    "marine_flex_crack",
    "marine_weathering_uv",
    "marine_deck_antislip",
    "marine_repair_constructability",
    "marine_tank_chemical",
}

MARINE_PROPERTY_FAMILY_CANONICAL_IDS: dict[str, tuple[str, ...]] = {
    "marine_antifouling": (
        "PROP_antifouling_raft_singapore",
        "PROP_antifouling_performance",
        "PROP_anti_fouling_performance",
        "PROP_antifouling_performance_raft",
        "PROP_antifouling_raft_test",
        "PROP_long_term_antifouling",
        "PROP_biofouling_coverage",
        "PROP_antifouling_static",
        "PROP_antifouling_dynamic",
        "PROP_biofouling_resistance",
        "PROP_antifouling_raft_spain",
        "PROP_antifouling_rating_florida_16weeks",
        "PROP_antifouling_rating_florida_8weeks",
        "PROP_antifouling_rating_sandefjord_rotor_18weeks",
        "PROP_antifouling_animal_fouling_score_spain_23wk",
        "PROP_antifouling_animal_fouling_score_singapore_16wk",
        "PROP_antifouling_slime_rating_singapore_32weeks",
        "PROP_antifouling_algae_rating_singapore_32weeks",
        "PROP_antifouling_animals_rating_singapore_32weeks",
        "PROP_efficacy_against_macro_fouling",
        "PROP_marine_fouling_resistance",
        "PROP_weed_fouling_coverage",
        "PROP_antifouling_microfouling_coverage_6mo",
        "PROP_antifouling_microfouling_coverage_12mo",
        "PROP_animal_fouling_coverage",
        "PROP_antifouling_coverage",
        "PROP_biofouling_field_performance",
    ),
    "marine_fouling_release": (
        "PROP_fouling_release_performance",
        "PROP_fouling_rating",
        "PROP_antifouling_total_fouling",
        "PROP_antifouling_soft_fouling",
        "PROP_antifouling_hard_fouling",
        "PROP_fouling_release_performance_soft",
        "PROP_fouling_release_performance_hard",
        "PROP_fouling_coverage_changi_44wk",
        "PROP_fouling_coverage_changi_35wk",
        "PROP_fouling_release_at_1p5_mps_80d",
        "PROP_fouling_release_at_2p6_mps_80d",
        "PROP_fouling_release_at_1p5_mps_7d",
        "PROP_fouling_release_at_2p6_mps_7d",
        "PROP_fouling_release_performance_raft_singapore_40weeks",
        "PROP_fouling_release_performance_raft_singapore_63weeks",
        "PROP_fouling_release_performance_raft_sandefjord_65weeks",
    ),
    "marine_corrosion": (
        "PROP_corrosion_resistance",
        "PROP_corrosion_resistance_salt_spray",
        "PROP_salt_spray_resistance",
        "PROP_salt_spray_corrosion_resistance",
        "PROP_nss_corrosion_width",
        "PROP_cathodic_delamination",
        "PROP_corrosion_protection",
        "PROP_corrosion_creep",
        "PROP_corrosion_rating",
    ),
    "marine_immersion_durability": (
        "PROP_blistering_fw_immersion",
        "PROP_cracking_sw_immersion",
        "PROP_wet_adhesion_seawater",
        "PROP_water_immersion_pass",
        "PROP_immersion_adhesion",
    ),
    "marine_adhesion": (
        "PROP_adhesion",
        "PROP_ADHESION",
        "PROP_cross_hatch_adhesion",
        "PROP_adhesion_rating",
        "PROP_wet_adhesion_seawater",
        "PROP_immersion_adhesion",
    ),
    "marine_mechanical_durability": (
        "PROP_abrasion_resistance",
        "PROP_CHIPPING_RESISTANCE",
    ),
    "marine_weathering_uv": (
        "PROP_gloss_60",
        "PROP_GLOSS",
        "PROP_total_solar_reflectance",
    ),
    "marine_repair_constructability": (
        "PROP_tack_free_time",
    ),
    "marine_tank_chemical": (
        "PROP_acid_resistance",
    ),
}

MARINE_DEFAULT_PROPERTY_FAMILIES: tuple[str, ...] = (
    "marine_antifouling",
    "marine_fouling_release",
    "marine_corrosion",
    "marine_immersion_durability",
    "marine_adhesion",
    "marine_mechanical_durability",
    "marine_weathering_uv",
    "marine_repair_constructability",
    "marine_tank_chemical",
)

MARINE_PROPERTY_FAMILIES_BY_REWRITE_CONCEPT: dict[str, tuple[str, ...]] = {
    "marine_antifouling": ("marine_antifouling", "marine_fouling_release"),
    "marine_immersion_water": ("marine_immersion_durability", "marine_adhesion"),
    "marine_adhesion_system": ("marine_adhesion", "marine_immersion_durability"),
    "marine_mechanical_durability": ("marine_mechanical_durability",),
    "marine_flex_crack": ("marine_immersion_durability",),
    "marine_weathering_uv": ("marine_weathering_uv",),
    "marine_repair_constructability": ("marine_repair_constructability",),
    "marine_tank_chemical": ("marine_tank_chemical",),
    "marine_corrosion": ("marine_corrosion",),
}

SEMANTIC_OPERATOR_TERMS: dict[str, tuple[str, ...]] = {
    "decrease": (
        "reduce",
        "reduced",
        "reducing",
        "lower",
        "lowered",
        "low",
        "less",
        "decrease",
        "decreased",
        "降低",
        "减少",
        "低含量",
        "低",
    ),
    "increase": (
        "increase",
        "increased",
        "increasing",
        "higher",
        "high",
        "more",
        "raise",
        "raised",
        "improve by increasing",
        "增加",
        "增多",
        "提高",
        "更高",
    ),
    "replace": (
        "replace",
        "replacement",
        "substitute",
        "substitution",
        "zinc-free",
        "zinc free",
        "without zinc",
        "替代",
        "取代",
        "无锌",
    ),
}

ZINC_LOADING_TRIGGERS: tuple[str, ...] = (
    "zinc loading",
    "zinc content",
    "zinc dust",
    "zinc powder",
    "zinc pigment",
    "zinc-rich",
    "zinc rich",
    "zinc",
    "锌含量",
    "锌粉",
    "富锌",
)

CORROSION_OUTCOME_TRIGGERS: tuple[str, ...] = (
    "corrosion",
    "anti-corrosion",
    "anticorrosion",
    "corrosion protection",
    "corrosion resistance",
    "salt spray",
    "salt fog",
    "astm b117",
    "red rust",
    "scribe creep",
    "galvanic protection",
    "防腐",
    "耐腐蚀",
    "盐雾",
)

ZINC_LOADING_EXPANSION_TERMS: dict[str, tuple[str, ...]] = {
    "decrease": (
        "reduced zinc loading",
        "lower zinc content",
        "low zinc",
        "low zinc primer",
        "reduced zinc dust",
        "less zinc",
        "zinc dust reduced",
        "reduced metal pigment",
        "zinc-rich primer with reduced zinc",
    ),
    "increase": (
        "increased zinc loading",
        "higher zinc content",
        "high zinc loading",
        "more zinc dust",
        "high zinc",
        "zinc-rich",
        "zinc rich primer",
    ),
    "replace": (
        "zinc-free",
        "zinc free",
        "zinc replacement",
        "zinc phosphate replacement",
        "conductive pigment replacement",
        "graphene enhanced zinc primer",
        "zinc-free anti-corrosion",
    ),
}

CORROSION_EXPANSION_TERMS: tuple[str, ...] = (
    "corrosion resistance",
    "corrosion protection",
    "anti-corrosion",
    "salt spray",
    "salt fog",
    "ASTM B117",
    "red rust",
    "scribe creep",
    "galvanic protection",
    "maintain corrosion resistance",
    "retain corrosion protection",
)

def contains_any(text: str, terms: tuple[str, ...] | list[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms)

def rewrite_concept_active(question: str, concept: str) -> bool:
    config = KG_QUERY_REWRITE_CONCEPTS.get(concept, {})
    return contains_any(question, config.get("triggers", ()))

def zinc_rich_primer_context(question: str) -> bool:
    lower = str(question or "").lower()
    return contains_any(
        lower,
        (
            "zinc-rich",
            "zinc rich",
            "zinc dust",
            "zinc powder",
            "zinc pigment",
            "epoxy zinc",
        ),
    ) or any(term in question for term in ("环氧富锌", "富锌", "锌粉"))

def append_unique_values(out: list[str], values: tuple[str, ...] | list[str]) -> None:
    seen = {value.lower() for value in out}
    for value in values:
        text = str(value).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)

def marine_property_families_for_question(question: str) -> list[str]:
    if not rewrite_concept_active(question, "marine"):
        return []
    families: list[str] = []
    for concept, concept_families in MARINE_PROPERTY_FAMILIES_BY_REWRITE_CONCEPT.items():
        if rewrite_concept_active(question, concept):
            append_unique_values(families, concept_families)
    if any(term in question for term in ("防腐", "耐腐蚀", "盐雾")):
        append_unique_values(families, ("marine_corrosion",))
    if zinc_rich_primer_context(question):
        append_unique_values(families, ("marine_corrosion",))
    if families:
        return families
    return []

def property_canonical_ids_for_families(families: list[str]) -> list[str]:
    canonical_ids: list[str] = []
    for family in families:
        append_unique_values(canonical_ids, MARINE_PROPERTY_FAMILY_CANONICAL_IDS.get(family, ()))
    return canonical_ids

def append_unique_terms(additions: list[str], seen: set[str], terms: tuple[str, ...] | list[str], lower: str) -> None:
    for term in terms:
        normalized = term.lower()
        if normalized in lower or normalized in seen:
            continue
        seen.add(normalized)
        additions.append(term)

def analyze_kg_query_semantics(question: str) -> dict[str, Any]:
    text = str(question or "")
    lower = text.lower()
    concepts: list[str] = []
    outcomes: list[str] = []
    operator = "unknown"

    if contains_any(lower, ZINC_LOADING_TRIGGERS):
        concepts.append("zinc_loading")
    if contains_any(lower, CORROSION_OUTCOME_TRIGGERS):
        outcomes.append("corrosion_protection")

    for candidate in ("replace", "decrease", "increase"):
        if contains_any(lower, SEMANTIC_OPERATOR_TERMS[candidate]):
            operator = candidate
            break

    expansion_terms: list[str] = []
    required_terms: list[str] = []
    seen: set[str] = set()
    if "zinc_loading" in concepts and operator in ZINC_LOADING_EXPANSION_TERMS:
        append_unique_terms(expansion_terms, seen, ZINC_LOADING_EXPANSION_TERMS[operator], lower)
        required_terms.extend(ZINC_LOADING_EXPANSION_TERMS[operator])
    if "corrosion_protection" in outcomes:
        append_unique_terms(expansion_terms, seen, CORROSION_EXPANSION_TERMS, lower)

    return {
        "operator": operator,
        "concepts": concepts,
        "outcomes": outcomes,
        "expansion_terms": expansion_terms,
        "required_terms": required_terms,
        "guard": bool("zinc_loading" in concepts and operator in ZINC_LOADING_EXPANSION_TERMS),
    }

def build_kg_search_query(query: str, original_question: str = "") -> str:
    combined = f"{query}\n{original_question}".strip()
    lower = combined.lower()
    additions: list[str] = []
    seen: set[str] = set()
    marine_context_active = rewrite_concept_active(combined, "marine")
    for concept_name, concept in KG_QUERY_REWRITE_CONCEPTS.items():
        if concept_name in MARINE_CONTEXT_REQUIRED_REWRITE_CONCEPTS and not marine_context_active:
            continue
        if not contains_any(combined, concept["triggers"]):
            continue
        append_unique_terms(additions, seen, concept["terms"], lower)
    semantics = analyze_kg_query_semantics(combined)
    append_unique_terms(additions, seen, semantics.get("expansion_terms", []), lower)
    base = str(query or original_question).strip()
    base_lower = base.lower()
    preserved_ids = [
        token
        for token in re.findall(r"\b(?:MAT|SUB|APP|PROP|TEST|PROC)_[A-Za-z0-9][A-Za-z0-9_:-]*", str(original_question or ""))
        if token.lower() not in base_lower
    ]
    if not additions and not preserved_ids:
        return base
    return f"{base} {' '.join(preserved_ids + additions)}".strip()

def explicit_corrosion_property_filter_requested(question: str) -> bool:
    lower = str(question or "").lower()
    asks_corrosion = contains_any(
        lower,
        (
            "corrosion_protection",
            "corrosion protection",
            "corrosion resistance",
            "anti-corrosion",
            "salt spray",
            "iso 12944",
            "astm b117",
            "iso 9227",
        ),
    ) or any(term in question for term in ["防腐", "耐腐蚀", "盐雾", "盐雾防腐"])
    explicit_scope = contains_any(lower, ("only", "strictly", "filter", "must be", "property=")) or any(
        term in question for term in ["只看", "仅看", "只要", "限定", "限于", "必须是", "明确"]
    )
    return asks_corrosion and explicit_scope

def kg_search_filters_for_question(question: str, raw_filters: Any = None) -> dict[str, Any]:
    filters = normalize_kg_search_filters(raw_filters)
    if rewrite_concept_active(question, "marine") and "marine" not in {value.lower() for value in filters["application_family"]}:
        filters["application_family"].append("marine")
    property_families = marine_property_families_for_question(question)
    append_unique_values(filters["property_families"], property_families)
    append_unique_values(
        filters["property_canonical_ids_soft"],
        property_canonical_ids_for_families(property_families),
    )
    if explicit_corrosion_property_filter_requested(question):
        if "corrosion_protection" not in {value.lower() for value in filters["properties"]}:
            filters["properties"].append("corrosion_protection")
    else:
        filters["properties"] = [value for value in filters["properties"] if value.lower() != "corrosion_protection"]
    return filters

def build_kg_search_queries(query: str, original_question: str = "", max_variants: int = 3) -> list[str]:
    primary = build_kg_search_query(query, original_question)
    semantics = analyze_kg_query_semantics(f"{query}\n{original_question}")
    variants = [primary]
    if semantics.get("guard"):
        base = str(query or original_question).strip()
        required = " ".join(semantics.get("required_terms", [])[:5])
        outcomes = " ".join(term for term in CORROSION_EXPANSION_TERMS[:5] if term.lower() not in base.lower())
        if required:
            variants.append(f"{base} {required} {outcomes}".strip())
        if semantics.get("operator") == "decrease":
            variants.append(
                f"{base} low zinc reduced zinc lower zinc content maintain corrosion resistance salt spray".strip()
            )
        elif semantics.get("operator") == "increase":
            variants.append(
                f"{base} high zinc increased zinc higher zinc content corrosion resistance salt spray".strip()
            )
        elif semantics.get("operator") == "replace":
            variants.append(
                f"{base} zinc-free zinc replacement conductive pigment graphene corrosion resistance".strip()
            )
    out: list[str] = []
    seen_variants: set[str] = set()
    for variant in variants:
        normalized = " ".join(str(variant).split())
        key = normalized.lower()
        if not normalized or key in seen_variants:
            continue
        seen_variants.add(key)
        out.append(normalized[:500])
        if len(out) >= max_variants:
            break
    return out

def kg_search_only_requested(question: str) -> bool:
    lower = question.lower()
    search_only_terms = [
        "object_id",
        "object ids",
        "hyperedge id",
        "hyperedge ids",
        "ids only",
        "candidate ids",
        "ranked ids",
    ]
    if any(term in lower for term in search_only_terms):
        return True
    return any(term in question for term in ["只要ID", "只要 id", "候选ID", "候选 id", "object_ids"])

def kg_expand_top_k_for_question(question: str) -> int:
    if kg_search_only_requested(question):
        return 0
    if analyze_kg_query_semantics(question).get("guard"):
        return 6
    lower = question.lower()
    evidence_terms = [
        "answer",
        "evidence",
        "quote",
        "page",
        "table",
        "where",
        "mentioned",
        "test",
        "result",
        "method",
        "compare",
        "recommend",
    ]
    if any(term in lower for term in evidence_terms):
        return 6
    if any(term in question for term in ["回答", "证据", "引用", "页", "表", "哪里", "提到", "测试", "试验", "结果", "方法", "对比", "推荐", "怎么样"]):
        return 6
    return 6

AGGREGATE_INTENTS = {"distinct_count", "list_distinct", "group_count", "numeric_distribution"}

AGGREGATE_TARGETS = {
    "test_method",
    "property",
    "material",
    "material_role",
    "resin_system",
    "substrate",
    "assignee",
    "doc_id",
    "example_kind",
    "polarity",
    "amount",
    "application_family",
    "formulation",
}

KG_GLOBAL_CONTAINER_TERMS = ("图谱", "知识图谱", "数据库", "KG", "kg", "全库")
SCOPE_RESET_TERMS = (
    "解除锁定",
    "解除scope",
    "解除 scope",
    "取消scope",
    "取消 scope",
    "接触锁定",
    "新检索",
    "重新检索",
    "不限当前",
    "不限这篇",
    "其他专利",
)
DOC_LOCAL_REFERENCE_TERMS = ("这篇", "本篇", "该专利", "这个专利", "这个文献", "该文献", "这份专利")

PATENT_EXPLANATION_TERMS = ("讲解", "解读", "分析", "介绍", "说一下", "具体", "说一个")
PATENT_SINGLE_TERMS = ("给一篇", "给我一篇", "找一篇", "一篇")
PATENT_MULTI_TERMS = (
    "给我几篇",
    "再给我几篇",
    "再来几篇",
    "多给几篇",
    "几篇",
    "十篇",
    "10篇",
    "其他专利",
    "要其他专利",
)
KG_FOLLOWUP_TERMS = (
    "再给我几篇",
    "再来几篇",
    "多给几篇",
    "还有吗",
    "继续",
    "more",
    "several more",
    "其他专利",
    "要其他专利",
    "换一篇",
    "比例",
    "配比",
    "组成",
    "成分",
    "用量",
    "重量份",
    "重新",
    "需要啊",
    "只看这篇",
)
COATING_DOMAIN_TERMS = (
    "涂料",
    "专利",
    "证据",
    "配方",
    "性能",
    "树脂",
    "基料",
    "固化剂",
    "颜料",
    "填料",
    "有机硅",
    "聚硅氧烷",
    "硅氧烷",
    "硅树脂",
    "硅烷",
    "防污",
    "防腐",
    "富锌",
    "环氧",
    "涂层",
    "油漆",
    "底漆",
    "底涂",
    "助剂",
    "聚氨酯",
    "锌粉",
    "防腐蚀",
    "耐腐蚀",
    "盐雾",
    "船舶",
    "船用",
    "海洋",
    "海工",
    "海水",
    "压载舱",
    "甲板",
    "配比",
    "比例",
    "组成",
    "成分",
    "用量",
)
PATENT_TECH_TERMS = (
    "有机硅",
    "聚硅氧烷",
    "硅氧烷",
    "硅树脂",
    "硅烷",
    "富锌",
    "锌粉",
    "环氧",
    "聚氨酯",
    "防污",
    "防腐",
    "silicone",
    "polysiloxane",
    "siloxane",
    "zinc-rich",
    "zinc rich",
    "epoxy",
)

def kg_global_container_question(question: str) -> bool:
    return any(term in str(question or "") for term in KG_GLOBAL_CONTAINER_TERMS)

def patent_list_request(question: str) -> bool:
    text = str(question or "")
    lower = text.lower()
    has_patent = "专利" in text or "涓撳埄" in text or "patent" in lower
    if has_patent and (
        any(term in text for term in PATENT_MULTI_TERMS)
        or any(term in text for term in ["列出", "给我", "我要", "要"])
    ):
        return True
    return (
        ("patent" in lower and any(term in lower for term in ["list", "show", "give me"]))
        or any(term in text for term in ["给我10篇", "我要10篇", "列出10篇", "给我十篇", "我要十篇"])
        or ("专利" in text and any(term in text for term in ["列出", "给我", "我要"]))
        or ("专利" in text and any(term in text for term in PATENT_MULTI_TERMS))
    )

def patent_explanation_request(question: str) -> bool:
    text = str(question or "")
    lower = text.lower()
    has_patent = "专利" in text or "patent" in lower
    if not has_patent:
        return False
    if any(term in text for term in PATENT_MULTI_TERMS) and not any(term in text for term in PATENT_EXPLANATION_TERMS):
        return False
    return any(term in text for term in (*PATENT_SINGLE_TERMS, *PATENT_EXPLANATION_TERMS)) or any(
        term in lower for term in ("explain", "walk through", "analyze", "specific patent")
    )

def patent_technology_search_request(question: str) -> bool:
    text = str(question or "")
    lower = text.lower()
    has_patent = "专利" in text or "patent" in lower
    if not has_patent:
        return False
    has_tech = any(term in text for term in PATENT_TECH_TERMS) or any(term in lower for term in PATENT_TECH_TERMS)
    if not has_tech:
        return False
    statistical_terms = ("多少", "总数", "统计", "占比", "分布", "count", "how many", "distribution", "percentage")
    return not (any(term in text for term in statistical_terms) or any(term in lower for term in statistical_terms))


def _wants_new_patent(question: str) -> bool:
    """User explicitly asks for a NEW patent — used to break out of active scope.

    Why: when an active doc scope is pinned (e.g. WO2014202495A1) and the user
    says "给一篇有机硅专利", the old logic let coating_scope_topic() steer the
    request through use_active → doc_field_scan on the pinned patent only,
    even though "给一篇" means "give me ONE [new]". This predicate keeps
    explicit new-patent intent from being silently swallowed by active scope.
    """
    if patent_list_request(question) or patent_technology_search_request(question):
        return True
    if patent_explanation_request(question):
        text = str(question or "")
        if any(term in text for term in PATENT_SINGLE_TERMS):
            return True
        if any(term in text for term in PATENT_MULTI_TERMS):
            return True
    return False


def kg_followup_request(question: str) -> bool:
    text = str(question or "").strip()
    lower = text.lower()
    if len(text) > 40:
        return False
    return any(term in text for term in KG_FOLLOWUP_TERMS) or any(term in lower for term in KG_FOLLOWUP_TERMS)

def contextual_kg_followup_question(question: str) -> str:
    if not (kg_followup_request(question) or doc_scope_only_statement(question)):
        return question
    turns = read_jsonl(_app_value("RAW_TURNS", RAW_TURNS))
    current = str(question or "").strip()
    for turn in reversed(turns[-10:]):
        if turn.get("role") != "user":
            continue
        content = str(turn.get("content") or "").strip()
        if not content or content == current:
            continue
        if (
            document_local_question(content)
            or patent_explanation_request(content)
            or patent_list_request(content)
            or wants_coating_kg_search(content)
        ):
            return f"{content}\n{question}"
    return question

def resin_system_aggregate_question(question: str) -> bool:
    text = str(question or "")
    lower = text.lower()
    return any(
        term in lower
        for term in ["resin system", "binder family", "resin family", "binder system"]
    ) or any(term in text for term in ["树脂体系", "树脂类型", "树脂类别", "成膜物体系", "基料体系"])

def total_count_followup(question: str) -> bool:
    lower = str(question or "").lower()
    return any(term in lower for term in ["total", "total count", "count it"]) or any(
        term in question for term in ["总数", "需要总数", "没错", "对的", "就是要数量"]
    )

def aggregate_context_question(question: str) -> str:
    if not total_count_followup(question):
        return question
    turns = read_jsonl(_app_value("RAW_TURNS", RAW_TURNS))
    for turn in reversed(turns[-8:]):
        if turn.get("role") != "user":
            continue
        content = str(turn.get("content") or "").strip()
        if content and not total_count_followup(content):
            return f"{content}\n{question}"
    return question

def wants_kg_aggregate(question: str) -> bool:
    lower = str(question or "").lower()
    if patent_explanation_request(question) or patent_technology_search_request(question):
        return False
    if resin_system_aggregate_question(question) or patent_list_request(question):
        return True
    if kg_global_container_question(question) and any(term in str(question or "") for term in ["专利", "多少", "几篇", "总数"]):
        return True
    if any(term in question for term in ["多少钱", "多少元"]) and not wants_coating_kg_search(question):
        return False
    aggregate_terms = [
        "count",
        "how many",
        "distinct",
        "distribution",
        "group by",
        "grouped by",
        "top ",
        "most frequent",
        "frequency",
        "statistics",
        "statistical",
    ]
    if any(term in lower for term in aggregate_terms):
        return wants_coating_kg_search(question) or infer_aggregate_target(question) != "property"
    if total_count_followup(question):
        context_question = aggregate_context_question(question)
        return context_question != question and (
            wants_coating_kg_search(context_question) or infer_aggregate_target(context_question) != "property"
        )
    chinese_count = any(
        term in question
        for term in [
            "多少种",
            "有多少",
            "多少个",
            "总数",
            "几种",
            "几个",
            "统计",
            "数量",
            "分布",
            "按",
            "分组",
            "出现最多",
            "最多",
            "列出所有",
            "所有测试方法",
            "有哪些测试方法",
            "用量分布",
        ]
    )
    return chinese_count and (wants_coating_kg_search(question) or infer_aggregate_target(question) != "property")

def infer_aggregate_intent(question: str) -> str:
    lower = str(question or "").lower()
    if resin_system_aggregate_question(question):
        return "group_count"
    if patent_list_request(question):
        return "list_distinct"
    if any(term in lower for term in ["distribution", "amount distribution"]) or any(
        term in question for term in ["分布", "用量分布"]
    ):
        if infer_aggregate_target(question) == "amount":
            return "numeric_distribution"
    if any(term in lower for term in ["list", "list all", "show all"]) or any(
        term in question for term in ["列出", "列出所有", "有哪些"]
    ):
        return "list_distinct"
    if any(term in lower for term in ["group by", "grouped by", "most frequent", "top "]) or any(
        term in question for term in ["按", "分组", "出现最多", "最多"]
    ):
        return "group_count"
    return "distinct_count"

def infer_aggregate_target(question: str) -> str:
    lower = str(question or "").lower()
    # "多少片 / 几篇 / 多少篇 / 多少专利" — 片/篇 are measure words for patents,
    # so the counting unit is documents, not formulations. Check this before the
    # formulation branch so "多少片...相关" is not mis-targeted to formulation.
    if any(term in question for term in ["多少片", "几片", "多少篇", "几篇", "多少专利", "几个专利", "多少文献"]):
        return "doc_id"
    if resin_system_aggregate_question(question):
        return "resin_system"
    if any(term in lower for term in ["test method", "test methods", "testing method", "standard", "astm", "iso"]) or any(
        term in question for term in ["测试方法", "试验方法", "测试标准", "试验标准"]
    ):
        return "test_method"
    if any(term in lower for term in ["formulation", "formula", "recipe", "coating system", "paint system"]) or any(
        term in question for term in ["配方", "涂料体系", "涂层体系"]
    ):
        return "formulation"
    if any(term in lower for term in ["patent", "patents", "doc_id", "document"]) or any(
        term in question for term in ["专利", "文献"]
    ):
        return "doc_id"
    if any(term in lower for term in ["application family", "application field", "use field", "marine"]) or any(
        term in question for term in ["应用领域", "应用场景", "船舶", "海洋"]
    ):
        return "application_family"
    if any(term in lower for term in ["assignee", "company", "applicant"]) or any(
        term in question for term in ["公司", "申请人", "权利人"]
    ):
        return "assignee"
    if any(term in lower for term in ["amount", "wt%", "weight percent", "loading", "dosage"]) or any(
        term in question for term in ["用量", "含量", "wt%", "重量百分比", "配比"]
    ):
        return "amount"
    if any(term in lower for term in ["material role", "role", "resin", "pigment", "filler", "additive"]) or any(
        term in question for term in ["材料角色", "树脂", "颜料", "填料", "助剂"]
    ):
        if any(term in question for term in ["多少种", "几种", "列出", "有哪些"]):
            return "material"
        return "material_role"
    if any(term in lower for term in ["material", "materials"]) or any(term in question for term in ["材料", "组分", "成分"]):
        return "material"
    if any(term in lower for term in ["substrate", "panel"]) or any(term in question for term in ["基材", "底材", "板材"]):
        return "substrate"
    if any(term in lower for term in ["patent", "doc_id", "document"]) or any(term in question for term in ["专利", "文献"]):
        return "doc_id"
    if any(term in lower for term in ["example kind", "comparative", "example type"]) or any(
        term in question for term in ["实施例类型", "对比例"]
    ):
        return "example_kind"
    if any(term in lower for term in ["polarity", "positive", "negative"]) or any(term in question for term in ["极性", "正例", "负例"]):
        return "polarity"
    if any(term in lower for term in ["property", "performance"]) or any(term in question for term in ["性能", "性质", "指标"]):
        return "property"
    return "property"

def aggregate_group_by_for_question(question: str, target: str, intent: str) -> list[str]:
    lower = str(question or "").lower()
    if intent != "group_count":
        return []
    if target == "resin_system" or resin_system_aggregate_question(question):
        return ["resin_system"]
    if any(term in lower for term in ["company", "assignee", "applicant"]) or any(term in question for term in ["公司", "申请人"]):
        return ["assignee"]
    if any(term in lower for term in ["property", "performance"]) or any(term in question for term in ["性能", "指标"]):
        return ["property"]
    if any(term in lower for term in ["test method", "standard"]) or any(term in question for term in ["测试方法", "试验方法"]):
        return ["test_method"]
    return [target]

def aggregate_filters_for_question(question: str) -> dict[str, Any]:
    filters = normalize_kg_aggregate_filters(None)
    lower = str(question or "").lower()
    property_families = marine_property_families_for_question(question)
    append_unique_values(filters["property_families"], property_families)
    append_unique_values(
        filters["property_canonical_ids_soft"],
        property_canonical_ids_for_families(property_families),
    )
    if not property_families and (
        "corrosion" in lower or any(term in question for term in ["防腐", "耐腐蚀", "盐雾"])
    ):
        filters["properties"] = ["corrosion_protection"]
    if any(
        term in lower
        for term in [
            "marine",
            "ship",
            "vessel",
            "naval",
            "offshore",
            "deck",
            "cargo hold",
            "cargo tank",
            "dry dock",
            "dry-dock",
            "shipyard",
            "splash zone",
        ]
    ) or any(
        term in question for term in ["船舶", "海洋", "海工", "海上", "压载舱", "甲板", "油舱", "货舱", "坞修", "船坞", "船厂", "浪溅区"]
    ):
        filters["application_family"] = ["marine"]
    if any(term in lower for term in ["resin", "binder"]) or "树脂" in question:
        filters["material_roles"] = ["resin"]
    elif any(term in lower for term in ["pigment", "pigments"]) or "颜料" in question:
        filters["material_roles"] = ["pigments"]
    elif any(term in lower for term in ["filler", "fillers"]) or "填料" in question:
        filters["material_roles"] = ["fillers"]
    elif any(term in lower for term in ["additive", "additives"]) or "助剂" in question:
        filters["material_roles"] = ["additives"]
    return filters

def merge_aggregate_domain_filters(question: str, raw_filters: Any) -> dict[str, Any]:
    """Normalize aggregate filters to a stable, property-family-based signal.

    Problem this fixes: the LLM picks aggregate filters ad-hoc each turn, so
    "船舶防腐/防污" counts drifted and were wrong. Naively unioning the curated
    marine families on top of the LLM's filters made it WORSE, because the KG
    backend ANDs across the listed property_families (so [marine_antifouling,
    marine_fouling_release] → 0) and ANDs property_families + application_family
    + the LLM's bare `properties` (so corrosion → 1 instead of ~34).

    Chosen semantics (user decision): "property-family" count — a record counts
    if it carries ANY property in the concept's canonical-id set. The cleanest
    way to express OR-over-properties against this backend is the
    `property_canonical_ids_soft` list (OR-within-list). So when the question
    maps to a marine property family we:
      - set property_canonical_ids_soft = the family's canonical ids (OR)
      - clear property_families (avoids backend multi-family AND → ~0)
      - clear bare `properties` (the canonical-id set supersedes it)
      - clear application_family (the marine_* family is already marine-scoped;
        the user wants the family count, not marine ∩ family which is stricter)

    For non-marine-family questions we keep the LLM's filters and merge the
    other curated dims (material_roles etc.) by union.
    """
    merged = normalize_kg_aggregate_filters(raw_filters)
    families = marine_property_families_for_question(question)
    if families:
        soft_ids = property_canonical_ids_for_families(families)
        append_unique_values(merged["property_canonical_ids_soft"], soft_ids)
        merged["property_families"] = []
        merged["properties"] = []
        merged["application_family"] = []
        return merged

    domain = aggregate_filters_for_question(question)
    for key, values in domain.items():
        if not isinstance(values, list) or not values:
            continue
        existing = merged.get(key)
        if isinstance(existing, list):
            append_unique_values(existing, values)
        else:
            merged[key] = list(values)
    return merged

def build_aggregate_call(question: str) -> dict[str, Any]:
    context_question = aggregate_context_question(question)
    target = infer_aggregate_target(context_question)
    intent = infer_aggregate_intent(context_question)
    if intent == "numeric_distribution":
        target = "amount"
    filters = aggregate_filters_for_question(context_question)
    if target == "doc_id" and (kg_global_container_question(context_question) or patent_list_request(context_question)):
        filters["doc_ids"] = []
    if target == "resin_system":
        filters["material_roles"] = []
        if any(term in context_question.lower() for term in ["marine", "ship", "vessel", "hull"]) or any(
            term in context_question for term in ["船舶", "船用", "海洋", "船体", "船壳"]
        ):
            append_unique_values(filters["application_family"], ["marine"])
        if any(term in context_question.lower() for term in ["antifouling", "fouling", "biofouling"]) or any(
            term in context_question for term in ["防污", "污损释放", "防生物", "生物污损"]
        ):
            append_unique_values(filters["property_families"], ["marine_antifouling", "marine_fouling_release"])
            append_unique_values(
                filters["property_canonical_ids_soft"],
                property_canonical_ids_for_families(["marine_antifouling", "marine_fouling_release"]),
            )
    return {
        "tool": "kg.sql_aggregate",
        "query": question,
        "intent": intent,
        "target": target,
        "filters": filters,
        "group_by": aggregate_group_by_for_question(context_question, target, intent),
        "limit": 50,
        "include_examples": True,
        "reason": "statistical coating KG aggregate fallback",
    }

def infer_plan_type(calls: list[dict[str, Any]]) -> str:
    if any(call.get("tool") == "kg.sql_aggregate" for call in calls):
        return "kg_aggregate" if len(calls) == 1 else "kg_mixed_plan"
    if any(call.get("tool") == "kg.doc_field_scan" for call in calls):
        return "kg_doc_field_scan" if len(calls) == 1 else "kg_mixed_plan"
    if any(call.get("tool") == "kg.hybrid_search" and int_or_default(call.get("expand_top_k"), 0) > 0 for call in calls):
        return "kg_search_then_expand"
    if any(call.get("tool") == "kg.hybrid_search" for call in calls):
        return "kg_search_only"
    if any(call.get("tool") == "kg.expand_hyperedge_multihop" for call in calls):
        return "kg_expand_only"
    if calls:
        return "tool_calls"
    return "answer_direct"

def wants_coating_kg_search(question: str) -> bool:
    if extract_hyperedge_object_ids(question):
        return False
    if patent_explanation_request(question):
        return True
    lower = question.lower()
    if any(term in question for term in ("配方", "涂料", "涂层", "专利")) and any(
        term in question
        for term in (
            "组成",
            "成分",
            "比例",
            "配比",
            "用量",
            "重量份",
            "结构",
        )
    ):
        return True
    english_signals = [
        "coating",
        "paint",
        "primer",
        "resin",
        "epoxy",
        "polyurethane",
        "silicone",
        "polysiloxane",
        "siloxane",
        "organosilicon",
        "silane",
        "zinc",
        "salt spray",
        "corrosion",
        "anti-corrosion",
        "anticorrosion",
        "substrate",
        "steel",
        "cold rolled",
        "galvanic",
        "adhesion",
        "gloss",
        "hardness",
        "weathering",
        "test method",
        "astm",
        "b117",
        "electrodeposit",
        "electrodepositable",
        "antifouling",
        "anti-fouling",
        "foul release",
        "biofouling",
        "biological fouling",
        "fouling control",
        "seawater immersion",
        "water resistance",
        "blistering",
        "wet adhesion",
        "pull-off adhesion",
        "cross-cut adhesion",
        "intercoat adhesion",
        "overcoat adhesion",
        "abrasion resistance",
        "impact resistance",
        "scratch resistance",
        "flexibility",
        "crack resistance",
        "thermal cycling",
        "uv resistance",
        "color retention",
        "chalking",
        "anti-slip",
        "non-skid",
        "slip-resistant",
        "fast cure",
        "low-temperature curing",
        "surface tolerant",
        "dry-dock",
        "cargo tank",
        "fuel resistance",
        "chemical resistance",
        "solvent resistance",
        "optical fiber",
        "optical fibre",
        "fiber coil",
        "fibre coil",
        "fiber ring",
        "fibre ring",
        "polarization-maintaining fiber",
        "polarization maintaining fiber",
        "pm fiber",
        "fiber-optic gyroscope",
        "fiber optic gyroscope",
        "fog",
        "primary coating",
        "secondary coating",
        "coloring coating",
        "colored ink",
        "ribbon coating",
        "matrix coating",
        "uv curing",
        "adhesive",
        "hyperedge",
    ]
    chinese_signals = [
        "涂料",
        "涂层",
        "油漆",
        "底漆",
        "树脂",
        "环氧",
        "聚氨酯",
        "富锌",
        "锌粉",
        "盐雾",
        "防腐",
        "腐蚀",
        "基材",
        "冷轧钢",
        "附着力",
        "光泽",
        "硬度",
        "耐候",
        "测试",
        "试验",
        "专利",
        "证据",
        "应用场景",
        "配方",
        "有机硅",
        "聚硅氧烷",
        "硅氧烷",
        "硅树脂",
        "硅烷",
        "船舶",
        "海洋",
        "海工",
        "甲板",
        "压载舱",
        "外板",
        "水线区",
        "油舱",
        "货舱",
        "船坞",
        "坞修",
        "船厂",
        "浪溅区",
        "防生物",
        "防生物附着",
        "抗生物附着",
        "防海生物附着",
        "生物污损",
        "污损生物",
        "耐水",
        "起泡",
        "白化",
        "湿附着",
        "拉开",
        "划格",
        "层间附着",
        "重涂",
        "旧漆",
        "耐磨",
        "抗冲击",
        "抗刮擦",
        "柔韧",
        "开裂",
        "冷热循环",
        "焊缝",
        "边角",
        "抗紫外",
        "保光",
        "保色",
        "粉化",
        "黄变",
        "防滑",
        "湿态防滑",
        "快干",
        "低温固化",
        "潮湿表面",
        "表面容忍",
        "耐燃油",
        "化学品",
        "光纤",
        "光纤环",
        "保偏光纤",
        "光纤陀螺",
        "涂覆",
        "涂覆材料",
        "胶粘剂",
        "胶黏剂",
        "着色油墨",
        "一次涂层",
        "二次涂层",
        "光纤带",
        "成带",
        "微弯",
        "固化收缩",
        "偏振",
    ]
    domain_hits = sum(1 for term in english_signals if term in lower)
    domain_hits += sum(1 for term in chinese_signals if term in question)
    if domain_hits >= 2:
        return True
    search_intents = ["where", "mentioned", "application", "test", "method", "evidence", "compare", "how many"]
    search_intents += ["哪里", "提到", "应用", "测试", "方法", "证据", "统计", "多少", "对比", "讲解", "解读", "介绍", "具体", "给一篇"]
    return domain_hits >= 1 and any(term in lower or term in question for term in search_intents)

def available_tools_for_router() -> list[dict[str, Any]]:
    return [
        {
            "name": "kg.expand_hyperedge_multihop",
            "description": (
                "Read-only Coating KG expansion. Use only when the user provides explicit hyperedge object_ids "
                "like HE_351_WO2019126498A1_0001 or DOC::HYP_..., or when a previous KG retrieval already "
                "returned object_ids. It expands object_ids into compact facts, evidence page/table/quote, and patent metadata. "
                "Do not use it for natural-language KG search."
            ),
            "input_schema": {
                "object_ids": ["hyperedge object_id strings"],
                "max_context_facts": 20,
                "max_evidence_per_item": 5,
            },
        },
        {
            "name": "kg.hybrid_search",
            "description": (
                "Read-only Coating KG natural-language search. Use for coating-domain questions about materials, "
                "substrates, properties, tests, examples, patents, performance, or evidence when the user does not "
                "already provide a hyperedge object_id. It returns ranked hyperedge object_ids and score metadata only; "
                "it does not expand evidence and does not answer the user."
            ),
            "input_schema": {
                "query": "natural-language coating KG search query",
                "top_k": KG_HYBRID_DEFAULT_TOP_K,
                "candidate_k": KG_HYBRID_DEFAULT_CANDIDATE_K,
                "filters": {
                    "doc_ids": [],
                    "properties": [],
                    "polarity": [],
                    "example_kind": [],
                    "qa_policy": "include_all",
                    "application_family": [],
                    "applications": [],
                    "substrates": [],
                    "test_methods": [],
                    "test_standards": [],
                    "material_roles": [],
                    "assignees": [],
                },
            },
        },
        {
            "name": "kg.doc_field_scan",
            "description": (
                "Read-only doc-scoped Coating KG field scan. Use when a hard doc scope is known and the user asks "
                "for facts, protocols, test conditions, formulations, layers, panels, materials, results, or evidence "
                "inside that one patent. It scans raw hyperedges, facts, and evidence fields without open top-k retrieval."
            ),
            "input_schema": {
                "doc_ids": ["WO publication or internal doc ids"],
                "query": "document-local fact/evidence question",
                "field_groups": DOC_FIELD_SCAN_GROUPS,
                "limit": 200,
                "include_evidence": True,
            },
        },
        {
            "name": "kg.sql_aggregate",
            "description": (
                "Read-only controlled-template Coating KG aggregation. Use for statistical questions such as "
                "how many distinct test methods, list all materials, group counts by assignee/property, or amount "
                "distributions. It returns compact aggregate rows with counts and examples; never generate raw SQL."
            ),
            "input_schema": {
                "intent": "distinct_count | list_distinct | group_count | numeric_distribution",
                "target": "test_method | property | material | material_role | substrate | assignee | doc_id | example_kind | polarity | amount | application_family | formulation",
                "filters": {
                    "doc_ids": [],
                    "properties": [],
                    "material_roles": [],
                    "assignees": [],
                    "application_family": [],
                    "polarity": [],
                    "example_kind": [],
                    "qa_policy": "include_all",
                },
                "group_by": [],
                "limit": 50,
                "include_examples": True,
            },
        },
    ]

def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(cleaned[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}

def sanitize_tool_routing(raw: dict[str, Any], question: str, *, router: str) -> dict[str, Any]:
    allowed = {tool["name"] for tool in available_tools_for_router()}
    calls: list[dict[str, Any]] = []
    for call in raw.get("calls", []):
        if not isinstance(call, dict):
            continue
        tool = str(call.get("tool") or "").strip()
        if tool not in allowed:
            continue
        query = str(call.get("query") or question).strip() or question
        cleaned_call = {
            "tool": tool,
            "query": query[:500],
            "reason": str(call.get("reason") or "").strip()[:300],
        }
        if tool == "kg.expand_hyperedge_multihop":
            object_ids = normalize_object_ids(call.get("object_ids"), f"{query}\n{question}")
            if not object_ids:
                continue
            cleaned_call["object_ids"] = object_ids[:20]
            cleaned_call["max_context_facts"] = int_or_default(call.get("max_context_facts"), 20)
            cleaned_call["max_evidence_per_item"] = int_or_default(call.get("max_evidence_per_item"), 5)
        elif tool == "kg.hybrid_search":
            cleaned_call["query"] = build_kg_search_query(query, question)[:500]
            cleaned_call["top_k"] = int_or_default(call.get("top_k"), KG_HYBRID_DEFAULT_TOP_K)
            cleaned_call["candidate_k"] = int_or_default(
                call.get("candidate_k"),
                KG_HYBRID_DEFAULT_CANDIDATE_K,
            )
            cleaned_call["filters"] = kg_search_filters_for_question(question, call.get("filters"))
            if "expand_top_k" in call:
                requested_expand_top_k = clamp_int(call.get("expand_top_k"), 0, 0, 8)
                if (
                    analyze_kg_query_semantics(question).get("guard")
                    or patent_explanation_request(question)
                    or patent_technology_search_request(question)
                ):
                    requested_expand_top_k = max(requested_expand_top_k, kg_expand_top_k_for_question(question))
                cleaned_call["expand_top_k"] = requested_expand_top_k
            else:
                cleaned_call["expand_top_k"] = kg_expand_top_k_for_question(question)
        elif tool == "kg.doc_field_scan":
            cleaned_call["doc_ids"] = normalize_string_list(call.get("doc_ids"))[:5]
            cleaned_call["field_groups"] = normalize_string_list(call.get("field_groups")) or DOC_FIELD_SCAN_GROUPS
            cleaned_call["limit"] = clamp_int(call.get("limit"), 200, 1, 500)
            cleaned_call["include_evidence"] = bool(call.get("include_evidence", True))
        elif tool == "kg.sql_aggregate":
            intent = str(call.get("intent") or infer_aggregate_intent(question)).strip()
            target = str(call.get("target") or infer_aggregate_target(question)).strip()
            if intent not in AGGREGATE_INTENTS:
                intent = infer_aggregate_intent(question)
            if target not in AGGREGATE_TARGETS:
                target = infer_aggregate_target(question)
            if intent == "numeric_distribution":
                target = "amount"
            cleaned_call["intent"] = intent
            cleaned_call["target"] = target
            cleaned_call["filters"] = merge_aggregate_domain_filters(question, call.get("filters"))
            group_by = normalize_string_list(call.get("group_by"))
            cleaned_call["group_by"] = [value for value in group_by if value in AGGREGATE_TARGETS][:3]
            if not cleaned_call["group_by"]:
                cleaned_call["group_by"] = aggregate_group_by_for_question(question, target, intent)
            cleaned_call["limit"] = clamp_int(call.get("limit"), 50, 1, 200)
            cleaned_call["include_examples"] = True
        calls.append(cleaned_call)
    confidence = raw.get("confidence", 0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "router": router,
        "needs_tools": bool(calls),
        "calls": calls[:3],
        "plan_type": infer_plan_type(calls[:3]),
        "confidence": max(0.0, min(1.0, confidence)),
        "answer_directly_reason": str(raw.get("answer_directly_reason") or "").strip()[:500],
    }

def fallback_tool_routing(question: str, *, reason: str = "keyword fallback") -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    object_ids = extract_hyperedge_object_ids(question)
    if object_ids:
        calls.append(
            {
                "tool": "kg.expand_hyperedge_multihop",
                "query": question,
                "object_ids": object_ids[:20],
                "max_context_facts": 20,
                "max_evidence_per_item": 5,
                "reason": "explicit hyperedge object_id fallback",
            }
        )
        return {
            "router": "fallback_keywords",
            "needs_tools": True,
            "calls": calls[:3],
            "plan_type": infer_plan_type(calls[:3]),
            "confidence": 0.9,
            "answer_directly_reason": "",
        }
    if wants_kg_aggregate(question):
        calls.append(build_aggregate_call(question))
        return {
            "router": "fallback_keywords",
            "needs_tools": True,
            "calls": calls[:3],
            "plan_type": infer_plan_type(calls[:3]),
            "confidence": 0.75,
            "answer_directly_reason": "",
        }
    if not object_ids and wants_coating_kg_search(question):
        calls.append(
            {
                "tool": "kg.hybrid_search",
                "query": build_kg_search_query(question),
                "top_k": KG_HYBRID_DEFAULT_TOP_K,
                "candidate_k": KG_HYBRID_DEFAULT_CANDIDATE_K,
                "filters": kg_search_filters_for_question(question),
                "expand_top_k": kg_expand_top_k_for_question(question),
                "reason": "coating KG natural-language search fallback",
            }
        )
    return {
        "router": "fallback_keywords",
        "needs_tools": bool(calls),
        "calls": calls[:3],
        "plan_type": infer_plan_type(calls[:3]),
        "confidence": 0.4 if calls else 0.0,
        "answer_directly_reason": "" if calls else reason,
    }

# OpenAI function-calling tool names cannot contain '.' — we map underscore
# names emitted to the LLM back to the internal dot-form when parsing tool_calls.
_OPENAI_TOOL_NAME_TO_INTERNAL = {
    "kg_hybrid_search": "kg.hybrid_search",
    "kg_sql_aggregate": "kg.sql_aggregate",
    "kg_doc_field_scan": "kg.doc_field_scan",
    "kg_expand_hyperedge_multihop": "kg.expand_hyperedge_multihop",
}


def _kg_tools_openai_format() -> list[dict[str, Any]]:
    """OpenAI/DeepSeek tools schema for the KG router (function calling)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "kg_hybrid_search",
                "description": (
                    "Coating KG natural-language search. Use for any coating-domain question "
                    "(materials, substrates, properties, test methods, examples, patents, "
                    "performance, evidence) when no explicit hyperedge object_id is given. "
                    "Returns ranked hyperedge object_ids. For Chinese questions include useful "
                    "English coating terms in the query. ALWAYS also set structured filters "
                    "when the user specifies a substrate, application, material role, property, "
                    "or assignee — empty filters means 'all of the KG'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "natural-language coating KG search query (mix Chinese + English coating terms)",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": f"number of top hits to return (default {KG_HYBRID_DEFAULT_TOP_K})",
                        },
                        "expand_top_k": {
                            "type": "integer",
                            "description": "expand evidence for this many top hits (6 if user wants an answer with evidence, 0 if user only wants IDs)",
                        },
                        "filters": {
                            "type": "object",
                            "description": (
                                "Structured filters that narrow retrieval. Fill any that the user "
                                "mentioned, e.g. user says '碳纤维基底' → substrates=['carbon fiber']; "
                                "'船舶/海洋' → application_family=['marine']; '富锌底漆' → "
                                "material_roles=['resin','zinc-rich primer']."
                            ),
                            "properties": {
                                "doc_ids": {"type": "array", "items": {"type": "string"}, "description": "specific patent doc_ids to restrict to"},
                                "substrates": {"type": "array", "items": {"type": "string"}, "description": "substrate / base material. Include likely spaced/unspaced, US/UK, and common word-order variants when applicable, e.g. ['cement fiber board','cement fiberboard','cement fibre board','cement fibreboard','fiber cement board','fiber cementboard','fibre cement board','fibre cementboard'] or ['carbon fiber','carbon fibre']"},
                                "application_family": {"type": "array", "items": {"type": "string"}, "description": "application domain, e.g. ['marine'], ['automotive'], ['aerospace']"},
                                "material_roles": {"type": "array", "items": {"type": "string"}, "description": "material role in formulation, e.g. ['resin'], ['pigment'], ['filler']"},
                                "properties": {"type": "array", "items": {"type": "string"}, "description": "performance properties, e.g. ['corrosion_protection'], ['adhesion']"},
                                "test_methods": {"type": "array", "items": {"type": "string"}, "description": "test methods, e.g. ['salt spray'], ['ASTM B117']"},
                                "test_standards": {"type": "array", "items": {"type": "string"}, "description": "test standards, e.g. ['ISO 12944']"},
                                "assignees": {"type": "array", "items": {"type": "string"}, "description": "patent assignee / company, e.g. ['Jotun']"},
                            },
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "kg_sql_aggregate",
                "description": (
                    "Statistical Coating KG aggregation. Use for 'how many', 'list all', 'count', "
                    "'distinct', 'grouped by', or numeric distribution questions. ALWAYS fill "
                    "structured filters when the user constrains by substrate, application, "
                    "property, material role, or assignee — empty filters counts the whole KG. "
                    "COUNTING UNIT decides target: '多少片 / 几篇 / 多少篇 / 多少专利 / how many patents' "
                    "counts patents → target='doc_id'; '多少配方 / 多少个 / 多少种 / how many formulations' "
                    "counts formulations → target='formulation'. Do not answer a patent-count question "
                    "with a formulation count. "
                    "Examples: '有多少以碳纤维为基底的配方' → target='formulation', "
                    "filters.substrates=['carbon fiber','carbon fibre']. Include spaced/unspaced variants for substrate names, "
                    "for example ['cement fiber board','cement fiberboard','cement fibre board','cement fibreboard',"
                    "'fiber cement board','fiber cementboard','fibre cement board','fibre cementboard']. "
                    "'多少片船舶防腐专利' → target='doc_id', filters.application_family=['marine']. "
                    "'多少船舶防腐配方' → target='formulation', filters.application_family=['marine']."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["distinct_count", "list_distinct", "group_count", "numeric_distribution"],
                        },
                        "target": {
                            "type": "string",
                            "enum": [
                                "test_method", "property", "material", "material_role",
                                "resin_system", "substrate", "assignee", "doc_id",
                                "example_kind", "polarity", "amount", "application_family", "formulation",
                            ],
                        },
                        "group_by": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "fields to group by (usually empty for distinct_count)",
                        },
                        "filters": {
                            "type": "object",
                            "description": (
                                "Structured filters that narrow the aggregation. Fill any that "
                                "the user mentioned. Without filters this counts the entire KG."
                            ),
                            "properties": {
                                "doc_ids": {"type": "array", "items": {"type": "string"}, "description": "specific patent doc_ids"},
                                "substrates": {"type": "array", "items": {"type": "string"}, "description": "substrate / base material. Include likely spaced/unspaced, US/UK, and common word-order variants when applicable, e.g. ['cement fiber board','cement fiberboard','cement fibre board','cement fibreboard','fiber cement board','fiber cementboard','fibre cement board','fibre cementboard'] or ['carbon fiber','carbon fibre']"},
                                "application_family": {"type": "array", "items": {"type": "string"}, "description": "application domain, e.g. ['marine'], ['automotive']"},
                                "material_roles": {"type": "array", "items": {"type": "string"}, "description": "material role, e.g. ['resin'], ['pigment']"},
                                "properties": {"type": "array", "items": {"type": "string"}, "description": "performance property, e.g. ['corrosion_protection']"},
                                "test_methods": {"type": "array", "items": {"type": "string"}, "description": "test method"},
                                "test_standards": {"type": "array", "items": {"type": "string"}, "description": "test standard, e.g. ['ISO 12944'], ['ASTM B117']"},
                                "assignees": {"type": "array", "items": {"type": "string"}, "description": "patent assignee / company"},
                            },
                        },
                    },
                    "required": ["intent", "target"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "kg_doc_field_scan",
                "description": (
                    "Doc-scoped Coating KG field scan. Use when a single patent is in scope and "
                    "the user asks for facts, protocols, test conditions, formulations, layers, "
                    "panels, materials, results, or evidence inside that patent."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "doc_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "list of patent doc_ids to scan (usually the active scope doc_ids)",
                        },
                        "query": {
                            "type": "string",
                            "description": "the user's question, used for field-relevance scoring",
                        },
                    },
                    "required": ["doc_ids", "query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "kg_expand_hyperedge_multihop",
                "description": (
                    "Expand explicit hyperedge object_ids into full evidence. Use ONLY when the "
                    "user gives explicit IDs like 'HE_351_WO2019126498A1_0001' or 'DOC::HYP_...', "
                    "or when prior KG retrieval already returned object_ids. Do not use for "
                    "natural-language KG search — use kg_hybrid_search instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "object_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "hyperedge object_id strings",
                        },
                    },
                    "required": ["object_ids"],
                },
            },
        },
    ]


def _router_system_prompt() -> str:
    """Minimal, behavior-only system prompt for function calling.

    Tool descriptions live in the `tools` field — this prompt only states
    when to use a tool vs answer directly, plus a few routing hints.
    """
    return (
        "You are a router for a coating and optical-fiber coating/adhesive materials knowledge-graph assistant.\n"
        "For ANY question about coatings, paints, primers, resins, fillers, pigments, "
        "substrates, performance, tests, patents, formulations, optical fibers, fiber coils, "
        "polarization-maintaining fibers, fiber-optic gyroscopes, coating materials, adhesives, or evidence — you MUST "
        "call a KG tool. Do not answer from your own knowledge.\n"
        "Only skip tools for: greetings, memory-only questions (\"我之前问过什么\"), or "
        "genuinely off-domain queries.\n"
        "Routing hints:\n"
        "- explicit hyperedge object_id (HE_xxx_xxx) → kg_expand_hyperedge_multihop\n"
        "- 'how many', 'list all', 'count', '统计', '多少', '几种' → kg_sql_aggregate\n"
        "- active scope on one patent + doc-local question (panel, layer, protocol) → kg_doc_field_scan\n"
        "- everything else coating-domain → kg_hybrid_search with expand_top_k=6\n"
        "For Chinese coating or optical-fiber material queries, include useful English search terms in the query argument."
    )


def _tool_call_to_internal_call(tool_call: dict[str, Any], question: str) -> dict[str, Any]:
    """Parse one OpenAI tool_call into the internal {tool, query, ...} format.

    The returned dict is later passed through sanitize_tool_routing which
    enforces param clamps and filter normalization, so partial/garbage
    arguments from the LLM are caught there.
    """
    fn = tool_call.get("function") or {}
    openai_name = str(fn.get("name") or "").strip()
    internal_name = _OPENAI_TOOL_NAME_TO_INTERNAL.get(openai_name)
    if internal_name is None:
        return {"tool": openai_name, "query": question, "reason": "unknown tool"}

    raw_args = fn.get("arguments") or "{}"
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = extract_json_object(raw_args)
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        args = {}

    call: dict[str, Any] = {"tool": internal_name, "reason": "llm function call"}
    if internal_name == "kg.hybrid_search":
        call["query"] = (str(args.get("query") or question)).strip() or question
        call["top_k"] = args.get("top_k") or KG_HYBRID_DEFAULT_TOP_K
        call["candidate_k"] = args.get("candidate_k") or KG_HYBRID_DEFAULT_CANDIDATE_K
        call["filters"] = args.get("filters") or {}
        if "expand_top_k" in args:
            call["expand_top_k"] = args["expand_top_k"]
    elif internal_name == "kg.sql_aggregate":
        call["intent"] = args.get("intent") or ""
        call["target"] = args.get("target") or ""
        call["filters"] = args.get("filters") or {}
        call["group_by"] = args.get("group_by") or []
        call["limit"] = args.get("limit") or 50
        call["include_examples"] = bool(args.get("include_examples", True))
        call["query"] = question
    elif internal_name == "kg.doc_field_scan":
        call["doc_ids"] = args.get("doc_ids") or []
        call["query"] = (str(args.get("query") or question)).strip() or question
        call["field_groups"] = args.get("field_groups") or DOC_FIELD_SCAN_GROUPS
        call["limit"] = args.get("limit") or 200
        call["include_evidence"] = bool(args.get("include_evidence", True))
    elif internal_name == "kg.expand_hyperedge_multihop":
        call["object_ids"] = args.get("object_ids") or []
        call["max_context_facts"] = args.get("max_context_facts") or 20
        call["max_evidence_per_item"] = args.get("max_evidence_per_item") or 5
        call["query"] = question
    return call


def route_tools_with_qwen(
    question: str,
    scope_hint: str | None = None,
    conv_hint: str | None = None,
) -> dict[str, Any]:
    """Call the LLM router with function-calling.

    Two optional context blocks, both injected as user-message prefix (NOT
    into the system prompt — that stays bit-identical so DeepSeek's automatic
    prompt cache stays warm at ~1792 tokens):

      - scope_hint: active patent scope summary (one line)
      - conv_hint: prior-turn context for short follow-ups ("好", "yes", etc.)
        — typically 1 prior user message + 1 prior assistant response, so the
        LLM can resolve confirmations into the underlying retry intent.

    Both prefixes are skipped when the hint string is None, keeping the user
    message bit-identical to the bare question — important for cache locality
    on long, self-contained queries.
    """
    cfg = provider_config()
    if not cfg["api_key"]:
        return fallback_tool_routing(question, reason="router model unavailable because API key is missing")

    prefix_parts: list[str] = []
    if conv_hint:
        prefix_parts.append(conv_hint)
    if scope_hint:
        prefix_parts.append(f"[Context: {scope_hint}]")
    user_content = ("\n\n".join(prefix_parts) + "\n\n" + question) if prefix_parts else question

    messages = [
        {"role": "system", "content": _router_system_prompt()},
        {"role": "user", "content": user_content},
    ]
    body = build_provider_body({**cfg, "enable_thinking": False}, messages, temperature=0, max_tokens=512, stream=False)
    body["tools"] = _kg_tools_openai_format()
    body["tool_choice"] = "auto"

    req = urllib.request.Request(
        provider_messages_url(cfg),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=provider_headers(cfg),
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        response = json.loads(resp.read().decode("utf-8"))

    choices = response.get("choices") or []
    message = (choices[0].get("message") if choices else None) or {}
    tool_calls = message.get("tool_calls") or []

    raw_decision: dict[str, Any] = {
        "needs_tools": bool(tool_calls),
        "calls": [_tool_call_to_internal_call(tc, question) for tc in tool_calls],
        "confidence": 0.95 if tool_calls else 0.5,
        "answer_directly_reason": "" if tool_calls else (message.get("content") or "")[:500],
    }
    decision = sanitize_tool_routing(raw_decision, question, router="llm-function-calling")
    decision["elapsed_ms"] = round((time.time() - started) * 1000)
    decision["model"] = cfg["model"]

    usage = response.get("usage") or {}
    decision["prompt_cache_hit_tokens"] = usage.get("prompt_cache_hit_tokens", 0)
    decision["prompt_cache_miss_tokens"] = usage.get("prompt_cache_miss_tokens", 0)
    decision["raw_router_text"] = (message.get("content") or "")[:500]
    return decision

def _record_route_decision(question: str, route: dict[str, Any]) -> None:
    append_jsonl(
        _app_value("TOOL_ROUTING", TOOL_ROUTING),
        {
            "id": f"route_{int(time.time() * 1000)}",
            "session_id": _app_value("SESSION_ID", SESSION_ID),
            "question": question,
            "decision": route,
            "created_at": now_iso(),
        },
    )

@dataclass
class RouterContext:
    question: str
    scope_resolution: dict[str, Any]
    routing_question: str
    contextualized_followup: bool
    fallback: dict[str, Any]


def _build_router_context(question: str) -> RouterContext:
    scope_resolution = resolve_scope(
        question, read_jsonl(_app_value("RAW_TURNS", RAW_TURNS)), load_scope_state()
    )
    routing_question = contextual_kg_followup_question(question)
    return RouterContext(
        question=question,
        scope_resolution=scope_resolution,
        routing_question=routing_question,
        contextualized_followup=routing_question != question,
        fallback=fallback_tool_routing(routing_question),
    )


def _build_conversational_hint(ctx: RouterContext) -> str | None:
    """Compact prior-turn context for short follow-ups.

    Why this exists: the routing layer historically only saw the bare current
    question + scope state. When the user replies with a generic confirmation
    ("好", "yes", "可以"), the router had no way to know what they were
    confirming, so it picked no tool and the answering layer's offer to retry
    looped indefinitely. This hint plugs that gap by feeding the LLM router
    one prior user message + one prior assistant response — enough context to
    resolve confirmations into the underlying retry intent.

    Cost discipline:
      - Only attaches for short messages (≤ 40 chars). Long, self-contained
        queries skip this and keep the user message identical to the bare
        question, preserving DeepSeek's prompt-cache hit pattern.
      - Trims prior turns to 200/300 chars to bound the cache miss size.
      - Returns None when there's no usable history (first turn, empty log).
    """
    bare = ctx.question.strip()
    if len(bare) > 40:
        return None

    turns = read_jsonl(_app_value("RAW_TURNS", RAW_TURNS))
    last_user: str | None = None
    last_assistant: str | None = None
    seen_current = False
    for turn in reversed(turns):
        role = turn.get("role")
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        if role == "user" and not seen_current and content == bare:
            seen_current = True
            continue
        if role == "assistant" and last_assistant is None:
            last_assistant = content
        elif role == "user" and last_user is None:
            last_user = content
        if last_user and last_assistant:
            break

    if not last_user and not last_assistant:
        return None

    lines: list[str] = ["[Prior conversation — use to interpret short follow-ups]"]
    if last_user:
        lines.append(f"prior user message: {last_user[:200]}")
    if last_assistant:
        snippet = last_assistant[:300]
        lines.append(f"prior assistant response: {snippet}")
    lines.append(
        "If the current message is a confirmation (好/可以/嗯/对/yes/ok/go ahead) or a short "
        "follow-up, resolve its intent from the prior user message + prior assistant response. "
        "If the assistant proposed a corrected retry (e.g. add a substrate or property filter), "
        "emit that corrected tool_call now."
    )
    return "\n".join(lines)


def _build_scope_hint(ctx: RouterContext) -> str | None:
    """Compact one-line scope hint for the LLM router (goes into user message,
    not system prompt — see route_tools_with_qwen for the caching rationale).

    Returns None when there's no active scope, so the user message stays
    bit-identical to the bare question and the LLM has no extra noise.
    """
    action = ctx.scope_resolution.get("scope_action")
    if action not in {"use_active", "set_new", "use_history"}:
        return None
    doc_ids = ctx.scope_resolution.get("doc_ids") or []
    if not doc_ids:
        return None
    joined = ", ".join(doc_ids)
    return (
        f"active patent scope = [{joined}]. "
        f"For doc-local questions (panel, layer, protocol, formulation, test result inside this patent), "
        f"prefer kg_doc_field_scan with doc_ids=[{joined}]. "
        f"For questions about other patents, the user has explicitly broken out of scope already; "
        f"prefer kg_hybrid_search."
    )


def _check_keyword_routes(ctx: RouterContext) -> dict[str, Any] | None:
    """Deterministic short-circuits — only for cases that the LLM cannot
    meaningfully improve on or where calling it would be wasteful.

      1. clarify_scope: pure state-machine decision. The router state already
         decided this needs a user follow-up; no tool to choose.
      2. scope_update: "只看 WO2014202495A1" is a scope command, no tool.
      3. explicit hyperedge object_id (HE_xxx pattern): deterministic regex,
         giving this to the LLM wastes ~2s on a known-correct decision.

    Everything else — aggregate intent, doc-scoped questions, unresolved
    pronouns, greetings, off-domain chat — falls through to the LLM router.
    The LLM gets an active-scope hint via _build_scope_hint(), so it can
    properly select kg_doc_field_scan when a single patent is in scope.

    Prompt caching keeps the LLM router at ~1-2s for cache-hit cases, so
    the latency cost of "always ask the LLM" is small.
    """
    if ctx.scope_resolution.get("scope_action") == "clarify":
        return clarify_scope_route(ctx.question, ctx.scope_resolution)
    if should_use_doc_field_scan(ctx.routing_question, ctx.scope_resolution):
        return doc_field_scan_route(ctx.routing_question, ctx.scope_resolution)
    if ctx.scope_resolution.get("scope_action") in {"set_new", "use_history"} and doc_scope_only_statement(ctx.question):
        return scope_update_route(ctx.question, ctx.scope_resolution)
    if unresolved_doc_local_reference(ctx.question, ctx.scope_resolution):
        # User said "这篇 / this patent" without setting scope — LLM can't resolve
        # this either (no doc_ids to fill). Ask the user. UX guard, not tool routing.
        return missing_doc_scope_route(ctx.question)
    fallback_calls = ctx.fallback.get("calls") or []
    if any(call.get("tool") == "kg.expand_hyperedge_multihop" for call in fallback_calls):
        return apply_scope_to_route(ctx.fallback | {"router": "explicit_hyperedge_object_id"}, ctx.scope_resolution)
    return None


def _run_llm_with_tools(ctx: RouterContext) -> dict[str, Any]:
    """LLM-led routing for cases that fell through the keyword short-circuits.

    The LLM decision wins when it picks a tool. Two narrow overrides remain
    (down from 4 — the aggregate-override was dead code after the keyword
    short-circuit already returned it):

      1. contextualized_followup: when the user's short followup ("再给我几篇")
         only makes sense via prior turn context, fallback already built the
         expanded KG search; if the LLM (seeing only the bare followup) said
         "no tool", trust the fallback's contextual rewrite.
      2. low-confidence LLM answer-direct: if the LLM returned no tool_calls
         (confidence=0.5 in the new function-calling path) but fallback has
         a KG call, prefer the KG call. This catches the "LLM confidently
         answers from its own knowledge" failure mode.
    """
    scope_hint = _build_scope_hint(ctx)
    conv_hint = _build_conversational_hint(ctx)
    try:
        decision = route_tools_with_qwen(ctx.question, scope_hint=scope_hint, conv_hint=conv_hint)
    except Exception as exc:  # noqa: BLE001
        # EMERGENCY: LLM API unreachable — keyword fallback keeps the demo alive.
        router_name = (
            "fallback_emergency_after_llm_error_contextual"
            if ctx.contextualized_followup and ctx.fallback.get("calls")
            else "fallback_emergency_after_llm_error"
        )
        return ctx.fallback | {"router": router_name, "router_error": str(exc)}

    if ctx.contextualized_followup and ctx.fallback.get("calls"):
        return ctx.fallback | {"router": "fallback_contextual_followup_preferred", "qwen_decision": decision}
    if decision.get("calls"):
        return decision
    if ctx.fallback.get("calls") and decision.get("confidence", 0) < 0.7:
        return ctx.fallback | {"router": "fallback_keywords_after_low_confidence_qwen", "qwen_decision": decision}
    return decision


def _safety_net(ctx: RouterContext, route: dict[str, Any]) -> dict[str, Any]:
    """Post-router rescue: if the LLM returned no tool calls but the user
    clearly wants KG (aggregate intent or doc-scoped question under active
    scope), still hit the KG via the keyword fallback path.
    """
    if not route.get("calls") and wants_kg_aggregate(ctx.question):
        route = ctx.fallback | {"router": "fallback_aggregate_after_router_miss"}
    if not route.get("calls") and should_force_scoped_kg_search(ctx.question, ctx.scope_resolution):
        return scoped_kg_search_route(ctx.question, ctx.scope_resolution)
    return apply_scope_to_route(route, ctx.scope_resolution)


def _decide_route(question: str) -> dict[str, Any]:
    ctx = _build_router_context(question)
    keyword_route = _check_keyword_routes(ctx)
    if keyword_route is not None:
        return keyword_route
    llm_route = _run_llm_with_tools(ctx)
    return _safety_net(ctx, llm_route)

def route_tools(question: str) -> dict[str, Any]:
    route = _decide_route(question)
    _record_route_decision(question, route)
    return route
