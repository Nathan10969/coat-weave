from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from .config import CoatingApiSettings
from .orchestrator import CoatingConversationEngine, compact_tool_calls
from .schemas import ChatRequest, ChatResponse, HealthResponse, OpenAIChatCompletionRequest, ToolProxyRequest
from .security import require_api_auth


SERVICE_ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = SERVICE_ROOT / "core"


def create_app(
    *,
    settings: CoatingApiSettings | None = None,
    engine: CoatingConversationEngine | None = None,
) -> FastAPI:
    settings = settings or CoatingApiSettings.from_env()
    engine = engine or CoatingConversationEngine(settings)
    app = FastAPI(title="Coating API", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def local_chat_ui() -> HTMLResponse:
        return HTMLResponse(LOCAL_CHAT_HTML)

    @app.get("/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return engine.health()

    @app.get("/ready")
    def ready() -> dict[str, Any]:
        return engine.ready()

    async def _auth(request: Request) -> None:
        require_api_auth(request, settings, request.headers.get("authorization"))

    @app.post("/api/v1/coating/chat", response_model=ChatResponse, dependencies=[Depends(_auth)])
    def chat(request: ChatRequest) -> ChatResponse:
        result = engine.chat(request)
        return ChatResponse(
            answer=result.answer,
            provider=result.provider,
            model=result.model,
            conversation_id=request.conversation_id,
            user_id=request.user_id,
            active_scope=result.packet.get("active_scope"),
            tool_calls=compact_tool_calls(result.tool_observations),
            citations=result.citations,
            examples=result.examples,
            debug=result.debug,
        )

    @app.post("/api/v1/coating/chat/stream", dependencies=[Depends(_auth)])
    def chat_stream(request: ChatRequest) -> StreamingResponse:
        def events():
            for event in engine.stream_chat(request):
                yield f"event: {event['event']}\n"
                yield f"data: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.post("/api/ask/stream/chat/completions", dependencies=[Depends(_auth)])
    def platform_stream_chat_completions(
        payload: OpenAIChatCompletionRequest,
        http_request: Request,
    ) -> StreamingResponse:
        chat_request = payload.to_chat_request(session_id=_session_id_from_request(http_request))

        def events():
            completion_id = f"chatcmpl-{uuid.uuid4().hex}"
            created = int(time.time())
            model = payload.model or settings.llm_model
            sent_first_content = False
            stream_mode = _platform_stream_mode()
            diag = (
                _stream_diagnostics(
                    completion_id,
                    chat_request.conversation_id,
                    layer="api.app.platform_stream",
                )
                if stream_mode == "live"
                else None
            )
            try:
                if diag:
                    active_after = diag.incr_active()
                    diag.event("platform_stream_start", stream_mode=stream_mode, active_after=active_after)
                try:
                    source = (
                        _live_openai_events(engine, chat_request, completion_id)
                        if stream_mode == "live"
                        else _buffered_openai_events(engine, chat_request, settings.stream_chunk_chars)
                    )
                    for event in source:
                        if event.get("model"):
                            model = str(event["model"])
                        if event.get("type") == "delta" and event.get("content"):
                            content = str(event["content"])
                            delta = (
                                {"role": "assistant", "content": content}
                                if not sent_first_content
                                else {"content": content}
                            )
                            sent_first_content = True
                            chunk = _openai_sse_chunk(
                                completion_id=completion_id,
                                created=created,
                                model=model,
                                delta=delta,
                                finish_reason=None,
                            )
                            if diag:
                                diag.event(
                                    "before_sse_yield",
                                    chars=len(content),
                                    bytes_=len(chunk.encode("utf-8")),
                                )
                            yield chunk
                            if diag:
                                diag.event("after_sse_yield", chars=len(content))
                except Exception as exc:  # noqa: BLE001 - SSE clients must receive a terminal event.
                    if diag:
                        diag.event("platform_stream_exception", error=str(exc))
                    message = f"Streaming request failed: {exc}"
                    delta = (
                        {"role": "assistant", "content": message}
                        if not sent_first_content
                        else {"content": message}
                    )
                    sent_first_content = True
                    chunk = _openai_sse_chunk(
                        completion_id=completion_id,
                        created=created,
                        model=model,
                        delta=delta,
                        finish_reason=None,
                    )
                    if diag:
                        diag.event(
                            "before_sse_yield",
                            chars=len(message),
                            bytes_=len(chunk.encode("utf-8")),
                        )
                    yield chunk
                    if diag:
                        diag.event("after_sse_yield", chars=len(message))
                if not sent_first_content:
                    chunk = _openai_sse_chunk(
                        completion_id=completion_id,
                        created=created,
                        model=model,
                        delta={"role": "assistant"},
                        finish_reason=None,
                    )
                    if diag:
                        diag.event("before_sse_yield", chars=0, bytes_=len(chunk.encode("utf-8")))
                    yield chunk
                    if diag:
                        diag.event("after_sse_yield", chars=0)
                stop_chunk = _openai_sse_chunk(
                    completion_id=completion_id,
                    created=created,
                    model=model,
                    delta={},
                    finish_reason="stop",
                )
                if diag:
                    diag.event("before_stop_yield", bytes_=len(stop_chunk.encode("utf-8")))
                yield stop_chunk
                if diag:
                    diag.event("after_stop_yield")
                done_chunk = "data: [DONE]\n\n"
                if diag:
                    diag.event("before_done_yield", bytes_=len(done_chunk.encode("utf-8")))
                yield done_chunk
                if diag:
                    diag.event("after_done_yield")
            finally:
                if diag:
                    diag.event("platform_stream_finally", sent_first_content=sent_first_content)
                    active_after = diag.decr_active()
                    diag.event("platform_stream_closed", active_after=active_after)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/coating/tools/hybrid_search", dependencies=[Depends(_auth)])
    def hybrid_search(request: ToolProxyRequest) -> dict[str, Any]:
        return engine.call_tool("kg.hybrid_search", request.payload)

    @app.post("/api/v1/coating/tools/expand_hyperedge_multihop", dependencies=[Depends(_auth)])
    def expand_hyperedge_multihop(request: ToolProxyRequest) -> dict[str, Any]:
        return engine.call_tool("kg.expand_hyperedge_multihop", request.payload)

    @app.post("/api/v1/coating/tools/sql_aggregate", dependencies=[Depends(_auth)])
    def sql_aggregate(request: ToolProxyRequest) -> dict[str, Any]:
        return engine.call_tool("kg.sql_aggregate", request.payload)

    @app.post("/api/v1/coating/tools/doc_field_scan", dependencies=[Depends(_auth)])
    def doc_field_scan(request: ToolProxyRequest) -> dict[str, Any]:
        return engine.call_tool("kg.doc_field_scan", request.payload)

    return app


def _session_id_from_request(request: Request) -> str | None:
    for name in (
        "x-session-id",
        "session-id",
        "sessionid",
        "x-conversation-id",
        "conversation-id",
    ):
        value = request.headers.get(name)
        if value and value.strip():
            return value.strip()
    for name in (
        "sessionId",
        "session_id",
        "sessionid",
        "conversation_id",
        "conversationId",
    ):
        value = request.query_params.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _platform_stream_mode() -> str:
    mode = os.environ.get("COATING_PLATFORM_STREAM_MODE", "buffered").strip().lower()
    return "live" if mode == "live" else "buffered"


def _stream_diagnostics(request_id: str, conversation_id: str, *, layer: str):
    try:
        if str(CORE_DIR) not in sys.path:
            sys.path.insert(0, str(CORE_DIR))
        from stream_diagnostics import StreamDiagnostics

        return StreamDiagnostics(request_id=request_id, conversation_id=conversation_id, layer=layer)
    except Exception:
        return None


def _live_openai_events(
    engine: CoatingConversationEngine,
    chat_request: ChatRequest,
    diag_request_id: str,
) -> Iterator[dict[str, Any]]:
    try:
        return engine.stream_openai_chat_completion(chat_request, diag_request_id=diag_request_id)
    except TypeError as exc:
        if "diag_request_id" not in str(exc):
            raise
        return engine.stream_openai_chat_completion(chat_request)


def _buffered_openai_events(
    engine: CoatingConversationEngine,
    chat_request: ChatRequest,
    chunk_chars: int,
) -> Iterator[dict[str, Any]]:
    result = engine.chat(chat_request)
    for chunk in _split_text(result.answer, chunk_chars):
        yield {"type": "delta", "content": chunk, "model": result.model}
    yield {
        "type": "done",
        "provider": result.provider,
        "model": result.model,
        "route": result.route,
        "tool_calls": compact_tool_calls(result.tool_observations),
    }


def _split_text(text: str, chunk_chars: int) -> Iterator[str]:
    content = str(text or "")
    size = max(1, int(chunk_chars or 4))
    for idx in range(0, len(content), size):
        yield content[idx : idx + size]


def _openai_sse_chunk(
    *,
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: str | None,
) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


LOCAL_CHAT_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Coating KG Chat</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fb;
      --panel: #ffffff;
      --ink: #171923;
      --muted: #667085;
      --line: #d7dce5;
      --accent: #1d6f63;
      --accent-2: #234ea5;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
    }
    .shell {
      display: grid;
      grid-template-rows: auto 1fr auto;
      min-height: 100vh;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 14px 18px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    h1 {
      margin: 0;
      font-size: 17px;
      font-weight: 650;
      letter-spacing: 0;
    }
    .meta {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 13px;
    }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
      background: #fbfcfe;
    }
    main {
      overflow-y: auto;
      padding: 20px;
    }
    #messages {
      max-width: 980px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .msg {
      width: fit-content;
      max-width: min(820px, 100%);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 11px 13px;
      background: var(--panel);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .user {
      align-self: flex-end;
      color: #ffffff;
      background: var(--accent-2);
      border-color: var(--accent-2);
    }
    .assistant {
      align-self: flex-start;
    }
    .system {
      align-self: center;
      color: var(--muted);
      background: transparent;
      border: 0;
      padding: 4px 0;
      font-size: 13px;
    }
    .error {
      color: var(--danger);
      border-color: #f4b9b2;
      background: #fff7f6;
    }
    form {
      border-top: 1px solid var(--line);
      background: var(--panel);
      padding: 14px 18px 18px;
    }
    .composer {
      max-width: 980px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: end;
    }
    textarea {
      width: 100%;
      min-height: 58px;
      max-height: 180px;
      resize: vertical;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 11px;
      color: var(--ink);
      font: inherit;
      outline: none;
      background: #ffffff;
    }
    textarea:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(29, 111, 99, 0.14);
    }
    .controls {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    button {
      height: 38px;
      min-width: 86px;
      border: 1px solid var(--accent);
      border-radius: 8px;
      padding: 0 14px;
      color: #ffffff;
      background: var(--accent);
      font: inherit;
      font-weight: 650;
      cursor: pointer;
    }
    button.secondary {
      color: var(--accent);
      background: #ffffff;
    }
    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }
    .settings {
      max-width: 980px;
      margin: 8px auto 0;
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 8px;
    }
    input {
      min-width: 0;
      height: 34px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 10px;
      font: inherit;
    }
    @media (max-width: 720px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding: 14px; }
      .composer { grid-template-columns: 1fr; }
      .controls { flex-direction: row; }
      .settings { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <h1>Coating KG Chat</h1>
      <div class="meta">
        <span class="pill" id="mode">live stream</span>
        <span class="pill" id="session"></span>
        <span class="pill"><a href="/docs" target="_blank" rel="noreferrer">API docs</a></span>
      </div>
    </header>
    <main id="scroll">
      <div id="messages">
        <div class="msg system">Local coating API is ready.</div>
      </div>
    </main>
    <form id="form">
      <div class="composer">
        <textarea id="input" autocomplete="off" placeholder="输入问题，比如：给我一个船舶涂料的配方"></textarea>
        <div class="controls">
          <button id="send" type="submit">发送</button>
          <button class="secondary" id="stop" type="button" disabled>停止</button>
        </div>
      </div>
      <div class="settings">
        <input id="model" value="deepseek-v4-pro" aria-label="model" />
        <input id="token" type="password" placeholder="Bearer token (optional)" aria-label="bearer token" />
      </div>
    </form>
  </div>
  <script>
    const messages = document.getElementById("messages");
    const scrollBox = document.getElementById("scroll");
    const form = document.getElementById("form");
    const input = document.getElementById("input");
    const send = document.getElementById("send");
    const stop = document.getElementById("stop");
    const model = document.getElementById("model");
    const token = document.getElementById("token");
    const session = document.getElementById("session");
    const sessionId = localStorage.getItem("coatingSessionId") || `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    localStorage.setItem("coatingSessionId", sessionId);
    session.textContent = sessionId;
    token.value = localStorage.getItem("coatingApiToken") || "";
    token.addEventListener("change", () => localStorage.setItem("coatingApiToken", token.value.trim()));
    let controller = null;

    function addMessage(role, text = "") {
      const node = document.createElement("div");
      node.className = `msg ${role}`;
      node.textContent = text;
      messages.appendChild(node);
      scrollBox.scrollTop = scrollBox.scrollHeight;
      return node;
    }

    function append(node, text) {
      node.textContent += text;
      scrollBox.scrollTop = scrollBox.scrollHeight;
    }

    function setBusy(busy) {
      send.disabled = busy;
      stop.disabled = !busy;
      input.disabled = busy;
    }

    async function sendMessage(question) {
      addMessage("user", question);
      const assistant = addMessage("assistant", "");
      controller = new AbortController();
      setBusy(true);
      try {
        const headers = {"Content-Type": "application/json", "X-Session-Id": sessionId};
        const auth = token.value.trim();
        if (auth) headers.Authorization = auth.toLowerCase().startsWith("bearer ") ? auth : `Bearer ${auth}`;
        const response = await fetch("/api/ask/stream/chat/completions", {
          method: "POST",
          headers,
          signal: controller.signal,
          body: JSON.stringify({
            model: model.value.trim() || "deepseek-v4-pro",
            stream: true,
            user: "local-browser",
            messages: [{role: "user", content: question}]
          })
        });
        if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          let boundary;
          while ((boundary = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            for (const rawLine of block.split(/\r?\n/)) {
              const line = rawLine.trim();
              if (!line.startsWith("data:")) continue;
              const data = line.slice(5).trim();
              if (data === "[DONE]") return;
              if (!data) continue;
              const payload = JSON.parse(data);
              for (const choice of payload.choices || []) {
                const content = choice.delta && choice.delta.content;
                if (content) append(assistant, content);
              }
            }
          }
        }
      } catch (err) {
        if (err.name !== "AbortError") {
          assistant.classList.add("error");
          append(assistant, `\n${err.message || err}`);
        }
      } finally {
        setBusy(false);
        controller = null;
        input.focus();
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const question = input.value.trim();
      if (!question) return;
      input.value = "";
      sendMessage(question);
    });
    stop.addEventListener("click", () => controller && controller.abort());
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) form.requestSubmit();
    });
    input.focus();
  </script>
</body>
</html>
"""
