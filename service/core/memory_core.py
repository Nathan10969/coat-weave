from __future__ import annotations

import time
from typing import Any

from demo_config import DATA, DURABLE, GRAPH_EDGES, GRAPH_NODES, SESSION_ID, SUMMARY
from demo_storage import append_jsonl, now_iso, read_json, read_jsonl, write_json, write_jsonl
from demo_text import stable_id, tokenize
from graph_store import rebuild_graph_index


def ensure_seed_data() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    if not DURABLE.exists():
        seeds = [
            {
                "id": "mem_project_constraint_single_chat",
                "type": "project_constraint",
                "title": "客户 demo 只做单对话框记忆",
                "content": "当前 demo 面向客户演示，只考虑一个本地浏览器对话框，不接完整 MCP、hooks、team memory 或复杂 runtime。",
                "status": "active",
                "confidence": 0.95,
                "source": "pdf_and_agentmemory_discussion",
                "created_at": now_iso(),
                "last_confirmed_at": now_iso(),
                "supersedes": [],
            },
            {
                "id": "mem_workflow_agentmemory_borrow",
                "type": "workflow_rule",
                "title": "借鉴 agentmemory 的轻量骨架",
                "content": "借鉴 raw turns、compressed observations、session summary、typed durable memories、delta lifecycle 和 Dialogue Memory Packet；暂不借完整 product runtime。",
                "status": "active",
                "confidence": 0.92,
                "source": "agentmemory_reading",
                "created_at": now_iso(),
                "last_confirmed_at": now_iso(),
                "supersedes": [],
            },
            {
                "id": "mem_user_preference_chinese",
                "type": "user_preference",
                "title": "中文解释和可验证本地 demo",
                "content": "用户偏好中文解释、一点点对齐，并希望能在本地浏览器看到可测试的结果。",
                "status": "active",
                "confidence": 0.9,
                "source": "conversation",
                "created_at": now_iso(),
                "last_confirmed_at": now_iso(),
                "supersedes": [],
            },
            {
                "id": "mem_provider_openai_compatible_env",
                "type": "project_constraint",
                "title": "OpenAI-compatible 模型配置来自本地环境",
                "content": "本 demo 默认读取项目 coating_kg/.env 和 demo 本地 .env 中的 API key、base URL 与模型名，使用 OpenAI-compatible chat completions；demo 可通过本地 .env 覆盖模型、1M context 标记和 64K 最大输出。",
                "status": "active",
                "confidence": 0.95,
                "source": "local_project_env",
                "created_at": now_iso(),
                "last_confirmed_at": now_iso(),
                "supersedes": [],
            },
        ]
        for row in seeds:
            append_jsonl(DURABLE, row)
    if not SUMMARY.exists():
        write_json(
            SUMMARY,
            {
                "session_id": SESSION_ID,
                "goal": "基于综合涂料行业知识与专利知识图谱回答用户问题；船舶、汽车、建筑、工业和光纤涂层等均为平等子领域。",
                "current_state": "本地 demo 使用文件存储和 Cytoscape.js 图谱；默认读取 OpenAI-compatible 模型配置，未配置 key 时走 mock。",
                "decisions": [
                    "不直接集成完整 agentmemory 仓库。",
                    "先实现单对话框、轻量记忆分层和可视化图谱。",
                    "模型调用复用项目 coating_kg/.env 和 demo 本地 .env 的 OpenAI-compatible 配置。",
                ],
                "open_questions": [
                    "是否需要把这个 demo 迁移到 coating_kg 正式代码结构。",
                    "是否需要给 durable memories 增加 embedding 检索。",
                ],
                "completed_actions": [],
                "updated_at": now_iso(),
            },
        )
    if not GRAPH_NODES.exists() or not GRAPH_EDGES.exists():
        rebuild_graph_index()

def classify_observation(text: str) -> str:
    lower = text.lower()
    if any(w in text for w in ["记住", "偏好", "喜欢", "以后"]):
        return "preference"
    if any(w in text for w in ["决定", "方案", "不要", "先", "只做", "直接写入"]):
        return "decision"
    if any(w in lower for w in ["bug", "error", "fail", "wrong"]) or any(w in text for w in ["错误", "失败", "问题"]):
        return "known_issue"
    if any(w in text for w in ["流程", "步骤", "工作流", "怎么做"]):
        return "workflow"
    return "conversation"

def make_observation(turn: dict[str, Any]) -> dict[str, Any]:
    content = turn["content"].strip()
    obs_type = classify_observation(content)
    return {
        "id": f"obs_{int(time.time() * 1000)}",
        "session_id": SESSION_ID,
        "type": obs_type,
        "title": content[:48] + ("..." if len(content) > 48 else ""),
        "summary": content[:220] + ("..." if len(content) > 220 else ""),
        "entities": sorted(tokenize(content))[:12],
        "importance": 8 if obs_type in {"decision", "preference", "workflow", "known_issue"} else 5,
        "confidence": 0.75 if obs_type == "conversation" else 0.85,
        "source_turn_ids": [turn["id"]],
        "created_at": now_iso(),
    }

def maybe_promote_memory(obs: dict[str, Any]) -> dict[str, Any] | None:
    if obs["type"] not in {"decision", "preference", "workflow", "known_issue"}:
        return None
    memory_type = {
        "decision": "decision",
        "preference": "user_preference",
        "workflow": "workflow_rule",
        "known_issue": "known_issue",
    }[obs["type"]]
    return {
        "id": f"mem_{obs['id']}",
        "type": memory_type,
        "title": obs["title"],
        "content": obs["summary"],
        "status": "active",
        "confidence": obs["confidence"],
        "source": "auto_observation",
        "source_observation_ids": [obs["id"]],
        "created_at": now_iso(),
        "last_confirmed_at": now_iso(),
        "supersedes": [],
    }

def update_summary(user_text: str, assistant_text: str | None = None) -> dict[str, Any]:
    summary = read_json(SUMMARY, {})
    summary.setdefault("session_id", SESSION_ID)
    summary.setdefault("goal", "涂料专利知识图谱问答")
    summary.setdefault("current_state", "")
    summary.setdefault("decisions", [])
    summary.setdefault("open_questions", [])
    summary.setdefault("completed_actions", [])

    if any(w in user_text for w in ["决定", "先", "只做", "不要", "可以", "直接写入"]):
        item = user_text.strip()
        if item and item not in summary["decisions"]:
            summary["decisions"] = (summary["decisions"] + [item])[-8:]
    if "?" in user_text or "？" in user_text:
        if user_text not in summary["open_questions"]:
            summary["open_questions"] = (summary["open_questions"] + [user_text])[-6:]
    if assistant_text:
        summary["current_state"] = "最近一次回答已基于知识图谱检索与记忆包生成，并保存了对话记录。"
    summary["updated_at"] = now_iso()
    write_json(SUMMARY, summary)
    return summary

def relevant_memories(question: str, memories: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    q_tokens = tokenize(question)
    scored: list[tuple[float, dict[str, Any]]] = []
    for mem in memories:
        status = mem.get("status", "active")
        text = f"{mem.get('title', '')} {mem.get('content', '')} {mem.get('type', '')}"
        tokens = tokenize(text)
        overlap = len(q_tokens & tokens)
        base = 2.0 if status == "active" else -1.0
        confidence = float(mem.get("confidence", 0.5))
        score = base + overlap + confidence
        if status == "active" or overlap > 0:
            scored.append((score, mem))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:limit]]
