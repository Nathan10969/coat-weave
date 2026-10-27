import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";
import type { PromptOptions } from "@earendil-works/pi-coding-agent";
import { createPiHttpServer, parsePrompt } from "../src/server.js";
import type { PiEvent, PiMessages, SessionPort } from "../src/contracts.js";

class FakeSession implements SessionPort {
  sessionId = randomUUID();
  messages: PiMessages = [];
  isStreaming = false;
  disposed = false;
  listeners = new Set<(event: PiEvent) => void>();
  received: { text: string; options?: PromptOptions }[] = [];
  run: (text: string, options?: PromptOptions) => Promise<void> = async () => {};
  release?: () => void;
  emit(event: PiEvent) { for (const listener of this.listeners) listener(event); }
  subscribe(listener: (event: PiEvent) => void) { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; }
  async prompt(text: string, options?: PromptOptions) { this.received.push({ text, options }); await this.run(text, options); }
  async abort() { this.isStreaming = false; this.release?.(); }
  dispose() { this.disposed = true; }
}
async function fixture(options: { token?: string; journalBytes?: number; startFail?: boolean } = {}) {
  const session = new FakeSession();
  const api = createPiHttpServer({ ...options, factory: async () => ({ session, start: async () => { if (options.startFail) throw new Error("private-details"); } }) });
  await new Promise<void>(resolve => api.server.listen(0, "127.0.0.1", resolve));
  const address = api.server.address(); assert.ok(address && typeof address !== "string");
  const base = `http://127.0.0.1:${address.port}`;
  const request = (path: string, method = "GET", value?: unknown, extra?: Record<string, string>) => fetch(base + path, {
    method, headers: { ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}), ...(value === undefined ? {} : { "Content-Type": "application/json" }), ...extra },
    ...(value === undefined ? {} : { body: JSON.stringify(value) }),
  });
  return { api, session, base, request, path: `/sessions/${session.sessionId}` };
}
async function readFrames(reader: ReadableStreamDefaultReader<Uint8Array>, count: number) {
  let buffer = "";
  const decoder = new TextDecoder();
  const frames: { id: number; event: string; data: unknown }[] = [];
  while (frames.length < count) {
    const chunk = await reader.read(); assert.equal(chunk.done, false);
    buffer += decoder.decode(chunk.value, { stream: true });
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
      const lines = frame.split("\n");
      const data = lines.find(line => line.startsWith("data: "));
      if (!data) continue;
      frames.push({ id: Number(lines.find(line => line.startsWith("id: "))?.slice(4)), event: lines.find(line => line.startsWith("event: "))!.slice(7), data: JSON.parse(data.slice(6)) });
    }
  }
  return frames;
}
test("validates HTTP inputs and SDK image shape without echoing credentials", async () => {
  const f = await fixture({ token: "test-only" });
  try {
    assert.equal((await f.request("/sessions", "POST", {}, { Authorization: "Bearer wrong" })).status, 401);
    assert.equal((await f.request("/sessions", "POST", { cwd: "/private" })).status, 400);
    assert.equal((await f.request("/sessions", "POST", {})).status, 201);
    assert.equal((await f.request(f.path + "/prompt", "POST", { text: "x", options: { images: [{ type: "image", source: {} }] } })).status, 400);
    assert.equal((await f.request("/sessions/missing/messages")).status, 404);
    assert.deepEqual(parsePrompt({ text: "图片", options: { images: [{ type: "image", data: "AA==", mimeType: "image/png" }] } }).text, "图片");
  } finally { await f.api.close(); }
});
test("preserves raw SDK events and nested tool details; SSE stays open after settlement", async () => {
  const f = await fixture();
  try {
    await f.request("/sessions", "POST", {});
    const response = await f.request(f.path + "/events");
    const reader = response.body!.getReader();
    const event: PiEvent = { type: "tool_execution_end", toolCallId: "call-1", toolName: "patent_search", isError: true,
      result: { content: [{ type: "text", text: "检索错误" }], details: { success: false, extra: { futureField: 42 } } } };
    f.session.run = async () => { f.session.emit(event); f.session.emit({ type: "agent_end", messages: [], willRetry: true }); f.session.emit({ type: "agent_settled" }); };
    const framesPromise = readFrames(reader, 3);
    assert.equal((await f.request(f.path + "/prompt", "POST", { text: "材料问题" })).status, 204);
    const frames = await framesPromise;
    assert.deepEqual(frames[0].data, event);
    assert.equal(frames[0].event, event.type);
    f.session.emit({ type: "session_info_changed", name: "late child result" });
    assert.deepEqual((await readFrames(reader, 1))[0].data, { type: "session_info_changed", name: "late child result" });
    f.session.messages = [{ role: "user", content: "用户", timestamp: 1 }];
    assert.deepEqual(await (await f.request(f.path + "/messages")).json(), f.session.messages);
    await reader.cancel();
    assert.equal(f.session.disposed, false);
    assert.equal((await f.request(f.path, "DELETE")).status, 204);
    assert.equal(f.session.disposed, true);
  } finally { await f.api.close(); }
});
test("replay snapshots streaming objects immediately; old cursors reject explicitly", async () => {
  const f = await fixture({ journalBytes: 512 });
  try {
    await f.request("/sessions", "POST", {});
    const event: PiEvent = { type: "session_info_changed", name: "before" };
    f.session.emit(event); event.name = "after";
    const response = await f.request(f.path + "/events", "GET", undefined, { "Last-Event-ID": "0" });
    const reader = response.body!.getReader();
    assert.deepEqual((await readFrames(reader, 1))[0].data, { type: "session_info_changed", name: "before" });
    await reader.cancel();
    for (let index = 0; index < 20; index++) f.session.emit({ type: "agent_start" });
    assert.equal((await f.request(f.path + "/events", "GET", undefined, { "Last-Event-ID": "0" })).status, 409);
    assert.equal((await f.request(f.path + "/events", "GET", undefined, { "Last-Event-ID": "invalid" })).status, 400);
  } finally { await f.api.close(); }
});
test("busy sessions reject concurrent prompts; queue options and abort use SDK methods", async () => {
  const f = await fixture();
  try {
    await f.request("/sessions", "POST", {});
    let entered!: () => void;
    const started = new Promise<void>(resolve => { entered = resolve; });
    f.session.run = async (_text, options) => {
      if (options?.streamingBehavior) return;
      f.session.isStreaming = true; entered();
      await new Promise<void>(resolve => { f.session.release = resolve; });
    };
    const first = f.request(f.path + "/prompt", "POST", { text: "first" });
    await started;
    assert.equal((await f.request(f.path + "/prompt", "POST", { text: "second" })).status, 409);
    assert.equal((await f.request(f.path + "/prompt", "POST", { text: "queued", options: { streamingBehavior: "followUp" } })).status, 204);
    assert.equal(f.session.received[1].options?.streamingBehavior, "followUp");
    assert.equal((await f.request(f.path + "/abort", "POST", {})).status, 204);
    assert.equal((await first).status, 204);
  } finally { await f.api.close(); }
});
test("initialization failures dispose the session and expose only transport error", async () => {
  const f = await fixture({ startFail: true });
  try {
    const response = await f.request("/sessions", "POST", {});
    assert.equal(response.status, 500);
    assert.equal((await response.text()).includes("private-details"), false);
    assert.equal(f.session.disposed, true);
  } finally { await f.api.close(); }
});
