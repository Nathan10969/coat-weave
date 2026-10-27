import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createAssistantMessageEventStream, type AssistantMessage, type TranscriptContext, type Model } from "@earendil-works/pi-ai";
import { createAgentSession, createExtensionRuntime, ModelRuntime, SessionManager, SettingsManager, type ResourceLoader } from "@earendil-works/pi-coding-agent";
import { prepareCompletionSession } from "../src/pi.js";
import { createPiHttpServer } from "../src/server.js";
import { appendCompletionSystemPrompt } from "../src/assistant-profile.js";

test("completion HTTP converts actual SDK events and seeds isolated model contexts", { timeout: 30000 }, async () => {
  const directory = await mkdtemp(join(tmpdir(), "pi-completion-sdk-test-"));
  const model: Model<"openai-completions"> = {
    id: "offline-test", name: "Offline fixture", api: "openai-completions", provider: "openai",
    baseUrl: "https://offline.invalid/v1", reasoning: false, input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 32000, maxTokens: 1024,
  };
  const runtime = await ModelRuntime.create({ authPath: join(directory, "auth.json"), modelsPath: join(directory, "models.json"), allowModelNetwork: false });
  await runtime.setRuntimeApiKey(model.provider, "offline-test-only");
  const contexts: TranscriptContext[] = [];
  const temperatures: (number | undefined)[] = [];
  const settled: boolean[] = [];
  const api = createPiHttpServer({ factory: async seed => {
    assert.ok(seed);
    const loader: ResourceLoader = {
      getExtensions: () => ({ extensions: [], errors: [], runtime: createExtensionRuntime() }),
      getSkills: () => ({ skills: [], diagnostics: [] }), getPrompts: () => ({ prompts: [], diagnostics: [] }),
      getThemes: () => ({ themes: [], diagnostics: [] }), getAgentsFiles: () => ({ agentsFiles: [] }),
      getSystemPrompt: () => "Offline completion test.", getSystemPromptSource: () => undefined,
      getAppendSystemPrompt: () => appendCompletionSystemPrompt([], seed.systemPrompt), getAppendSystemPromptSources: () => [], extendResources: () => {}, reload: async () => {},
    };
    const { session } = await createAgentSession({ cwd: directory, agentDir: directory, model, modelRuntime: runtime,
      resourceLoader: loader, tools: [], sessionManager: SessionManager.inMemory(directory),
      settingsManager: SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } }),
    });
    session.subscribe(event => { if (event.type === "agent_settled") settled.push(true); });
    session.agent.streamFunction = (_model, context, options) => {
      contexts.push(structuredClone(context));
      temperatures.push(options?.temperature);
      const stream = createAssistantMessageEventStream();
      const failed = contexts.length === 3;
      const message: AssistantMessage = {
        role: "assistant", content: failed ? [] : [{ type: "text", text: "结论 [WO123](patent://WO123)" }], api: model.api,
        provider: model.provider, model: model.id, timestamp: 123, stopReason: failed ? "error" : "stop",
        ...(failed ? { errorMessage: "private provider error" } : {}),
        usage: { input: 4, output: 3, cacheRead: 2, cacheWrite: 0, totalTokens: 9,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
      };
      stream.push({ type: "start", partial: message });
      if (failed) stream.push({ type: "error", reason: "error", error: message });
      else {
        stream.push({ type: "text_delta", contentIndex: 0, delta: "结论 ", partial: message });
        stream.push({ type: "text_delta", contentIndex: 0, delta: "[WO123](patent://WO123)", partial: message });
        stream.push({ type: "done", reason: "stop", message });
      }
      return stream;
    };
    prepareCompletionSession(session, seed);
    return { session, start: () => session.bindExtensions({ mode: "json" }) };
  } });
  await new Promise<void>(resolve => api.server.listen(0, "127.0.0.1", resolve));
  const address = api.server.address(); assert.ok(address && typeof address !== "string");
  const post = (body: unknown) => fetch(`http://127.0.0.1:${address.port}/chat/completions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  try {
    const first = await post({ model: "coat-weave-pi", stream: true, temperature: 0, enable_thinking: false,
      stream_options: { include_usage: true }, messages: [
      { role: "system", content: "Keep the citation." },
      { role: "user", content: "First question" }, { role: "assistant", content: "Earlier answer" },
      { role: "user", content: "Now compare" },
    ] });
    assert.equal(first.status, 200);
    const wire = await first.text();
    assert.equal(wire.match(/data: \[DONE\]/g)?.length, 1);
    assert.match(wire, /patent:\/\/WO123/);
    assert.match(wire, /"finish_reason":"stop"/);
    assert.equal(wire.includes("offline-test"), false);
    const context = contexts[0];
    const system = JSON.stringify(context.messages.filter(message => message.role === "system"));
    assert.ok(system.includes("Keep the citation."));
    assert.ok(system.includes("You are CoatWeave"));
    assert.ok(system.indexOf("Keep the citation.") < system.indexOf("You are CoatWeave"));
    assert.deepEqual(context.messages.filter(message => message.role !== "system").map(message => ({ role: message.role, text: typeof message.content === "string"
      ? message.content : message.content.filter(part => part.type === "text").map(part => part.text).join("") })), [
      { role: "user", text: "First question" }, { role: "assistant", text: "Earlier answer" },
      { role: "user", text: "Now compare" },
    ]);
    const second = await post({ model: "coat-weave-pi", stream: false, messages: [{ role: "user", content: "Independent request" }] });
    assert.equal(second.status, 200);
    const body = await second.json();
    assert.equal(body.choices[0].message.content, "结论 [WO123](patent://WO123)");
    assert.deepEqual(body.usage, { prompt_tokens: 6, completion_tokens: 3, total_tokens: 9 });
    assert.equal(contexts[1].messages.filter(message => message.role !== "system").length, 1);
    assert.equal(JSON.stringify(contexts[1]).includes("Keep the citation."), false);
    assert.deepEqual(temperatures, [0, undefined]);
    const failed = await post({ model: "coat-weave-pi", messages: [{ role: "user", content: "Fail this request" }] });
    assert.equal(failed.status, 502);
    const error = await failed.text();
    assert.match(error, /PI_COMPLETION_FAILED/);
    assert.equal(error.includes("private provider error"), false);
    assert.equal(error.includes("[DONE]"), false);
    assert.equal(settled.length, 3);
  } finally {
    await api.close();
    await rm(directory, { recursive: true, force: true });
  }
});
