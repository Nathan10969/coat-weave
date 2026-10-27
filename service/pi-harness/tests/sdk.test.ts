import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createAssistantMessageEventStream, type AssistantMessage, type Model } from "@earendil-works/pi-ai";
import { createAgentSession, createExtensionRuntime, ModelRuntime, SessionManager, SettingsManager, type ResourceLoader } from "@earendil-works/pi-coding-agent";
import { createPiHttpServer } from "../src/server.js";
import type { PiEvent } from "../src/contracts.js";

test("official SDK runs a prompt and HTTP forwards its actual events/messages", { timeout: 30000 }, async () => {
  const directory = await mkdtemp(join(tmpdir(), "coat-weave-pi-sdk-test-"));
  const model: Model<"openai-completions"> = {
    id: "offline-test", name: "Offline fixture", api: "openai-completions", provider: "openai",
    baseUrl: "https://offline.invalid/v1", reasoning: false, input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 32000, maxTokens: 1024,
  };
  const loader: ResourceLoader = {
    getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
    getSkills: () => ({ skills: [], diagnostics: [] }), getPrompts: () => ({ prompts: [], diagnostics: [] }),
    getThemes: () => ({ themes: [], diagnostics: [] }), getAgentsFiles: () => ({ agentsFiles: [] }),
    getSystemPrompt: () => "Offline SDK transport test.", getSystemPromptSource: () => undefined,
    getAppendSystemPrompt: () => [], getAppendSystemPromptSources: () => [], extendResources: () => {}, reload: async () => {},
  };
  const native: PiEvent[] = [];
  const runtime = await ModelRuntime.create({ authPath: join(directory, "auth.json"), modelsPath: join(directory, "models.json"), allowModelNetwork: false });
  await runtime.setRuntimeApiKey(model.provider, "offline-test-only");
  const { session } = await createAgentSession({ cwd: directory, agentDir: directory, model, modelRuntime: runtime,
    resourceLoader: loader, tools: [], sessionManager: SessionManager.inMemory(directory),
    settingsManager: SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } }),
  });
  session.agent.streamFunction = () => {
    const stream = createAssistantMessageEventStream();
    const message: AssistantMessage = {
      role: "assistant", content: [{ type: "text", text: "Official SDK offline reply" }], api: model.api,
      provider: model.provider, model: model.id, timestamp: 123, stopReason: "stop",
      usage: { input: 1, output: 2, cacheRead: 0, cacheWrite: 0, totalTokens: 3,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
    };
    stream.push({ type: "start", partial: message });
    stream.push({ type: "text_delta", contentIndex: 0, delta: "Official SDK offline reply", partial: message });
    stream.push({ type: "done", reason: "stop", message });
    return stream;
  };
  session.subscribe(event => native.push(JSON.parse(JSON.stringify(event))));
  const api = createPiHttpServer({ factory: async () => ({ session, start: () => session.bindExtensions({ mode: "json" }) }) });
  await new Promise<void>(resolve => api.server.listen(0, "127.0.0.1", resolve));
  const address = api.server.address(); assert.ok(address && typeof address !== "string");
  const base = `http://127.0.0.1:${address.port}`;
  const post = (path: string, value: unknown) => fetch(base + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
  try {
    assert.equal((await post("/sessions", {})).status, 201);
    const path = `/sessions/${session.sessionId}`;
    assert.equal((await post(path + "/prompt", { text: "Run an offline SDK test." })).status, 204);
    assert.ok(native.some(event => event.type === "message_update" && event.assistantMessageEvent.type === "text_delta"));
    assert.ok(native.some(event => event.type === "agent_settled"));
    assert.deepEqual(await (await fetch(base + path + "/messages")).json(), session.messages);
    const assistant = session.messages.find(message => message.role === "assistant");
    assert.equal(assistant?.stopReason, "stop");
    const stream = await fetch(base + path + "/events", { headers: { "Last-Event-ID": "0" } });
    const reader = stream.body!.getReader();
    const decoded: PiEvent[] = [];
    const decoder = new TextDecoder(); let buffer = "";
    while (decoded.length < native.length) {
      const chunk = await reader.read(); assert.equal(chunk.done, false);
      buffer += decoder.decode(chunk.value, { stream: true }); let end: number;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        const data = frame.split("\n").find(line => line.startsWith("data: "));
        if (data) decoded.push(JSON.parse(data.slice(6)));
      }
    }
    assert.deepEqual(decoded, native);
    await reader.cancel();
  } finally {
    await api.close();
    await rm(directory, { recursive: true, force: true });
  }
});
