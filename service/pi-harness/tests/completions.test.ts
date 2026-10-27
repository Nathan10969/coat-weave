import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import test from "node:test";
import type { AssistantMessage } from "@earendil-works/pi-ai";
import type { PromptOptions } from "@earendil-works/pi-coding-agent";
import type { CompletionSeed, PiEvent, PiMessages, SessionPort } from "../src/contracts.js";
import { createPiHttpServer, type ServerOptions } from "../src/server.js";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const expectedWelcome = "Hello, I am CoatWeave, an evidence-first coating patent research assistant.";

function assistant(text: string, stopReason: AssistantMessage["stopReason"] = "stop"): AssistantMessage {
  return { role: "assistant", content: [{ type: "text", text }], api: "openai-completions", provider: "offline",
    model: "fixture", stopReason, timestamp: 1, usage: { input: 1, output: 2, cacheRead: 3, cacheWrite: 4,
      totalTokens: 10, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
}
class FakeSession implements SessionPort {
  sessionId = randomUUID();
  messages: PiMessages = [];
  isStreaming = false;
  disposed = false;
  aborts = 0;
  listeners = new Set<(event: PiEvent) => void>();
  received: { text: string; options?: PromptOptions }[] = [];
  release?: () => void;
  onAbort?: () => void;
  constructor(readonly run: (session: FakeSession) => Promise<void>) {}
  emit(event: PiEvent) { for (const listener of this.listeners) listener(event); }
  subscribe(listener: (event: PiEvent) => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
  async prompt(text: string, options?: PromptOptions) {
    this.received.push({ text, options }); this.isStreaming = true;
    try { await this.run(this); } finally { this.isStreaming = false; }
  }
  async abort() { this.aborts++; this.release?.(); this.onAbort?.(); }
  dispose() { this.disposed = true; }
  finish(text = "reply", deltas = [text]) {
    const message = assistant(text);
    this.emit({ type: "agent_start" });
    this.emit({ type: "message_start", message });
    for (const delta of deltas) this.emit({ type: "message_update", message,
      assistantMessageEvent: { type: "text_delta", contentIndex: 0, delta, partial: message } });
    this.messages.push(message);
    this.emit({ type: "message_end", message });
    this.emit({ type: "agent_end", messages: [message], willRetry: false });
    this.emit({ type: "agent_settled" });
  }
}
async function fixture(
  run: (session: FakeSession) => Promise<void> = async session => { session.finish(); },
  options: Omit<ServerOptions, "factory"> & { startFail?: boolean; create?: () => Promise<void> } = {},
) {
  const sessions: FakeSession[] = [];
  const seeds: (CompletionSeed | undefined)[] = [];
  const api = createPiHttpServer({ ...options, factory: async seed => {
    seeds.push(seed);
    await options.create?.();
    const session = new FakeSession(run); sessions.push(session);
    return { session, start: async () => { if (options.startFail) throw new Error("private startup error"); } };
  } });
  await new Promise<void>(resolve => api.server.listen(0, "127.0.0.1", resolve));
  const address = api.server.address(); assert.ok(address && typeof address !== "string");
  const base = `http://127.0.0.1:${address.port}`;
  const post = (value: unknown, path = "/chat/completions", headers: Record<string, string> = {}) => fetch(base + path, {
    method: "POST", headers: { "Content-Type": "application/json", ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}), ...headers },
    body: JSON.stringify(value),
  });
  return { api, sessions, seeds, base, post };
}
const payload = { model: "coat-weave-pi", messages: [{ role: "user", content: "question" }], stream: true };

test("native and completion routes share metadata-only nested tool logging", async () => {
  const directory = mkdtempSync(join(tmpdir(), "coat-weave-http-log-"));
  const path = join(directory, "events.jsonl");
  const f = await fixture(async session => {
    session.emit({ type: "tool_execution_start", toolName: "patent_search", toolCallId: "synthetic_call",
      args: { statement: { op: "read_document", doc_id: "SYNTHETIC-DEMO", start_line: 1 } } });
    session.emit({ type: "tool_execution_end", toolName: "patent_search", toolCallId: "synthetic_call", isError: false,
      result: { details: { success: true, has_more: false, content: "PRIVATE_BODY_MUST_NOT_APPEAR" } } });
    session.finish("done");
  }, { eventLogPath: path });
  try {
    assert.equal((await f.post({ ...payload, stream: false })).status, 200);
    const created = await f.post({}, "/sessions");
    const { sessionId } = await created.json();
    assert.equal((await f.post({ text: "Read the synthetic document." }, `/sessions/${sessionId}/prompt`)).status, 204);
    const text = readFileSync(path, "utf8");
    assert.doesNotMatch(text, /PRIVATE_BODY_MUST_NOT_APPEAR|Read the synthetic document/);
    const rows = text.trim().split("\n").map(line => JSON.parse(line));
    const ends = rows.filter(row => row.event_type === "tool_execution_end");
    assert.equal(ends.length, 2);
    assert.equal(new Set(ends.map(row => row.session_id)).size, 2);
    assert.ok(ends.every(row => row.op === "read_document" && row.start_line === 1 && Object.keys(row).length === 11));
  } finally { await f.api.close(); rmSync(directory, { recursive: true, force: true }); }
});
interface Chunk {
  id: string;
  model: string;
  object: string;
  choices: { index: number; delta: { content?: string; role?: string }; finish_reason: string | null }[];
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}
function chunks(text: string): Chunk[] {
  return text.split("\n").filter(line => line.startsWith("data: ") && line !== "data: [DONE]").map(line => JSON.parse(line.slice(6)) as Chunk);
}
async function closed(session: FakeSession) {
  for (let attempt = 0; attempt < 100 && !session.disposed; attempt++) await delay(5);
  assert.equal(session.disposed, true);
}

test("OpenAI SSE forwards text once, preserves citations, hides reasoning and includes requested usage", async () => {
  const text = "Evidence: tos://bucket/WO/document.md#L12";
  const f = await fixture(async session => {
    const message = assistant(text);
    session.emit({ type: "message_start", message });
    session.emit({ type: "message_update", message, assistantMessageEvent: {
      type: "thinking_delta", contentIndex: 0, delta: "private reasoning", partial: message,
    } });
    session.emit({ type: "tool_execution_end", toolCallId: "t1", toolName: "patent_search", isError: false,
      result: { content: [{ type: "text", text: "private tool body" }], details: { success: true } } });
    session.finish(text, ["Evidence: ", "tos://bucket/WO/document.md#L12"]);
  });
  try {
    const response = await f.post({ ...payload, enable_thinking: false, stream_options: { include_usage: true } });
    assert.equal(response.status, 200);
    assert.match(response.headers.get("content-type")!, /text\/event-stream/);
    const body = await response.text();
    const frames = chunks(body);
    assert.equal(frames.flatMap(frame => frame.choices).map(choice => choice.delta.content ?? "").join(""), text);
    assert.equal(frames[0].choices[0].delta.role, "assistant");
    assert.equal(frames.at(-2)?.choices[0].finish_reason, "stop");
    assert.deepEqual(frames.at(-1)?.usage, { prompt_tokens: 8, completion_tokens: 2, total_tokens: 10 });
    assert.equal(new Set(frames.map(frame => frame.id)).size, 1);
    assert.ok(frames.every(frame => frame.model === "coat-weave-pi" && frame.object === "chat.completion.chunk"));
    assert.equal(body.includes("private"), false);
    assert.equal(body.match(/data: \[DONE\]/g)?.length, 1);
    await closed(f.sessions[0]);
  } finally { await f.api.close(); }
});

test("each completion seeds supplied history and releases its own context; native sessions remain independent", async () => {
  const f = await fixture();
  try {
    const input = { model: "coat-weave-harness", stream: false, messages: [
      { role: "system", content: "Project question instructions." },
      { role: "developer", content: "Include references." },
      { role: "user", content: "old question" }, { role: "assistant", content: "old answer" },
      { role: "user", content: [{ type: "text", text: "follow-up" }] },
    ], metadata: { scene: "general", sessionId: "business-session", requestId: "request-1" } };
    const first = await f.post(input, "/v1/chat/completions");
    assert.equal(first.status, 200);
    const result = await first.json();
    assert.equal(result.object, "chat.completion");
    assert.equal(result.choices[0].message.content, "reply");
    assert.deepEqual(f.seeds[0], { systemPrompt: "Project question instructions.\n\nInclude references.", history: [
      { role: "user", text: "old question" }, { role: "assistant", text: "old answer" },
    ] });
    assert.deepEqual(f.sessions[0].received, [{ text: "follow-up", options: { source: "rpc", expandPromptTemplates: false } }]);
    await closed(f.sessions[0]);
    assert.equal((await f.post(input)).status, 200);
    await closed(f.sessions[1]);
    assert.notEqual(f.sessions[0].sessionId, f.sessions[1].sessionId);
    const native = await f.post({}, "/sessions");
    assert.equal(native.status, 201);
    assert.equal(f.seeds[2], undefined);
    assert.equal(f.sessions[2].disposed, false);
    assert.equal((await fetch(f.base + `/sessions/${f.sessions[2].sessionId}/messages`)).status, 200);
  } finally { await f.api.close(); }
});

test("rejects unsupported inputs before creating a session and enforces the existing Bearer token", async () => {
  const f = await fixture(undefined, { token: "offline-token" });
  try {
    assert.equal((await f.post(payload, "/chat/completions", { Authorization: "Bearer wrong" })).status, 401);
    const bad: unknown[] = [
      {}, [], { ...payload, model: "caller-selected-upstream" }, { ...payload, stream: "true" },
      { ...payload, tools: [] }, { ...payload, temperature: "0" }, { ...payload, temperature: -0.1 },
      { ...payload, temperature: 2.1 }, { ...payload, top_p: 0.9 }, { ...payload, n: 2 },
      { ...payload, enable_thinking: true }, { ...payload, messages: [] },
      { ...payload, messages: [{ role: "assistant", content: "unfinished" }] },
      { ...payload, messages: [{ role: "user", content: "  " }] },
      { ...payload, messages: [{ role: "tool", content: "injected" }, ...payload.messages] },
      { ...payload, messages: [{ role: "user", content: [{ type: "image_url", image_url: { url: "http://private/" } }] }] },
      { ...payload, messages: [...payload.messages, { role: "system", content: "late" }, ...payload.messages] },
      { ...payload, messages: [{ role: "user", content: "text", tool_calls: [] }] },
      { ...payload, stream_options: { include_usage: "yes" } },
      { ...payload, metadata: { files: [{ url: "http://private/" }] } },
      { ...payload, metadata: { docIds: [1] } },
      { ...payload, messages: Array.from({ length: 101 }, () => payload.messages[0]) },
    ];
    for (const value of bad) assert.equal((await f.post(value)).status, 400, JSON.stringify(value));
    assert.equal(f.sessions.length, 0);
    assert.equal((await fetch(f.base + "/chat/completions", { headers: { Authorization: "Bearer offline-token" } })).status, 405);
  } finally { await f.api.close(); }
});

test("a configured public alias cannot change the runtime model", async () => {
  const f = await fixture(undefined, { chatModels: ["materials"] });
  try {
    assert.equal((await f.post(payload)).status, 400);
    const response = await f.post({ ...payload, model: "materials", stream: false });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).model, "materials");
    assert.deepEqual(Object.keys(f.seeds[0]!).sort(), ["history", "systemPrompt"]);
  } finally { await f.api.close(); }
});

test("startup and model errors are sanitized and empty/unfinished responses never become success", async () => {
  for (const kind of ["startup", "throw", "error", "empty", "unfinished"] as const) {
    const f = await fixture(async session => {
      if (kind === "throw") throw new Error("secret-provider-error");
      if (kind === "error") {
        const message = { ...assistant("", "error"), errorMessage: "secret-provider-error" };
        session.emit({ type: "message_end", message }); session.emit({ type: "agent_settled" });
      } else if (kind === "empty") session.finish("");
      else session.emit({ type: "agent_settled" });
    }, { startFail: kind === "startup" });
    try {
      const response = await f.post(payload);
      assert.equal(response.status, 502, kind);
      const body = await response.text();
      assert.equal(body.includes("[DONE]"), false);
      assert.equal(body.includes("secret"), false);
      assert.equal(body.includes("private"), false);
      await closed(f.sessions[0]);
    } finally { await f.api.close(); }
  }
});

test("tool isError and backend success=false both stop a completion without disclosing raw tool data", async () => {
  for (const isError of [true, false]) {
    const f = await fixture(async session => {
      session.emit({ type: "tool_execution_end", toolName: "patent_search", toolCallId: "t1", isError,
        result: { content: [{ type: "text", text: "private body" }], details: { success: false, message: "secret-detail" } } });
      session.finish("misleading success");
    });
    try {
      const response = await f.post(payload);
      assert.equal(response.status, 502);
      const body = await response.json();
      assert.equal(body.error.code, "TOOL_FAILED");
      assert.equal(JSON.stringify(body).includes("secret"), false);
      assert.equal(JSON.stringify(body).includes("misleading"), false);
      await closed(f.sessions[0]);
    } finally { await f.api.close(); }
  }
});

test("retry before output waits for final settlement; final snapshots are not lost", async () => {
  const f = await fixture(async session => {
    session.emit({ type: "message_end", message: assistant("", "error") });
    session.emit({ type: "agent_end", messages: [], willRetry: true });
    session.emit({ type: "auto_retry_start", attempt: 1, maxAttempts: 2, delayMs: 0, errorMessage: "private error" });
    session.finish("retried answer", []);
  });
  try {
    const response = await f.post(payload);
    const body = await response.text();
    assert.equal(response.status, 200);
    assert.equal(chunks(body).flatMap(frame => frame.choices).map(choice => choice.delta.content ?? "").join(""), "retried answer");
    assert.equal(body.match(/\[DONE\]/g)?.length, 1);
  } finally { await f.api.close(); }
});

test("failure after partial text closes an incomplete stream without a success terminator", async () => {
  const f = await fixture(async session => {
    const message = assistant("partial");
    session.emit({ type: "message_start", message });
    session.emit({ type: "message_update", message, assistantMessageEvent: { type: "text_delta", contentIndex: 0, delta: "partial", partial: message } });
    await delay(30);
    session.emit({ type: "message_end", message: { ...assistant("", "error"), errorMessage: "secret" } });
    session.emit({ type: "agent_settled" });
  });
  try {
    const response = await f.post(payload);
    assert.equal(response.status, 200);
    const reader = response.body!.getReader();
    let body = "";
    await assert.rejects(async () => {
      for (;;) { const chunk = await reader.read(); if (chunk.done) break; body += new TextDecoder().decode(chunk.value); }
    });
    assert.ok(body.includes("partial"));
    assert.equal(body.includes("[DONE]"), false);
    assert.equal(body.includes('"finish_reason":"stop"'), false);
    assert.equal(body.includes("secret"), false);
  } finally { await f.api.close(); }
});

test("disconnect aborts the SDK and completion slots are shared with native sessions", async () => {
  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const f = await fixture(async session => {
    entered();
    await new Promise<void>(resolve => { session.release = resolve; });
  }, { maxSessions: 1 });
  try {
    const controller = new AbortController();
    const pending = fetch(f.base + "/chat/completions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: controller.signal });
    const rejected = assert.rejects(pending);
    await started;
    assert.equal((await f.post(payload)).status, 503);
    assert.equal((await f.post({}, "/sessions")).status, 503);
    controller.abort(); await rejected;
    await closed(f.sessions[0]);
    assert.ok(f.sessions[0].aborts > 0);
    assert.equal((await f.post({}, "/sessions")).status, 201);
    assert.equal((await f.post(payload)).status, 503);
  } finally { await f.api.close(); }
});

test("completion timeout responds even when prompt has not returned, and shutdown aborts active runs", async () => {
  const f = await fixture(async session => {
    await new Promise<void>(resolve => { session.release = resolve; });
  }, { completionTimeoutMs: 30 });
  try {
    const response = await f.post(payload);
    assert.equal(response.status, 504);
    assert.equal((await response.json()).error.code, "COMPLETION_TIMEOUT");
    await closed(f.sessions[0]);
  } finally { await f.api.close(); }

  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const g = await fixture(async session => { entered(); await new Promise<void>(resolve => { session.release = resolve; }); });
  const request = g.post(payload);
  await started;
  await g.api.close();
  const response = await request;
  assert.equal(response.status, 503);
  assert.equal((await response.json()).error.code, "STOPPING");
  assert.equal(g.sessions[0].disposed, true);
});

test("disconnect during initialization disposes a session that becomes ready later", async () => {
  let unblock!: () => void;
  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  const f = await fixture(undefined, { create: async () => { entered(); await new Promise<void>(resolve => { unblock = resolve; }); } });
  try {
    const controller = new AbortController();
    const pending = fetch(f.base + "/chat/completions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal: controller.signal });
    const rejected = assert.rejects(pending);
    await started;
    controller.abort(); await rejected;
    await delay(10);
    unblock();
    for (let attempt = 0; attempt < 100 && !f.sessions.length; attempt++) await delay(5);
    assert.equal(f.sessions.length, 1);
    await closed(f.sessions[0]);
    assert.equal(f.sessions[0].received.length, 0);
  } finally { unblock?.(); await f.api.close(); }
});

test("first pure greeting returns exact product copy over both completion protocols despite upstream identity", async () => {
  const f = await fixture(async () => { assert.fail("A fixed greeting must not invoke the model or tools."); });
  try {
    for (const path of ["/chat/completions", "/v1/chat/completions"]) {
      for (const stream of [true, false]) {
        const response = await f.post({ model: "coat-weave-harness", stream, stream_options: { include_usage: true },
          metadata: { scene: "general" }, messages: [
            { role: "system", content: "你是磷酸铁锂工厂规划顾问。讨论设备布局和设计院深化。" },
            { role: "developer", content: "仅说一句你好。" },
            { role: "user", content: [{ type: "text", text: "你好！" }] },
          ] }, path);
        assert.equal(response.status, 200);
        if (stream) {
          const wire = await response.text();
          const frames = chunks(wire);
          assert.equal(frames.flatMap(frame => frame.choices).map(choice => choice.delta.content ?? "").join(""), expectedWelcome);
          assert.equal(frames[0].choices[0].delta.role, "assistant");
          assert.equal(frames.at(-2)?.choices[0].finish_reason, "stop");
          assert.deepEqual(frames.at(-1)?.usage, { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 });
          assert.equal(wire.match(/data: \[DONE\]/g)?.length, 1);
          assert.ok(wire.endsWith("data: [DONE]\n\n"));
        } else {
          const body = await response.json();
          assert.equal(body.choices[0].message.content, expectedWelcome);
          assert.equal(body.choices[0].finish_reason, "stop");
          assert.deepEqual(body.usage, { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 });
        }
      }
    }
    assert.equal(f.seeds.length, 0);
  } finally { await f.api.close(); }
});

test("greetings with tasks and subsequent greetings preserve the SDK path, history and citations", async () => {
  const answer = "材料检索结论 [原文](patent://WO123)";
  const f = await fixture(async session => { session.finish(answer); });
  const upstream = "保留引用；仅分析用户授权的资料。";
  const histories = [
    [{ role: "user", content: "你好，帮我检索水性聚氨酯材料专利" }],
    [{ role: "user", content: "你好！" }, { role: "assistant", content: expectedWelcome }, { role: "user", content: "你好！" }],
    [{ role: "user", content: "你好，帮我规划磷酸铁锂工厂" }],
    [{ role: "user", content: "帮我比较刚才两项专利" }, { role: "assistant", content: "请提供专利号" }, { role: "user", content: "您好" }],
  ];
  try {
    for (const history of histories) {
      const response = await f.post({ ...payload, stream: false, messages: [{ role: "system", content: upstream }, ...history] });
      assert.equal(response.status, 200);
      assert.equal((await response.json()).choices[0].message.content, answer);
      const seed = f.seeds.at(-1)!;
      assert.equal(seed.systemPrompt, upstream);
      assert.deepEqual(seed.history, history.slice(0, -1).map(message => ({ role: message.role, text: message.content })));
      assert.equal(f.sessions.at(-1)!.received[0].text, history.at(-1)!.content);
      await closed(f.sessions.at(-1)!);
    }
  } finally { await f.api.close(); }
});
