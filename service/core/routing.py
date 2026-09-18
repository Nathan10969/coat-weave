from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from answering import build_provider_body, extract_provider_text, provider_config, provider_headers, provider_messages_url
from kg_contract import load_tool_contract, normalize_filter_request, openai_tool_parameters
from demo_config import (
    KG_HYBRID_DEFAULT_CANDIDATE_K,
    KG_HYBRID_DEFAULT_TOP_K,
    RAW_TURNS,
    SESSION_ID,
    TOOL_OBSERVATIONS,
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
    _app_value,
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
    last_scope_event = state.get("last_scope_event") if isinstance(state.get("last_scope_event"), dict) else {}
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

    if has_any(question, PREVIOUS_SCOPE_TERMS) and history and not last_scope_event:
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

    recovered_doc_ids = (
        recent_doc_ids_for_doc_followup(question, recent_turns)
        if not active and not last_scope_event
        else []
    )
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
                updated["route_adjustments"] = list(
                    dict.fromkeys([*(updated.get("route_adjustments") or []), "hard_doc_scope_injected"])
                )
            elif scope_resolution.get("scope_action") == "clear_global":
                filters["doc_ids"] = []
                updated["route_adjustments"] = list(
                    dict.fromkeys([*(updated.get("route_adjustments") or []), "doc_scope_cleared_by_event"])
                )
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
    explicit_doc_ids = extract_doc_ids(question)
    route = {
        "router": "scope_guard_fallback",
        "needs_tools": True,
        "calls": [
            {
                "tool": "kg.hybrid_search",
                "query": build_kg_search_query(question),
                "top_k": KG_HYBRID_DEFAULT_TOP_K,
                "candidate_k": KG_HYBRID_DEFAULT_CANDIDATE_K,
                "filters": normalize_kg_search_filters({"doc_ids": explicit_doc_ids}),
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
    reference_terms = [*DOC_LOCAL_REFERENCE_TERMS, "this patent", "this doc", "this document", "里面"]
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

# Single source of truth for marine question wording. Union of the former five
# near-duplicate copies (KG_QUERY_REWRITE_CONCEPTS["marine"].triggers plus the
# inline tables in infer_aggregate_target, aggregate_filters_for_question,
# build_aggregate_call and wants_coating_kg_search). Kept as two constants
# because some call sites match EN terms against the lower()-cased question
# while ZH terms are matched against the raw question text.
MARINE_QUESTION_TERMS_ZH: tuple[str, ...] = (
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
)
MARINE_QUESTION_TERMS_EN: tuple[str, ...] = (
    "marine",
    "ship",
    "vessel",
    "hull",
    "naval",
    "offshore",
    "deck",
    "cargo hold",
    "cargo tank",
    "dry dock",
    "dry-dock",
    "shipyard",
    "splash zone",
)

# Single source of truth for zinc-rich(-primer) trigger wording. Union of the
# former zinc_rich / zinc_powder / zinc_rich_primer concept triggers plus the
# zinc_rich_primer_context() inline table. Also the head of
# ZINC_LOADING_TRIGGERS below.
ZINC_RICH_PRIMER_TRIGGERS: tuple[str, ...] = (
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
    "epoxy zinc",
)

KG_QUERY_REWRITE_CONCEPTS: dict[str, dict[str, tuple[str, ...]]] = {
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
        "triggers": MARINE_QUESTION_TERMS_ZH + MARINE_QUESTION_TERMS_EN,
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
    # Merged former zinc_rich / zinc_powder / zinc_rich_primer concepts: their
    # trigger tuples were subsets of one another, so they always fired for the
    # same zinc questions; terms below are the union of all three.
    "zinc_rich_primer": {
        "triggers": ZINC_RICH_PRIMER_TRIGGERS,
        "terms": (
            "epoxy zinc-rich primer",
            "zinc-rich primer",
            "zinc dust",
            "zinc powder",
            "epoxy primer",
            "steel primer",
            "anti-corrosion primer",
            "zinc rich",
            "zinc-rich",
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
    *ZINC_RICH_PRIMER_TRIGGERS,
    "zinc loading",
    "zinc content",
    "zinc",
    "锌含量",
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

def rewrite_concept_active(question: str, concept: str) -> bool:
    config = KG_QUERY_REWRITE_CONCEPTS.get(concept, {})
    return has_any(question, config.get("triggers", ()))

def zinc_rich_primer_context(question: str) -> bool:
    return has_any(str(question or ""), ZINC_RICH_PRIMER_TRIGGERS)

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

def _empty_query_facets() -> dict[str, Any]:
    slot_names = (
        "substrates",
        "materials",
        "systems",
        "coating_types",
        "properties",
        "tests",
        "numeric_constraints",
    )
    return {
        "hard": {slot: [] for slot in slot_names},
        "soft": {slot: [] for slot in slot_names},
    }

def _append_facet_values(out: list[Any], values: tuple[Any, ...] | list[Any]) -> None:
    seen = {json.dumps(value, sort_keys=True, ensure_ascii=False) for value in out}
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)

def _numeric_constraint(kind: str, operator: str, value: float, unit: str) -> dict[str, Any]:
    return {"kind": kind, "operator": operator, "value": float(value), "unit": unit}

def analyze_query_facets(question: str) -> dict[str, Any]:
    text = str(question or "")
    lower = text.lower()
    facets = _empty_query_facets()
    hard = facets["hard"]

    cfrp_terms = (
        "cfrp",
        "pa-cf",
        "carbon fiber reinforced",
        "carbon fibre reinforced",
        "carbon-fiber reinforced",
        "carbon-fibre reinforced",
        "碳纤维复合",
        "碳纤维增强",
    )
    substrate_markers = (
        "substrate",
        "base material",
        "as substrate",
        "for substrate",
        "为基底",
        "为基材",
        "作为基底",
        "作为基材",
        "基底",
        "基材",
        "底材",
    )
    if has_any(lower, cfrp_terms) and has_any(lower, substrate_markers):
        _append_facet_values(
            hard["substrates"],
            ("CFRP", "carbon fiber reinforced plastic", "carbon fiber reinforced composite"),
        )

    zinc_rich_primer = zinc_rich_primer_context(text) or has_any(
        lower,
        (
            "epoxy zinc-rich primer",
            "zinc-rich epoxy primer",
            "zinc rich epoxy primer",
            "epoxy zinc rich primer",
        ),
    )
    if zinc_rich_primer:
        if has_any(lower, ("epoxy",)) or "环氧" in text:
            _append_facet_values(hard["systems"], ("epoxy",))
        else:
            _append_facet_values(facets["soft"]["systems"], ("epoxy",))
        _append_facet_values(hard["materials"], ("zinc powder", "zinc dust", "zinc pigment"))
        _append_facet_values(hard["coating_types"], ("primer",))

    if has_any(lower, ("salt spray", "salt fog", "astm b117", "iso 9227")) or any(
        term in text for term in ("盐雾", "盐霧")
    ):
        _append_facet_values(facets["soft"]["tests"], ("salt spray", "ASTM B117", "ISO 9227"))
    if has_any(lower, ("adhesion", "cross-cut", "cross cut", "pull-off", "pull off")) or "附着" in text:
        _append_facet_values(facets["soft"]["properties"], ("adhesion",))
    if has_any(lower, ("chemical immersion", "chemical resistance immersion", "seawater immersion")) or any(
        term in text for term in ("化学性浸泡", "耐化学性浸泡", "海水浸泡", "挂板海水")
    ):
        _append_facet_values(facets["soft"]["tests"], ("chemical immersion", "seawater immersion"))

    comparison_patterns = [
        (r"(?:超过|大于|高于|>|>=)\s*(\d+(?:\.\d+)?)\s*(?:小时|h|hr|hrs|hour|hours)", ">"),
        (r"(?:小于|低于|少于|<|<=)\s*(\d+(?:\.\d+)?)\s*(?:μm|um|micron|microns|微米)", "<"),
        (r"(?:小于|低于|少于|<|<=)\s*(\d+(?:\.\d+)?)\s*(?:mil|mils)", "<"),
    ]
    for pattern, operator in comparison_patterns:
        for match in re.finditer(pattern, lower, flags=re.IGNORECASE):
            value = float(match.group(1))
            unit_text = match.group(0).lower()
            if "mil" in unit_text:
                value *= 25.4
                unit = "um"
            elif any(token in unit_text for token in ("μm", "um", "micron", "微米")):
                unit = "um"
            else:
                unit = "h"
            if unit == "h" and (has_any(lower, ("salt spray", "salt fog", "astm b117", "iso 9227")) or "盐雾" in text):
                _append_facet_values(
                    hard["numeric_constraints"],
                    (_numeric_constraint("salt_spray_duration", operator, value, unit),),
                )
            elif unit == "um" and (has_any(lower, ("film thickness", "dft", "thickness")) or "膜厚" in text):
                _append_facet_values(
                    hard["numeric_constraints"],
                    (_numeric_constraint("film_thickness", operator, value, unit),),
                )
    return facets

def analyze_kg_query_semantics(question: str) -> dict[str, Any]:
    text = str(question or "")
    lower = text.lower()
    concepts: list[str] = []
    outcomes: list[str] = []
    operator = "unknown"

    if has_any(lower, ZINC_LOADING_TRIGGERS):
        concepts.append("zinc_loading")
    if has_any(lower, CORROSION_OUTCOME_TRIGGERS):
        outcomes.append("corrosion_protection")

    for candidate in ("replace", "decrease", "increase"):
        if has_any(lower, SEMANTIC_OPERATOR_TERMS[candidate]):
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

    facets = analyze_query_facets(question)
    hard_facets = facets.get("hard", {})
    has_hard_facets = any(bool(values) for values in hard_facets.values())
    return {
        "operator": operator,
        "concepts": concepts,
        "outcomes": outcomes,
        "expansion_terms": expansion_terms,
        "required_terms": required_terms,
        "query_facets": facets,
        "guard": bool("zinc_loading" in concepts and operator in ZINC_LOADING_EXPANSION_TERMS) or has_hard_facets,
    }

def hard_epoxy_zinc_primer_facets(facets: dict[str, Any]) -> bool:
    hard_facets = facets.get("hard") if isinstance(facets.get("hard"), dict) else {}
    hard_materials = {str(value).lower() for value in hard_facets.get("materials", [])}
    hard_systems = {str(value).lower() for value in hard_facets.get("systems", [])}
    hard_coating_types = {str(value).lower() for value in hard_facets.get("coating_types", [])}
    return bool(
        hard_materials & {"zinc powder", "zinc dust", "zinc pigment"}
        and "epoxy" in hard_systems
        and "primer" in hard_coating_types
    )

def build_kg_search_query(query: str, original_question: str = "") -> str:
    combined = f"{query}\n{original_question}".strip()
    lower = combined.lower()
    additions: list[str] = []
    seen: set[str] = set()
    pre_semantics = analyze_kg_query_semantics(combined)
    if hard_epoxy_zinc_primer_facets(pre_semantics.get("query_facets", {})):
        return (
            "epoxy zinc-rich primer zinc powder zinc dust zinc pigment "
            "steel primer anti-corrosion primer formulation composition "
            "recipe table component amount parts by weight wt%"
        )
    marine_context_active = rewrite_concept_active(combined, "marine")
    for concept_name, concept in KG_QUERY_REWRITE_CONCEPTS.items():
        if concept_name in MARINE_CONTEXT_REQUIRED_REWRITE_CONCEPTS and not marine_context_active:
            continue
        if not has_any(combined, concept["triggers"]):
            continue
        append_unique_terms(additions, seen, concept["terms"], lower)
    semantics = pre_semantics
    append_unique_terms(additions, seen, semantics.get("expansion_terms", []), lower)
    facets = semantics.get("query_facets") if isinstance(semantics.get("query_facets"), dict) else {}
    hard_facets = facets.get("hard") if isinstance(facets.get("hard"), dict) else {}
    for slot in ("substrates", "materials", "systems", "coating_types", "tests"):
        append_unique_terms(additions, seen, hard_facets.get(slot, []), lower)
    for constraint in hard_facets.get("numeric_constraints", []):
        if not isinstance(constraint, dict):
            continue
        if constraint.get("kind") == "salt_spray_duration":
            append_unique_terms(additions, seen, ("salt spray duration", "test hours"), lower)
        elif constraint.get("kind") == "film_thickness":
            append_unique_terms(additions, seen, ("film thickness", "dry film thickness"), lower)
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
    asks_corrosion = has_any(
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
    explicit_scope = has_any(lower, ("only", "strictly", "filter", "must be", "property=")) or any(
        term in question for term in ["只看", "仅看", "只要", "限定", "限于", "必须是", "明确"]
    )
    return asks_corrosion and explicit_scope

def kg_search_filters_for_question(question: str, raw_filters: Any = None) -> dict[str, Any]:
    filters = normalize_kg_search_filters(raw_filters)
    facets = analyze_query_facets(question)
    hard_facets = facets.get("hard", {})
    hard_zinc_primer = hard_epoxy_zinc_primer_facets(facets)
    append_unique_values(filters["substrates"], hard_facets.get("substrates", []))
    if hard_zinc_primer:
        # "Marine" is often conversation/application context, while zinc-rich
        # epoxy primer evidence may be stored as generic heavy-duty corrosion
        # protection. Do not let that soft context exclude direct material facts.
        filters["application_family"] = []
        # The LLM sometimes emits "zinc-rich primer" as a material role. It is a
        # coating/system facet, not a role, and narrows backend filtering wrongly.
        filters["material_roles"] = []
    if (
        not hard_zinc_primer
        and rewrite_concept_active(question, "marine")
        and "marine" not in {value.lower() for value in filters["application_family"]}
    ):
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
        return 30
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
        return 30
    if any(term in question for term in ["回答", "证据", "引用", "页", "表", "哪里", "提到", "测试", "试验", "结果", "方法", "对比", "推荐", "怎么样"]):
        return 30
    return 30

KG_TOOL_CONTRACT = load_tool_contract()
AGGREGATE_INTENTS = set(KG_TOOL_CONTRACT["aggregate"]["intents"])
AGGREGATE_TARGETS = set(KG_TOOL_CONTRACT["aggregate"]["targets"])
AGGREGATE_GROUP_BY = set(KG_TOOL_CONTRACT["aggregate"]["group_by"])

# Every entry below also appears in scope_state.GLOBAL_SCOPE_TERMS (head of the
# list). Kept as a separate, deliberately narrower subset: GLOBAL_SCOPE_TERMS
# drives resolve_scope's clear_global decision, while this tuple only detects
# "the KG/database as a container" questions. Do not extend it with the
# scope-clearing phrases ("所有专利", "换一篇", ...).
KG_GLOBAL_CONTAINER_TERMS = ("图谱", "知识图谱", "数据库", "KG", "kg", "全库")
# Curated proper subset of scope_state.DOCUMENT_LOCAL_TERMS: only the explicit
# ZH "this document" phrases. Not replaced by DOCUMENT_LOCAL_TERMS itself —
# that list also holds generic content words (里面/其中/method/result/...),
# which would flip the bare-"里(面)" guard in unresolved_doc_local_reference.
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
# NOTE: "其他专利"/"要其他专利"/"换一篇" also appear in scope_state.GLOBAL_SCOPE_TERMS
# with a DIFFERENT meaning (clear doc scope → go global). resolve_scope runs first
# and wins when both match; the entries here only mark short follow-up queries for
# query rewriting. Same words, different axes — do not merge the two lists.
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


PAGINATION_FOLLOWUP_TERMS = (
    "more",
    "next page",
    "continue",
    "\u66f4\u591a",
    "\u4e0b\u4e00\u9875",
    "\u7ee7\u7eed",
    "\u8fd8\u6709\u5417",
    "\u518d\u7ed9\u6211",
    "\u518d\u6765",
)


def pagination_followup_request(question: str) -> bool:
    text = str(question or "").strip().casefold()
    return bool(text) and len(text) <= 40 and any(term.casefold() in text for term in PAGINATION_FOLLOWUP_TERMS)


def pagination_route_from_observation(question: str, observation: dict[str, Any]) -> dict[str, Any]:
    result = observation.get("result") if isinstance(observation, dict) else {}
    result = result if isinstance(result, dict) else {}
    pagination = result.get("pagination") if isinstance(result.get("pagination"), dict) else {}
    next_offset = pagination.get("next_offset")
    if next_offset is None:
        return {
            "router": "deterministic_pagination_exhausted",
            "needs_tools": False,
            "calls": [],
            "plan_type": "answer_directly",
            "confidence": 1.0,
            "answer_directly_reason": "previous candidate pool has no next page",
        }
    call = {
        "tool": "kg.hybrid_search",
        "query": str(result.get("query") or question).strip() or question,
        "top_k": KG_HYBRID_DEFAULT_TOP_K,
        "candidate_k": KG_HYBRID_DEFAULT_CANDIDATE_K,
        "offset": max(0, int_or_default(next_offset, 0)),
        "filters": normalize_kg_search_filters(result.get("applied_filters")),
        "expand_top_k": kg_expand_top_k_for_question(question),
        "reason": "continue previous ranked candidate pool",
    }
    return {
        "router": "deterministic_candidate_pagination",
        "needs_tools": True,
        "calls": [call],
        "plan_type": infer_plan_type([call]),
        "confidence": 1.0,
        "answer_directly_reason": "",
    }


def latest_hybrid_search_observation() -> dict[str, Any] | None:
    rows = read_jsonl(_app_value("TOOL_OBSERVATIONS", TOOL_OBSERVATIONS))
    for row in reversed(rows):
        result = row.get("result") if isinstance(row, dict) else None
        if row.get("tool") == "kg.hybrid_search" and isinstance(result, dict) and isinstance(result.get("pagination"), dict):
            return row
    return None

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
    # NOTE: deliberately NOT the full MARINE_QUESTION_TERMS union — this branch
    # decides the counting TARGET, and broad marine context words (甲板/货舱/
    # 船厂...) must not hijack material/substrate-count questions that merely
    # mention a marine setting. Keep the original narrow set.
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

# Fiber-domain slot mapping. These constants are shared by the domain-detection
# signal lists below AND the deterministic aggregate slot fallback, so detection
# and slot-filling can never disagree again about what counts as a fiber
# question (2026-06-12 incident: detection knew 光纤, the slot fallback did not,
# so an all-empty filter set counted the whole KG as "光纤专利 554 篇").
FIBER_QUESTION_TERMS_EN = [
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
]
FIBER_QUESTION_TERMS_ZH = [
    "光纤",
    "光纤环",
    "保偏光纤",
    "光纤陀螺",
]
def fiber_domain_question(question: str) -> bool:
    lower = str(question or "").lower()
    if any(term in lower for term in FIBER_QUESTION_TERMS_EN + ["fiber optic", "fibre optic"]):
        return True
    return any(term in question for term in FIBER_QUESTION_TERMS_ZH)


# Anaphora / continuation markers: a count question carrying one of these
# probably leans on the previous turn ("那按公司分呢", "其中多少是船舶的"),
# so the prior-turn router hint must be kept for it.
AGGREGATE_FOLLOWUP_MARKERS = ("那", "这", "其中", "它", "上面", "刚才", "之前", "继续", "再", "还", "也", "呢", "换", "改")


def aggregate_question_self_contained(question: str) -> bool:
    """True when a count question carries its own scope — no anaphora and
    either a deterministic domain filter or an explicit whole-KG phrasing.
    Only those questions skip the conversational hint; context-dependent
    count follow-ups keep it."""
    bare = str(question or "").strip()
    if any(marker in bare for marker in AGGREGATE_FOLLOWUP_MARKERS):
        return False
    filters = aggregate_filters_for_question(bare)
    if any(values for values in filters.values() if isinstance(values, list) and values):
        return True
    return kg_global_container_question(bare)


# The KG backend matches application_family by exact lowercase string equality
# (kg_expand_http_service.application_family_matches) — the lone exception is
# "marine", which has a hand-written alias set there. Any other value that is
# not byte-equal to a vocabulary value can never match and silently yields an
# empty result (2026-06-12 incident: application_family=['optical fiber'] → 0
# while the library holds optical_fiber_coating/optical_fiber_ribbon/...).
KG_FILTER_VOCAB_PATH = Path(
    os.environ.get(
        "COATING_KG_FILTER_VOCAB",
        str(Path(__file__).resolve().parent.parent / "config" / "kg_filter_vocab.json"),
    )
)
APPLICATION_FAMILY_EXACT_MATCH_EXEMPT = {"marine"}
_KG_FILTER_VOCAB_CACHE: tuple[float, dict[str, set[str]]] | None = None
_KG_FILTER_VOCAB_WARNED = False


def load_kg_filter_vocab() -> dict[str, set[str]]:
    """mtime-keyed cache: re-running scripts/export_kg_vocab.py takes effect
    without a service restart, and a missing/corrupt file degrades to an
    inactive guard (logged once) instead of breaking routing."""
    global _KG_FILTER_VOCAB_CACHE, _KG_FILTER_VOCAB_WARNED
    try:
        mtime = KG_FILTER_VOCAB_PATH.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _KG_FILTER_VOCAB_CACHE is not None and _KG_FILTER_VOCAB_CACHE[0] == mtime:
        return _KG_FILTER_VOCAB_CACHE[1]
    vocab: dict[str, set[str]] = {}
    if mtime >= 0:
        try:
            raw = json.loads(KG_FILTER_VOCAB_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        for dimension, values in (raw.get("dimensions") or {}).items():
            if isinstance(values, (dict, list)):
                vocab[dimension] = {str(v).strip().lower() for v in values if str(v).strip()}
    if not vocab.get("application_family") and not _KG_FILTER_VOCAB_WARNED:
        _KG_FILTER_VOCAB_WARNED = True
        print(
            f"[routing] kg_filter_vocab missing/empty at {KG_FILTER_VOCAB_PATH}; "
            "application_family vocabulary guard is INACTIVE — run scripts/export_kg_vocab.py",
            file=sys.stderr,
        )
    _KG_FILTER_VOCAB_CACHE = (mtime, vocab)
    return vocab


# Closed application_family enum for the router function-calling schema.
# Keys + descriptions come from config/application_family_enum.json (generated
# from the 2026-06-12 taxonomy; descriptions are probe-tested: 45 customer
# phrasings × 3 blind routers → 42/45 correct, 45/45 unanimous). The LLM picks
# from this list instead of inventing values, so router output is byte-equal
# to the remapped data and the backend's exact matcher never misses.
APPLICATION_FAMILY_ENUM_PATH = Path(
    os.environ.get(
        "COATING_APP_FAMILY_ENUM",
        str(Path(__file__).resolve().parent.parent / "config" / "application_family_enum.json"),
    )
)
_APP_FAMILY_ENUM_CACHE: tuple[float, list[dict[str, str]]] | None = None


def load_application_family_enum() -> list[dict[str, str]]:
    global _APP_FAMILY_ENUM_CACHE
    try:
        mtime = APPLICATION_FAMILY_ENUM_PATH.stat().st_mtime
    except OSError:
        mtime = -1.0
    if _APP_FAMILY_ENUM_CACHE is not None and _APP_FAMILY_ENUM_CACHE[0] == mtime:
        return _APP_FAMILY_ENUM_CACHE[1]
    families: list[dict[str, str]] = []
    if mtime >= 0:
        try:
            raw = json.loads(APPLICATION_FAMILY_ENUM_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        for item in raw.get("families") or []:
            key = str(item.get("key") or "").strip()
            description = str(item.get("description") or "").strip()
            if key:
                families.append({"key": key, "description": description})
    _APP_FAMILY_ENUM_CACHE = (mtime, families)
    return families


def application_family_schema_property() -> dict[str, Any]:
    """Schema fragment for filters.application_family: closed enum when the
    config is present, the legacy free-form shape when it is not (the P0
    vocabulary guard still backstops free-form values at sanitize time)."""
    families = load_application_family_enum()
    if not families:
        return {
            "type": "array",
            "items": {"type": "string"},
            "description": "application domain, e.g. ['marine'], ['automotive']",
        }
    return {
        "type": "array",
        "items": {"type": "string", "enum": [f["key"] for f in families]},
        "description": (
            "application domain family — CLOSED LIST, pick the key(s) whose description matches the question. "
            + " | ".join(f"{f['key']}: {f['description']}" for f in families)
        ),
    }


def validate_aggregate_filter_vocab(filters: dict[str, Any]) -> list[str]:
    """Post-route vocabulary guard. Values the backend's exact matcher can never
    match are demoted to substrates (the only dimension with normalized fuzzy
    matching), instead of silently producing a false-negative count.
    No-op when the vocabulary file is absent. Returns warnings for the trace."""
    known = load_kg_filter_vocab().get("application_family")
    if not known:
        return []
    kept: list[str] = []
    moved: list[str] = []
    for value in filters.get("application_family") or []:
        lowered = str(value).strip().lower()
        if lowered in APPLICATION_FAMILY_EXACT_MATCH_EXEMPT or lowered in known:
            kept.append(value)
        else:
            moved.append(value)
    if not moved:
        return []
    filters["application_family"] = kept
    append_unique_values(filters.setdefault("substrates", []), moved)
    return [f"application_family values not in vocabulary, retried as substrates: {moved}"]


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
    if any(term in lower for term in MARINE_QUESTION_TERMS_EN) or any(
        term in question for term in MARINE_QUESTION_TERMS_ZH
    ):
        filters["application_family"] = ["marine"]
    if fiber_domain_question(question):
        # Post-taxonomy: fiber questions map to the canonical family key, which
        # exact-matches the remapped data. The substrate-variant injection this
        # replaced (P0-2) is no longer needed and would over-constrain (AND
        # across dimensions) now that the family route exists.
        append_unique_values(filters["application_family"], ["optical_fiber"])
    if any(term in lower for term in ["resin", "binder"]) or "树脂" in question:
        filters["material_roles"] = ["resin"]
    elif any(term in lower for term in ["pigment", "pigments"]) or "颜料" in question:
        filters["material_roles"] = ["pigment"]
    elif any(term in lower for term in ["filler", "fillers"]) or "填料" in question:
        filters["material_roles"] = ["filler"]
    elif any(term in lower for term in ["additive", "additives"]) or "助剂" in question:
        filters["material_roles"] = ["additive"]
    return filters

def merge_aggregate_domain_filters(question: str, raw_filters: Any) -> dict[str, Any]:
    """Normalize aggregate filters to a stable, property-family-based signal.

    Historical background (2026-05, marine count-collapse fix): the LLM picked
    aggregate filters ad-hoc each turn, so "船舶防腐/防污" counts drifted and
    were wrong. At the time, naively unioning the curated marine families on
    top of the LLM's filters made it worse, because the KG backend combined
    the listed property_families conjunctively ([marine_antifouling,
    marine_fouling_release] collapsed to 0) and likewise ANDed
    property_families + application_family + the LLM's bare `properties`
    (corrosion → 1 instead of ~34). The design below dates from that fix;
    `property_canonical_ids_soft` is the OR-semantics channel chosen to
    express "any property in the family".

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
    other curated dims (material_roles etc.) by union, except for hard
    epoxy/zinc-rich/primer slot queries. In that case "marine" is soft context
    and "zinc-rich primer" is a coating-system facet, not a material role, so
    keeping those aggregate filters makes the count side-channel contradict the
    direct formulation search.
    """
    merged = normalize_kg_aggregate_filters(raw_filters)
    if hard_epoxy_zinc_primer_facets(analyze_query_facets(question)):
        merged["application_family"] = []
        merged["material_roles"] = []
        return merged

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
        if any(term in context_question.lower() for term in MARINE_QUESTION_TERMS_EN) or any(
            term in context_question for term in MARINE_QUESTION_TERMS_ZH
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
        "include_examples": intent != "distinct_count",
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
        *FIBER_QUESTION_TERMS_EN,
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
        *MARINE_QUESTION_TERMS_ZH,
        "防生物",
        "防生物附着",
        "抗生物附着",
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
        *FIBER_QUESTION_TERMS_ZH,
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

ROUTER_TOOL_NAMES = frozenset(KG_TOOL_CONTRACT["tools"])

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
    allowed = ROUTER_TOOL_NAMES
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
            cleaned_call["object_ids"] = object_ids[:30]
            cleaned_call["max_context_facts"] = int_or_default(call.get("max_context_facts"), 20)
            cleaned_call["max_evidence_per_item"] = int_or_default(call.get("max_evidence_per_item"), 5)
        elif tool == "kg.hybrid_search":
            cleaned_call["query"] = build_kg_search_query(query)[:500]
            cleaned_call["top_k"] = clamp_int(call.get("top_k"), KG_HYBRID_DEFAULT_TOP_K, 1, 50)
            cleaned_call["candidate_k"] = clamp_int(
                call.get("candidate_k"), KG_HYBRID_DEFAULT_CANDIDATE_K, cleaned_call["top_k"], 500
            )
            cleaned_call["offset"] = clamp_int(
                call.get("offset"),
                0,
                0,
                cleaned_call["candidate_k"],
            )
            receipt = normalize_filter_request(tool, call.get("filters"))
            cleaned_call["filters"] = receipt["effective_filters"]
            cleaned_call["requested_filters"] = receipt["requested_filters"]
            cleaned_call["unsupported_constraints"] = receipt["unsupported_constraints"]
            cleaned_call["route_adjustments"] = receipt["route_adjustments"]
            cleaned_call["expand_top_k"] = clamp_int(call.get("expand_top_k"), 30, 0, 30)
        elif tool == "kg.doc_field_scan":
            cleaned_call["doc_ids"] = normalize_string_list(call.get("doc_ids"))[:5]
            allowed_groups = set(KG_TOOL_CONTRACT["tools"][tool]["allowed_groups"])
            requested_groups = normalize_string_list(call.get("field_groups"))
            cleaned_call["field_groups"] = [group for group in requested_groups if group in allowed_groups]
            if not cleaned_call["field_groups"]:
                cleaned_call["field_groups"] = list(KG_TOOL_CONTRACT["tools"][tool]["default_groups"])
            cleaned_call["limit"] = clamp_int(call.get("limit"), 200, 1, 500)
            cleaned_call["include_evidence"] = bool(call.get("include_evidence", True))
        elif tool == "kg.sql_aggregate":
            intent = str(call.get("intent") or "").strip()
            target = str(call.get("target") or "").strip()
            if intent not in AGGREGATE_INTENTS:
                continue
            if target not in AGGREGATE_TARGETS:
                continue
            if intent == "numeric_distribution":
                target = "amount"
            cleaned_call["intent"] = intent
            cleaned_call["target"] = target
            receipt = normalize_filter_request(tool, call.get("filters"))
            cleaned_call["filters"] = receipt["effective_filters"]
            cleaned_call["requested_filters"] = receipt["requested_filters"]
            cleaned_call["unsupported_constraints"] = receipt["unsupported_constraints"]
            cleaned_call["route_adjustments"] = receipt["route_adjustments"]
            group_by = normalize_string_list(call.get("group_by"))
            cleaned_call["group_by"] = [value for value in group_by if value in AGGREGATE_GROUP_BY][:3]
            invalid_groups = [value for value in group_by if value not in AGGREGATE_GROUP_BY]
            if invalid_groups:
                cleaned_call["unsupported_constraints"] = sorted(
                    set(cleaned_call["unsupported_constraints"] + invalid_groups)
                )
            cleaned_call["limit"] = clamp_int(call.get("limit"), 50, 1, 200)
            cleaned_call["include_examples"] = bool(call.get("include_examples", intent != "distinct_count"))
        calls.append(cleaned_call)
    inherited_assignees: list[str] = []
    for call in calls:
        if call.get("tool") != "kg.hybrid_search":
            continue
        append_unique_values(inherited_assignees, (call.get("filters") or {}).get("assignees") or [])
    if inherited_assignees:
        for call in calls:
            if call.get("tool") != "kg.hybrid_search":
                continue
            filters = dict(call.get("filters") or {})
            if not filters.get("assignees"):
                filters["assignees"] = list(inherited_assignees)
            call["filters"] = filters
    deduped_calls: list[dict[str, Any]] = []
    seen_calls: set[str] = set()
    for call in calls:
        comparable = {key: value for key, value in call.items() if key not in {"reason", "warnings"}}
        call_key = json.dumps(comparable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if call_key in seen_calls:
            continue
        seen_calls.add(call_key)
        deduped_calls.append(call)
    calls = deduped_calls
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
                "object_ids": object_ids[:30],
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
    if wants_coating_kg_search(question):
        explicit_doc_ids = extract_doc_ids(question)
        filters = normalize_kg_search_filters({"doc_ids": explicit_doc_ids})
        calls.append(
            {
                "tool": "kg.hybrid_search",
                "query": question,
                "top_k": KG_HYBRID_DEFAULT_TOP_K,
                "candidate_k": KG_HYBRID_DEFAULT_CANDIDATE_K,
                "filters": filters,
                "expand_top_k": 30,
                "reason": "emergency coating KG search using the original question",
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
    descriptions = {
        "kg.hybrid_search": (
            "Coating KG natural-language search for materials, substrates, properties, tests, "
            "examples, patents, performance, and evidence. Preserve every user constraint in filters. "
            "For material names, include useful Chinese and English aliases in materials."
        ),
        "kg.sql_aggregate": (
            "Statistical Coating KG aggregation for counts, distinct lists, grouping, and numeric "
            "distributions. Patent counts use target=doc_id; formulation counts use target=formulation. "
            "Preserve every user constraint in filters."
        ),
        "kg.doc_field_scan": (
            "Scan structured fields inside one or more explicit patent documents for formulations, "
            "tests, materials, results, and evidence."
        ),
        "kg.expand_hyperedge_multihop": (
            "Expand explicit hyperedge object IDs returned by retrieval into facts and evidence."
        ),
    }
    tools: list[dict[str, Any]] = []
    for public_name, internal_name in _OPENAI_TOOL_NAME_TO_INTERNAL.items():
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": public_name,
                    "description": descriptions[internal_name],
                    "parameters": openai_tool_parameters(internal_name),
                },
            }
        )
    return tools


def _router_system_prompt() -> str:
    """Minimal, behavior-only system prompt for function calling.

    Tool descriptions live in the `tools` field — this prompt only states
    when to use a tool vs answer directly, plus a few routing hints.
    """
    return (
        "You are the tool router for a comprehensive coating-industry knowledge assistant.\n"
        "The assistant covers the full coating field: architectural, automotive OEM and refinish, marine, protective, "
        "industrial, powder, wood, packaging, coil, aerospace, electronics, optical-fiber coatings and adhesives, "
        "plus formulations, raw materials, substrates, properties, tests, patents, evidence, and related applications. "
        "Marine and optical-fiber coatings are specialist branches, not the default domain.\n"
        "For ANY professional coating-domain question, you MUST call a KG tool before answering. Do not answer professional "
        "facts from your own knowledge at the routing stage. Only skip tools for greetings, memory-only questions, or "
        "genuinely off-domain queries. For greetings, respond briefly as a comprehensive coating-industry knowledge assistant; "
        "do not foreground marine or optical-fiber coatings.\n"
        "Preserve conversational intent. A short follow-up must inherit the prior application, product, company, patent, "
        "performance target, and requested operation unless the user explicitly changes them. If the user asks you to choose "
        "a scenario, choose one consistent with the active conversation; never jump to another coating sector.\n"
        "Routing hints:\n"
        "- explicit hyperedge object_id (HE_xxx_xxx) -> kg_expand_hyperedge_multihop\n"
        "- 'how many', 'list all', 'count', '统计', '多少', '几种' -> kg_sql_aggregate\n"
        "- active scope on one patent + doc-local question (panel, layer, protocol) -> kg_doc_field_scan\n"
        "- everything else coating-domain -> kg_hybrid_search with expand_top_k=30\n"
        "For Chinese coating queries, include useful English search terms in the query argument."
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
    with urllib.request.urlopen(req, timeout=max(1, int(cfg.get("stream_read_timeout_seconds") or 60))) as resp:
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
    # When hints were injected, the full router input goes into the trace so
    # routing drift is replayable — the bare `question` field alone hid the
    # hint text that explained why identical questions routed differently.
    # Bare questions add nothing (question == user_content), so skip them to
    # keep the recorded decisions (and the packet built from them) small.
    if prefix_parts:
        decision["router_user_content"] = user_content[:600]

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
    if pagination_followup_request(ctx.question):
        previous = latest_hybrid_search_observation()
        if previous is not None:
            return pagination_route_from_observation(ctx.question, previous)
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
    # Self-contained aggregate questions skip the prior-turn hint: injecting
    # varying history made the SAME question route to different filter
    # dimensions across turns (2026-06-12 trace: 4 asks of "你有多少篇光纤专利"
    # produced 3 different filter sets). Context-dependent count follow-ups
    # ("那按公司分呢") keep the hint — they need the prior turn to make sense.
    suppress_hint = wants_kg_aggregate(ctx.question) and aggregate_question_self_contained(ctx.question)
    conv_hint = None if suppress_hint else _build_conversational_hint(ctx)
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
    if not route.get("calls") and wants_coating_kg_search(ctx.question):
        route = ctx.fallback | {"router": "fallback_hybrid_after_router_miss"}
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
