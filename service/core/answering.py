from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

from demo_config import PROJECT_ROOT, ROOT
from stream_diagnostics import StreamDiagnostics


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def provider_config() -> dict[str, Any]:
    api_key = (
        os.environ.get("ZENMUX_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("QWEN_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    base_url = (
        os.environ.get("ZENMUX_BASE_URL")
        or os.environ.get("ANTHROPIC_BASE_URL")
        or os.environ.get("LLM_BASE_URL")
        or os.environ.get("DEEPSEEK_BASE_URL")
        or os.environ.get("QWEN_BASE_URL")
        or os.environ.get("DASHSCOPE_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    model = (
        os.environ.get("ZENMUX_MODEL")
        or os.environ.get("ANTHROPIC_MODEL")
        or os.environ.get("LLM_MODEL")
        or os.environ.get("DEEPSEEK_MODEL")
        or os.environ.get("QWEN_MODEL")
        or os.environ.get("QWEN_TEXT_MODEL")
        or "deepseek-v4-pro"
    )
    protocol = (
        os.environ.get("LLM_API_PROTOCOL")
        or os.environ.get("MODEL_API_PROTOCOL")
        or os.environ.get("ZENMUX_API_PROTOCOL")
        or ("anthropic" if "/anthropic" in base_url.lower() else "openai")
    ).strip().lower()
    context_window_tokens = int(os.environ.get("LLM_CONTEXT_WINDOW_TOKENS") or os.environ.get("QWEN_CONTEXT_WINDOW_TOKENS", "1000000"))
    max_output_tokens = int(os.environ.get("LLM_MAX_OUTPUT_TOKENS") or os.environ.get("QWEN_MAX_OUTPUT_TOKENS", "65536"))
    stream_chunk_chars = int(os.environ.get("LLM_STREAM_CHUNK_CHARS", "4"))
    stream_read_timeout_seconds = _int_env("LLM_STREAM_READ_TIMEOUT_SECONDS", 30)
    stream_first_token_timeout_seconds = _int_env("LLM_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS", 45)
    stream_content_idle_timeout_seconds = _int_env("LLM_STREAM_CONTENT_IDLE_TIMEOUT_SECONDS", 45)
    stream_total_timeout_seconds = _int_env("LLM_STREAM_TOTAL_TIMEOUT_SECONDS", 180)
    enable_thinking = (
        os.environ.get("DEEPSEEK_ENABLE_THINKING")
        or os.environ.get("QWEN_ENABLE_THINKING", "false")
    ).strip().lower() in {"1", "true", "yes", "on"}
    return {
        "api_key": api_key,
        "api_key_set": bool(api_key),
        "base_url": base_url,
        "model": model,
        "protocol": protocol,
        "context_window_tokens": context_window_tokens,
        "max_output_tokens": max_output_tokens,
        "stream_chunk_chars": stream_chunk_chars,
        "stream_read_timeout_seconds": stream_read_timeout_seconds,
        "stream_first_token_timeout_seconds": stream_first_token_timeout_seconds,
        "stream_content_idle_timeout_seconds": stream_content_idle_timeout_seconds,
        "stream_total_timeout_seconds": stream_total_timeout_seconds,
        "enable_thinking": enable_thinking,
        "env_sources": [str(PROJECT_ROOT / "coating_kg" / ".env"), str(ROOT / ".env")],
    }


def stream_timeout_reason(
    *,
    now: float,
    started_at: float,
    first_content_at: float | None,
    last_content_at: float,
    cfg: dict[str, Any],
) -> str | None:
    total = int(cfg.get("stream_total_timeout_seconds") or 0)
    if total > 0 and now - started_at >= total:
        return "total_timeout"
    if first_content_at is None:
        first_token = int(cfg.get("stream_first_token_timeout_seconds") or 0)
        if first_token > 0 and now - started_at >= first_token:
            return "first_token_timeout"
        return None
    content_idle = int(cfg.get("stream_content_idle_timeout_seconds") or 0)
    if content_idle > 0 and now - last_content_at >= content_idle:
        return "content_idle_timeout"
    return None


def stream_timeout_customer_message(reason: str, *, had_content: bool) -> str:
    labels = {
        "first_token_timeout": "首个内容等待超时",
        "content_idle_timeout": "内容流空闲超时",
        "total_timeout": "总时长超时",
    }
    label = labels.get(reason, "流式响应超时")
    if had_content:
        return f"\n\n[回答因{label}被截断。前面的内容已保留，可以继续追问“继续”。]"
    return f"模型流式响应因{label}中止，没有收到可用内容。可以稍后重试或换一个更窄的问题。"


def apply_thinking_config(body: dict[str, Any], cfg: dict[str, Any]) -> None:
    if cfg.get("protocol") == "anthropic":
        return
    enabled = bool(cfg.get("enable_thinking"))
    body["enable_thinking"] = enabled
    model = str(cfg.get("model") or "").lower()
    base_url = str(cfg.get("base_url") or "").lower()
    if not enabled and ("deepseek" in model or "deepseek" in base_url):
        body["thinking"] = {"type": "disabled"}


def provider_label(cfg: dict[str, Any]) -> str:
    return "anthropic-compatible" if cfg.get("protocol") == "anthropic" else "openai-compatible"


def provider_messages_url(cfg: dict[str, Any]) -> str:
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    if cfg.get("protocol") == "anthropic":
        return f"{base_url}/messages" if base_url.endswith("/v1") else f"{base_url}/v1/messages"
    return f"{base_url}/chat/completions"


def provider_headers(cfg: dict[str, Any]) -> dict[str, str]:
    api_key = str(cfg.get("api_key") or "")
    if cfg.get("protocol") == "anthropic":
        return {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }


def provider_supports_temperature(cfg: dict[str, Any]) -> bool:
    model = str(cfg.get("model") or "").lower()
    base_url = str(cfg.get("base_url") or "").lower()
    if "zenmux.ai" in base_url and model.startswith("anthropic/claude"):
        return False
    return cfg.get("protocol") != "anthropic"


def build_provider_body(
    cfg: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int | None = None,
    stream: bool = False,
) -> dict[str, Any]:
    token_limit = int(max_tokens or cfg.get("max_output_tokens") or 4096)
    if cfg.get("protocol") == "anthropic":
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, str]] = []
        for message in messages:
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")
            if role == "system":
                system_parts.append(content)
            elif role in {"user", "assistant"}:
                anthropic_messages.append({"role": role, "content": content})
            else:
                anthropic_messages.append({"role": "user", "content": content})
        body: dict[str, Any] = {
            "model": cfg["model"],
            "messages": anthropic_messages,
            "max_tokens": token_limit,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        if stream:
            body["stream"] = True
        return body
    body = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": token_limit,
    }
    if provider_supports_temperature(cfg):
        body["temperature"] = temperature
    if stream:
        body["stream"] = True
    apply_thinking_config(body, cfg)
    return body


def extract_provider_text(payload: dict[str, Any], cfg: dict[str, Any]) -> str:
    if cfg.get("protocol") == "anthropic":
        parts = []
        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("text") is not None:
                parts.append(str(block["text"]))
        return "".join(parts)
    return str(payload["choices"][0]["message"].get("content", ""))


def extract_stream_delta_text(payload: dict[str, Any], cfg: dict[str, Any]) -> str:
    if cfg.get("protocol") == "anthropic":
        delta = payload.get("delta") or {}
        if isinstance(delta, dict) and delta.get("text") is not None:
            return str(delta["text"])
        content_block = payload.get("content_block") or {}
        if isinstance(content_block, dict) and content_block.get("text") is not None:
            return str(content_block["text"])
        return ""
    choice = (payload.get("choices") or [{}])[0]
    delta = choice.get("delta") or {}
    return str(delta.get("content") or "")


def stream_delta_units(text: str, cfg: dict[str, Any]) -> Iterator[str]:
    max_chars = int(cfg.get("stream_chunk_chars") or 0)
    if max_chars <= 0 or len(text) <= max_chars:
        yield text
        return
    for idx in range(0, len(text), max_chars):
        yield text[idx : idx + max_chars]


def build_model_messages(question: str, packet: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "/no_think\n"
        "你是一个项目对话记忆助手。你会收到 Dialogue Memory Packet 和用户当前问题。"
        "请优先使用 active 记忆；不要把 stale 或 superseded 记忆当作当前事实。"
        "如果用户当前消息与记忆冲突，以用户当前消息为准，并简短指出记忆需要更新。"
        "用中文回答，回答要适合客户 demo：清楚、简洁、可验证。"
        "如果 Dialogue Memory Packet 里存在 status=ok 的 tool_observations，"
        "它们就是当前本地 demo 工具层的真实外部调用结果；请优先使用这些工具结果，"
        "不要把它们说成模拟，除非 tool_observations 的 status=error。"
        "如果工具是 kg.expand_hyperedge_multihop，请把它视为涂料 KG 的 compact evidence pack，"
        "回答时优先引用 item.doc_id、hyperedge_id、evidence.page、evidence.table、evidence.quote 和 unresolved。"
        "这个工具只展开已有 object_id，不代表已经完成自然语言 KG 检索。"
        "如果 Dialogue Memory Packet 里 active_scope.policy=hard，回答只能使用 active_scope.doc_ids 范围内的 KG 证据，"
        "如果 tool_observations 里有范围外 doc_id 的证据，必须忽略并说明是 scope mismatch。"
        "如果 last_scope_resolution.scope_action=clarify，请只提出范围确认问题，不要自行调用或想象 KG 结果。"
        "对于票价、余票、库存、价格等强实时交易信息，如果只有 web.search 结果而没有专用票务/交易 API，"
        "请基于搜索结果给出可核验来源链接和谨慎判断；不要直接拒绝联网，也不要编造搜索结果里没有的精确金额。"
    )
    system += (
        "Do not expose internal tool names, endpoint names, Dialogue Memory Packet, tool_observations, System Prompt, "
        "kg.hybrid_search, kg.sql_aggregate, kg.doc_field_scan, or kg.expand_hyperedge_multihop in customer-facing answers. "
        "Explain successful retrieval results as 我已查询到 or statistics as 我已统计到, but only when evidence actually exists. "
        "If coverage_status=no_structured_hyperedges_for_doc_ids or warnings include structured_doc_scan_no_hyperedges, "
        "docs_scanned only counts requested doc_ids; do not say 已查询到 or 已锁定 this patent, do not claim it is in the live KG, "
        "and instead say the current live structured KG has no structured evidence for that patent/doc_id. "
        "If the fallback same-doc search also returns zero items, say the patent is not currently searchable in the live KG; "
        "you may mention that this does not prove the original patent document does not exist outside the live KG.\n"
        "If the user already asks for a total/count, "
        "do not ask the customer whether to run a tool; use the available aggregate result. If no aggregate result "
        "is present, state that this run did not complete the statistic rather than recommending an internal tool.\n"
        "Never expose routing-state internals to the customer. Do not quote field names like scope_action, "
        "last_scope_resolution, scope_resolution, active_doc_scope, scope_history, scope_policy, policy=hard, "
        "router=, plan_type, needs_tools, fallback_keywords, fallback_direct_no_tool, qwen-json-router, or llm-json-router. "
        "If the current scope limits the answer (e.g. only one patent is active), say it in plain Chinese — for example "
        "\"我现在只在 WO2014202495A1 这一篇里找,如果你想看其他专利可以直接说\" — never dump JSON or technical labels, "
        "and never present a yes/no/option table asking the customer to authorize broadening scope.\n"
        "CRITICAL — you cannot retry tools mid-conversation. The current tool_observations are whatever the router "
        "produced for THIS user message. You must answer with what's available. NEVER offer phrases like "
        "\"需要我重新查询吗\", \"是否需要我重新统计\", \"要不要重新检索\", \"do you want me to retry\", or any "
        "yes/no question asking permission to re-run a tool — the customer saying \"好/yes\" cannot trigger a "
        "re-query, so offering creates a dead loop. If the current result is too broad (e.g. the aggregate "
        "returned all 18697 formulations but the user asked for a substrate-filtered subset), say plainly: "
        "\"我这次拿到的是全库总数,没按碳纤维基底过滤;请再问一遍并明确说『按碳纤维基底统计配方数量』,我下一轮会带上 "
        "substrates=['carbon fiber'] 这个筛选去查\" — give the user a concrete next phrasing to copy, do not ask "
        "them to confirm a retry.\n"
        "RESPECT THE COUNT THE USER ASKED FOR. If the user says \"给我一个/一篇/一种\" (one), present exactly ONE "
        "(the most representative) in full detail, then add at most one short line like \"库里还有 N 个相关的,需要可以继续\". "
        "Do not dump multiple full formulations when the user asked for one. If the user asks \"多少片/几篇\" (how many "
        "patents) answer with the patent COUNT (doc_id count), not the formulation count, and do not silently switch the "
        "counting unit. Keep the answered unit consistent with what the user counted.\n"
        "If tool_observations include kg.sql_aggregate, treat it as the only authoritative source for statistical "
        "counts, distinct counts, grouped counts, and numeric distributions. Use summary.distinct_count, "
        "summary.matched_hyperedges, item.count, item.doc_count, item.assignee_count, item.assignees, "
        "and item.examples when explaining the result. "
        "Never infer totals from kg.hybrid_search top-k candidates or text_preview.\n"
        "Only packet.tool_observations are authoritative for the current user message. "
        "packet.recent_tool_observations are historical context only; do not use them as current counts, "
        "candidate lists, or evidence unless the user explicitly asks about a previous/just-mentioned result. "
        "If the current message asks for a count/list and packet.tool_observations is empty, say the current "
        "run did not complete that statistic instead of reusing historical search results.\n"
        "If tool_observations include kg.hybrid_search, treat it as Coating KG candidate retrieval only: "
        "it gives ranked hyperedge object_ids, score_parts, metadata, and text_preview. "
        "Do not present kg.hybrid_search.text_preview as fully expanded evidence. "
        "If a later kg.expand_hyperedge_multihop observation exists for the same question, use that expanded evidence "
        "for page/table/quote citations. Do not cite page/table/quote from kg.hybrid_search alone; say that evidence "
        "expansion still requires kg.expand_hyperedge_multihop on selected object_ids when no expansion is present.\n"
        "If tool_observations include kg.doc_field_scan, treat it as the authoritative source for doc-scoped "
        "field lookup inside active_scope.doc_ids. Use items[].test_method, items[].test_condition, items[].result, "
        "items[].facts, and items[].evidence for the answer. If it returns empty with hyperedges_scanned>0, say the requested field was not "
        "found in the scoped patent evidence; do not broaden to other patents. If it returns empty with hyperedges_scanned=0, "
        "treat patent presence in the live structured KG as unverified/missing, not as a field-level negative result.\n"
        "If the current user message is a short follow-up or scope phrase (for example 只看这篇, 重新, 需要啊) "
        "and the current tool_observations include a result.query that expands the prior user intent, treat that "
        "result.query as the effective expanded tool query and answer the tool result directly. In this case, "
        "do not only acknowledge the scope; answer the expanded tool query using the returned evidence.\n"
        "If a kg.expand_hyperedge_multihop observation has semantic_guard.enabled=true and "
        "semantic_guard.direct_match=false, do not turn related coating evidence into a direct answer. "
        "Say the current expanded evidence did not directly satisfy the required semantic constraint, and separate "
        "related evidence from confirmed matches. If item_warnings mention missing_required_semantic_terms, treat those "
        "items as related candidates only.\n"
    )
    system += (
        "\nDomain role and customer-facing behavior:\n"
        "The project-memory instructions above describe context plumbing. In customer-facing answers, act as a "
        "materials R&D advisor for optical fiber, fiber coils, polarization-maintaining fiber, fiber-optic gyroscopes, "
        "and related coating materials and adhesives. Your users are R&D directors, materials engineers, optical-fiber "
        "process engineers, and product owners. Your goal is to turn patents, papers, standards, public product data, "
        "and internal KG evidence into traceable, judgment-oriented, verifiable R&D information, not search-engine prose.\n"
        "For fuzzy requests such as 给我一个光纤涂料, 找一个配方, 有没有类似材料, or 这个体系能不能做, first classify the likely "
        "application scenario instead of giving an isolated formula. Use these categories: Primary coating (glass-fiber "
        "inner flexible protection; low modulus, buffering, microbend loss, damp-heat reliability), Secondary coating "
        "(outer mechanical protection; high modulus, abrasion, hydrolysis resistance, cracking, draw-speed fit), "
        "Colored ink / Coloring coating (fiber identification; high-speed UV cure, adhesion, color fastness, strippability, "
        "ink thickness), Matrix / Ribbon coating (multi-fiber ribbon bonding; peelability, flexibility, cure shrinkage, "
        "long-term reliability), and fiber-ring / polarization-maintaining-fiber adhesives and coating materials "
        "(光纤环/保偏光纤相关胶粘剂与涂覆材料; winding, bonding, curing, skeleton treatment, "
        "post-treatment; stress control, polarization stability, thermal cycling, long-term drift, cure shrinkage, reliability). "
        "If the user did not specify the scenario, do not stop at 请补充信息. Give one default answer with a boundary sentence "
        "such as 我先按【某一类场景】给出一个证据完整的样本; 如果您关注一次涂层、二次涂层或光纤环胶粘剂, 可以再切换.\n"
        "For material, formulation, patent, or process questions, use this five-layer answer shape when useful: "
        "1. 结论先行 (2-4 sentences: whether a usable sample exists, material class, suitable problem, and unsuitable use); "
        "2. 证据样本 (patent/paper/standard, assignee/author, application, composition, process, performance, evidence level); "
        "3. 材料设计逻辑 (what each oligomer/resin/reactive diluent/photoinitiator/pigment/filler/additive solves, key trade-offs); "
        "4. 适用边界与风险 (whether it can be directly compounded, raw-material age/regulatory/supply risk, equipment-dependent "
        "process window, effects on optical loss, microbend, polarization holding, stress, and reliability, and missing data); "
        "5. 下一步验证路线 (small-sample plan, key variables, tests, go/no-go criteria, and minimal validation cost).\n"
        "Do not force that five-layer shape for simple statistics, patent counts, direct evidence extraction, ID lists, "
        "scope clarification, or short factual answers; answer those directly and concisely.\n"
        "Evidence level is mandatory when making a technical recommendation: A-level / A级证据 means patent examples, standard tests, "
        "paper experiments, or public company technical data with clear composition/process/performance; B-level / B级证据 means patent "
        "description, review, product note, or white paper with a clear direction but incomplete formula or test conditions; "
        "C-level / C级证据 means industry experience, reasonable analogy, or model inference. For C-level / C级证据, explicitly say: "
        "这是推断，不应直接作为配方或工艺决策依据.\n"
        "Write like an R&D consultant: do not only say 可以; say within what boundary it may work. Do not only say risk is high; "
        "say which variable creates the risk. Do not only list data; explain the judgment and trade-off.\n"
        "Do not provide unverified precise final formulations, do not promise that a material will certainly work, "
        "and do not treat patent examples as mature commercial products. Do not generalize optical-fiber coloring ink "
        "/ 光纤着色油墨 into primary/secondary coating / 一次/二次涂层; "
        "do not ignore substrate, coating thickness, cure conditions, or test conditions; do not claim a public patent can "
        "reproduce a customer product; do not give infringement, patent-circumvention, or competitor-copying advice; "
        "do not answer with tables only, always include R&D judgment.\n"
        "For environmental questions, remind the user to verify VOC, SDS, hazardous-chemical, waste-disposal, and "
        "local regulatory constraints. For patent questions, distinguish technical inspiration, competitor "
        "intelligence, and patent risk; do not make infringement conclusions.\n"
        "For regulatory or compliance questions, provide only source-backed technical/compliance screening. If the "
        "available packet lacks current legal/regulatory evidence, say that a final compliance conclusion cannot be "
        "made and list the missing documents or authoritative sources needed, such as SDS/TDS versions, active "
        "substance status, product authorization, AFS statements, PSPC/class approvals, test reports, and project "
        "records.\n"
    )
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "Dialogue Memory Packet:\n"
            + json.dumps(packet, ensure_ascii=False)
            + "\n\nCurrent user message:\n"
            + question,
        },
    ]


def scope_clarification_answer(packet: dict[str, Any]) -> str | None:
    resolution = packet.get("last_scope_resolution") if isinstance(packet, dict) else None
    if not isinstance(resolution, dict) or resolution.get("scope_action") != "clarify":
        return None
    doc_ids = ", ".join(resolution.get("doc_ids") or [])
    if not doc_ids:
        return "这里需要先确认一下：你是要在当前限定范围内比较，还是要切到全库/所有专利里比较？"
    return (
        f"这里需要先确认一下：你是要在当前 {doc_ids} 这篇里比较，"
        "还是要切到全库/所有专利里比较？我先不调用 KG，避免把当前篇和全库结果混在一起。"
    )


def local_mock_answer(question: str, packet: dict[str, Any]) -> str:
    clarification = scope_clarification_answer(packet)
    if clarification:
        return clarification
    bullets = []
    for mem in packet.get("relevant_memories", [])[:4]:
        bullets.append(f"- {mem.get('type')}: {mem.get('content')}")
    return (
        "【本地 mock 回复：未检测到 DeepSeek/DashScope/OpenAI-compatible API key】\n\n"
        "我会根据当前 Dialogue Memory Packet 回答。当前命中的记忆包括：\n"
        + ("\n".join(bullets) if bullets else "- 暂时没有命中的长期记忆。")
        + f"\n\n你的问题是：{question}\n\n"
        "这个 demo 已经保存了本轮 raw turn、生成 observation，并会在右侧图谱中显示记忆节点。"
    )


def sanitize_customer_answer(answer: str) -> str:
    """Keep implementation details out of the customer-facing chat surface."""
    text = str(answer or "")
    replacements = {
        "`kg.sql_aggregate`": "结构化统计结果",
        "kg.sql_aggregate": "结构化统计结果",
        "`kg.hybrid_search`": "候选检索结果",
        "kg.hybrid_search": "候选检索结果",
        "`kg.doc_field_scan`": "单篇证据扫描结果",
        "kg.doc_field_scan": "单篇证据扫描结果",
        "`kg.expand_hyperedge_multihop`": "证据展开结果",
        "kg.expand_hyperedge_multihop": "证据展开结果",
        "`tool_observations`": "本轮工具结果",
        "tool_observations": "本轮工具结果",
        "`Dialogue Memory Packet`": "当前上下文",
        "Dialogue Memory Packet": "当前上下文",
        "`System Prompt`": "系统指令",
        "System Prompt": "系统指令",
        "application_family": "应用领域",
        "target=formulation": "统计对象=配方",
        "target=test_method": "统计对象=测试方法",
        "property=corrosion_protection": "性能属性=corrosion_protection",
        "filter=": "筛选条件=",
        "distinct_count=": "去重数量=",
        "matched_hyperedges=": "匹配记录数=",
        # Routing-state internals — never surface these field names to the customer.
        "last_scope_resolution.scope_action": "",
        "last_scope_resolution": "",
        "scope_resolution": "",
        "scope_action=none": "",
        "scope_action=clarify": "",
        "scope_action=use_active": "",
        "scope_action=use_history": "",
        "scope_action=set_new": "",
        "scope_action=clear_global": "",
        "scope_action": "",
        "active_doc_scope": "当前文档",
        "scope_history": "",
        "scope_policy": "",
        "policy=hard": "",
        "qwen-json-router": "",
        "llm-json-router": "",
        "fallback_contextual_followup_preferred": "",
        "fallback_aggregate_preferred": "",
        "fallback_keywords_after_low_confidence_qwen": "",
        "fallback_keywords_after_qwen_error": "",
        "fallback_direct_no_tool": "",
        "fallback_keywords": "",
        "explicit_hyperedge_object_id": "",
        "needs_tools": "",
        "plan_type": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def stream_qwen(
    question: str,
    packet: dict[str, Any],
    *,
    diag_request_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    clarification = scope_clarification_answer(packet)
    cfg = provider_config()
    model = cfg["model"]
    if clarification:
        yield {
            "type": "delta",
            "content": sanitize_customer_answer(clarification),
            "provider": "scope-resolver",
            "model": model,
        }
        yield {"type": "done", "provider": "scope-resolver", "model": model}
        return

    api_key = cfg["api_key"]
    base_url = cfg["base_url"]
    if not api_key:
        answer = sanitize_customer_answer(local_mock_answer(question, packet))
        for idx in range(0, len(answer), 64):
            yield {
                "type": "delta",
                "content": answer[idx : idx + 64],
                "provider": "local-mock",
                "model": "mock-memory-demo",
            }
        yield {"type": "done", "provider": "local-mock", "model": "mock-memory-demo"}
        return

    body = build_provider_body(
        cfg,
        build_model_messages(question, packet),
        temperature=0.4,
        max_tokens=cfg["max_output_tokens"],
        stream=True,
    )
    req = urllib.request.Request(
        provider_messages_url(cfg),
        data=json.dumps(body).encode("utf-8"),
        headers=provider_headers(cfg),
        method="POST",
    )
    emitted = False
    plain_lines: list[str] = []
    started_at = time.monotonic()
    first_content_at: float | None = None
    last_content_at = started_at
    timeout_reason: str | None = None
    # observation-only; never raises. When A/B thread diag_request_id down, all three
    # layers share one id (robust correlation); else auto-id + thread_id/ts fallback.
    diag = StreamDiagnostics(request_id=diag_request_id, conversation_id="", layer="core.answering.stream_qwen")
    diag.event("upstream_read_start")
    try:
        with urllib.request.urlopen(req, timeout=max(1, int(cfg.get("stream_read_timeout_seconds") or 30))) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    diag.line_event("empty_line")
                    timeout_reason = stream_timeout_reason(
                        now=time.monotonic(),
                        started_at=started_at,
                        first_content_at=first_content_at,
                        last_content_at=last_content_at,
                        cfg=cfg,
                    )
                    if timeout_reason:
                        break
                    continue
                if not line.startswith("data:"):
                    diag.line_event("non_data")
                    plain_lines.append(line)
                    timeout_reason = stream_timeout_reason(
                        now=time.monotonic(),
                        started_at=started_at,
                        first_content_at=first_content_at,
                        last_content_at=last_content_at,
                        cfg=cfg,
                    )
                    if timeout_reason:
                        break
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    diag.line_event("data_done")
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    diag.line_event("data_unparseable")
                    continue
                content = extract_stream_delta_text(payload, cfg)
                diag.line_event("data_content" if content else "data_no_content")
                if content:
                    now = time.monotonic()
                    if first_content_at is None:
                        first_content_at = now
                    last_content_at = now
                    emitted = True
                    for unit in stream_delta_units(sanitize_customer_answer(content), cfg):
                        yield {
                            "type": "delta",
                            "content": unit,
                            "provider": provider_label(cfg),
                            "model": payload.get("model") or model,
                        }
                timeout_reason = stream_timeout_reason(
                    now=time.monotonic(),
                    started_at=started_at,
                    first_content_at=first_content_at,
                    last_content_at=last_content_at,
                    cfg=cfg,
                )
                if timeout_reason:
                    break
        if timeout_reason:
            diag.event(
                "upstream_timeout",
                kind=timeout_reason,
                emitted=emitted,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            yield {
                "type": "delta",
                "content": stream_timeout_customer_message(timeout_reason, had_content=emitted),
                "provider": provider_label(cfg),
                "model": model,
            }
            yield {"type": "done", "provider": provider_label(cfg), "model": model}
            return
        if not emitted and plain_lines:
            payload = json.loads("\n".join(plain_lines))
            content = extract_provider_text(payload, cfg)
            answer = sanitize_customer_answer(content)
            if answer:
                for unit in stream_delta_units(answer, cfg):
                    yield {
                        "type": "delta",
                        "content": unit,
                        "provider": provider_label(cfg),
                        "model": payload.get("model") or model,
                    }
        diag.event("upstream_loop_done", emitted=emitted)
        yield {"type": "done", "provider": provider_label(cfg), "model": model}
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError, json.JSONDecodeError) as exc:
        diag.event("upstream_exception", error=str(exc))
        yield {
            "type": "delta",
            "content": f"妯″瀷 API 娴佸紡璋冪敤澶辫触锛屽凡淇濈暀鏈湴璁板繂閾捐矾銆傞敊璇細{exc}",
            "provider": "openai-compatible-error",
            "model": model,
        }
        yield {"type": "done", "provider": "openai-compatible-error", "model": model}


def call_qwen(question: str, packet: dict[str, Any]) -> dict[str, Any]:
    clarification = scope_clarification_answer(packet)
    if clarification:
        cfg = provider_config()
        return {"answer": clarification, "provider": "scope-resolver", "model": cfg["model"]}
    cfg = provider_config()
    api_key = cfg["api_key"]
    base_url = cfg["base_url"]
    model = cfg["model"]
    if not api_key:
        return {
            "answer": sanitize_customer_answer(local_mock_answer(question, packet)),
            "provider": "local-mock",
            "model": "mock-memory-demo",
        }

    body = build_provider_body(
        cfg,
        build_model_messages(question, packet),
        temperature=0.4,
        max_tokens=cfg["max_output_tokens"],
        stream=False,
    )
    req = urllib.request.Request(
        provider_messages_url(cfg),
        data=json.dumps(body).encode("utf-8"),
        headers=provider_headers(cfg),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        answer = sanitize_customer_answer(extract_provider_text(payload, cfg))
        return {"answer": answer, "provider": provider_label(cfg), "model": model}
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, TimeoutError) as exc:
        return {
            "answer": f"模型 API 调用失败，已保留本地记忆链路。错误：{exc}",
            "provider": "openai-compatible-error",
            "model": model,
        }
