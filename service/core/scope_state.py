from __future__ import annotations

import sys
from typing import Any

from demo_config import SCOPE_STATE
from demo_storage import now_iso, read_json, write_json
from demo_text import normalize_string_list


def _app_value(name: str, default: Any) -> Any:
    app_module = sys.modules.get("app")
    return getattr(app_module, name, default) if app_module is not None else default


def default_scope_state() -> dict[str, Any]:
    return {"active_doc_scope": None, "scope_history": [], "last_scope_resolution": None}

def normalize_scope_state(value: Any) -> dict[str, Any]:
    state = value if isinstance(value, dict) else {}
    active = state.get("active_doc_scope")
    if not isinstance(active, dict) or not active.get("doc_ids"):
        active = None
    else:
        active = {
            **active,
            "doc_ids": normalize_string_list(active.get("doc_ids")),
            "policy": active.get("policy") or "hard",
        }
    history: list[dict[str, Any]] = []
    for item in state.get("scope_history") or []:
        if not isinstance(item, dict):
            continue
        doc_ids = normalize_string_list(item.get("doc_ids"))
        if not doc_ids:
            continue
        history.append({**item, "doc_ids": doc_ids, "policy": item.get("policy") or "hard"})
    return {
        "active_doc_scope": active,
        "scope_history": history[:10],
        "last_scope_resolution": state.get("last_scope_resolution"),
    }

def load_scope_state() -> dict[str, Any]:
    return normalize_scope_state(read_json(_app_value("SCOPE_STATE", SCOPE_STATE), default_scope_state()))

def save_scope_state(state: dict[str, Any]) -> None:
    write_json(_app_value("SCOPE_STATE", SCOPE_STATE), normalize_scope_state(state))

def has_any(text: str, terms: list[str]) -> bool:
    lower = text.lower()
    return any(term.lower() in lower for term in terms)

GLOBAL_SCOPE_TERMS = [
    "全库",
    "全部专利",
    "所有专利",
    "知识图谱",
    "图谱",
    "数据库",
    "KG",
    "kg",
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
    "给我10篇",
    "我要10篇",
    "不限制这篇",
    "不限定这篇",
    "跨专利",
    "要其他专利",
    "换一篇",
    "换其他",
    "其他的专利",
    "别的专利",
    "别的",
    "另一篇",
    "另外的专利",
    "另外一篇",
    "换别的",
    "换一个",
    "all patents",
    "global",
    "whole database",
    "entire database",
    "across patents",
    "another patent",
    "different patent",
    "more patents",
    "other patents",
]

PREVIOUS_SCOPE_TERMS = ["上一篇", "之前那篇", "前一篇", "previous patent", "last patent"]

DOCUMENT_LOCAL_TERMS = [
    "这篇",
    "这篇专利",
    "这份专利",
    "本篇",
    "该专利",
    "这个专利",
    "这个文献",
    "该文献",
    "里面",
    "其中",
    "panel",
    "second layer",
    "first layer",
    "dry-on-dry",
    "intercoat",
    "adhesion",
    "protocol",
    "test protocol",
    "test method",
    "test condition",
    "thermal exposure",
    "thermal cycling",
    "quench",
    "pass/fail",
    "result",
    "method",
    "condition",
    "pigment",
    "filler",
    "wt%",
    "第二层",
    "第一层",
    "这个配方",
    "该配方",
    "这个体系",
    "该体系",
    "哪一层",
    "哪个panel",
    "哪个 panel",
    "哪块板",
]

AMBIGUOUS_COMPARISON_TERMS = [
    "最好",
    "最佳",
    "哪种",
    "哪个更好",
    "推荐",
    "比较",
    "性能最好",
    "best",
    "recommend",
    "compare",
    "which coating",
    "which formulation",
]

def document_local_question(question: str) -> bool:
    return has_any(question, DOCUMENT_LOCAL_TERMS)

def ambiguous_global_comparison(question: str) -> bool:
    return has_any(question, AMBIGUOUS_COMPARISON_TERMS)

def make_doc_scope(doc_ids: list[str], *, source_turn_id: str | None = None, reason: str = "") -> dict[str, Any]:
    return {
        "type": "doc",
        "doc_ids": doc_ids,
        "policy": "hard",
        "source_turn_id": source_turn_id,
        "reason": reason,
        "updated_at": now_iso(),
    }

def latest_user_turn_id(recent_turns: list[dict[str, Any]]) -> str | None:
    for turn in reversed(recent_turns or []):
        if turn.get("role") == "user" and turn.get("id"):
            return str(turn["id"])
    return None

def make_scope_resolution(
    action: str,
    *,
    doc_ids: list[str] | None = None,
    reason: str = "",
    scope_policy: str = "hard",
) -> dict[str, Any]:
    return {
        "scope_action": action,
        "doc_ids": doc_ids or [],
        "scope_policy": scope_policy,
        "reason": reason,
        "resolved_at": now_iso(),
    }

def remember_scope_resolution(state: dict[str, Any], resolution: dict[str, Any]) -> dict[str, Any]:
    state = normalize_scope_state(state)
    state["last_scope_resolution"] = resolution
    save_scope_state(state)
    return state

def scope_applies_to_kg_search(scope_resolution: dict[str, Any]) -> bool:
    return scope_resolution.get("scope_action") in {"set_new", "use_active", "use_history"} and bool(
        scope_resolution.get("doc_ids")
    )
