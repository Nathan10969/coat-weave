import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createAssistantMessageEventStream, type AssistantMessage, type ToolCall } from "@earendil-works/pi-ai";
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { prepareLocalState, serverConfiguration } from "../src/config.js";
import { officialPiFactory } from "../src/pi.js";

test("migrated factory registers explicit tools and SDK blocks unsafe writes/reads", { timeout: 30000 }, async () => {
  const directory = mkdtempSync(join(tmpdir(), "coat-weave-factory-"));
  const env = { PI_TOOL_MODE: "workspace-write", PI_API_TOKEN: "synthetic-test-service-token",
    PI_LLM_BASE_URL: "https://offline.invalid/v1", PI_LLM_MODEL: "offline-fixture", PI_LLM_API_KEY: "synthetic-only" };
  const config = serverConfiguration(env, directory);
  prepareLocalState(config, env);
  const previousKey = process.env.PI_LLM_API_KEY;
  process.env.PI_LLM_API_KEY = env.PI_LLM_API_KEY;
  assert.doesNotMatch(readFileSync(join(config.agentDir, "models.json"), "utf8"), /synthetic-only/);
  writeFileSync(join(config.cwd, ".env"), "PRIVATE_FIXTURE_MUST_NOT_APPEAR");
  mkdirSync(join(config.cwd, ".pi"));
  writeFileSync(join(config.cwd, ".pi", "SYSTEM.md"), "PROJECT_INJECTION_MUST_NOT_APPEAR");
  let handle: Awaited<ReturnType<ReturnType<typeof officialPiFactory>>> | undefined;
  try {
    handle = await officialPiFactory(config.cwd, config.agentDir, config.toolMode)();
    const session = handle.session as AgentSession;
    assert.deepEqual(new Set(session.getActiveToolNames()), new Set(["read", "patent_search", "write", "edit"]));
    let calls = 0;
    session.agent.streamFunction = (_model, context) => {
      assert.doesNotMatch(JSON.stringify(context), /PROJECT_INJECTION_MUST_NOT_APPEAR/);
      const toolCalls: ToolCall[] = [
        { type: "toolCall" as const, id: "write_ok", name: "write", arguments: { path: "outputs/report.md", content: "Synthetic report" } },
        { type: "toolCall" as const, id: "write_denied", name: "write", arguments: { path: "outside.md", content: "Blocked" } },
        { type: "toolCall" as const, id: "read_denied", name: "read", arguments: { path: ".env" } },
      ];
      const call = toolCalls[calls++];
      const model = session.model!;
      const message: AssistantMessage = { role: "assistant", content: call ? [call] : [{ type: "text", text: "Done" }],
        api: model.api, provider: model.provider, model: model.id, timestamp: 1, stopReason: call ? "toolUse" : "stop",
        usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
      const stream = createAssistantMessageEventStream();
      stream.push({ type: "start", partial: message });
      if (call) stream.push({ type: "toolcall_end", contentIndex: 0, toolCall: call, partial: message });
      stream.push({ type: "done", reason: message.stopReason as "stop" | "toolUse", message });
      return stream;
    };
    await handle.start();
    await session.prompt("Exercise the workspace policy offline.");
    assert.equal(readFileSync(join(config.cwd, "outputs", "report.md"), "utf8"), "Synthetic report");
    assert.equal(existsSync(join(config.cwd, "outside.md")), false);
    const results = session.messages.filter(message => message.role === "toolResult");
    assert.equal(results.length, 3);
    assert.deepEqual(results.map(message => message.isError), [false, true, true]);
    assert.doesNotMatch(JSON.stringify(results), /PRIVATE_FIXTURE_MUST_NOT_APPEAR/);
  } finally {
    await handle?.close?.();
    if (previousKey === undefined) delete process.env.PI_LLM_API_KEY;
    else process.env.PI_LLM_API_KEY = previousKey;
    rmSync(directory, { recursive: true, force: true });
  }
});
