import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createAssistantMessageEventStream, type AssistantMessage, type ToolCall } from "@earendil-works/pi-ai";
import type { AgentSession } from "@earendil-works/pi-coding-agent";
import { prepareLocalState, serverConfiguration } from "../src/config.js";
import { kgTools } from "../src/kg-tools.js";
import { officialPiFactory } from "../src/pi.js";
import { createEventLogParser } from "../src/events.js";

test("actual Pi SDK calls existing KG endpoints and receives complete source-bound evidence", { timeout: 30000 }, async () => {
  const directory = mkdtempSync(join(tmpdir(), "coat-weave-kg-sdk-"));
  const longQuote = "synthetic evidence ".repeat(120000);
  const received: string[] = [];
  const requests: any[] = [];
  const filters = { application_domains: ["marine"], coating_functions: ["anticorrosion"] };
  const server = createServer(async (request, response) => {
    let body = "";
    for await (const chunk of request) body += chunk;
    requests.push(JSON.parse(body));
    const tool = request.url!.split("/").at(-1)!;
    received.push(tool);
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ tool, status: "ok", count_unit: "patent", count: 2,
      items: tool === "kg.expand_hyperedge_multihop"
        ? [{ object_id: "A::DOC::S1", sample_id: "S1", facts: [{ value: 7, unit: "wt%", quote: longQuote }] }]
        : [{ object_id: "A::DOC::S1", doc_id: "DOC" }],
      applied_filters: filters, query_interpretation: { group_by: ["assignee", "publication_year"] } }));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  const previous = { key: process.env.PI_LLM_API_KEY, url: process.env.PI_KG_TOOLS_URL };
  const env = { PI_RETRIEVAL_BACKEND: "kg", PI_LLM_BASE_URL: "https://offline.invalid/v1",
    PI_LLM_MODEL: "offline-fixture", PI_LLM_API_KEY: "synthetic-only" };
  process.env.PI_LLM_API_KEY = env.PI_LLM_API_KEY;
  process.env.PI_KG_TOOLS_URL = `http://127.0.0.1:${address.port}`;
  const config = serverConfiguration(env, directory);
  prepareLocalState(config, env);
  let handle: Awaited<ReturnType<ReturnType<typeof officialPiFactory>>> | undefined;
  try {
    handle = await officialPiFactory(config.cwd, config.agentDir, config.toolMode, config.retrievalBackend)();
    const session = handle.session as AgentSession;
    assert.deepEqual(new Set(session.getActiveToolNames()), new Set(["read", ...kgTools.map(tool => tool.name)]));
    const calls: ToolCall[] = [
      { type: "toolCall", id: "kg_search", name: "kg_hybrid_search", arguments: { query: "original marine question", filters } },
      { type: "toolCall", id: "kg_expand", name: "kg_expand_hyperedge_multihop", arguments: { object_ids: ["A::DOC::S1"] } },
      { type: "toolCall", id: "kg_count", name: "kg_sql_aggregate", arguments: { intent: "group_count", target: "doc_id", filters, group_by: ["assignee", "publication_year"] } },
    ];
    let round = 0;
    session.agent.streamFunction = (_model, context) => {
      if (round === 2) {
        const messages = context.messages as any[];
        const result = messages.find(message => message.role === "toolResult" && message.toolName === "kg_expand_hyperedge_multihop");
        assert.ok(result, "full expansion reaches the model, not just the HTTP client");
        const evidence = JSON.parse(result.content[0].text);
        assert.equal(evidence.items[0].facts[0].quote, longQuote);
        assert.equal(evidence.items[0].facts[0].unit, "wt%");
        assert.equal(evidence.items[0].object_id, "A::DOC::S1");
      }
      const call = calls[round++];
      const model = session.model!;
      const message: AssistantMessage = { role: "assistant", content: call ? [call] : [{ type: "text", text: "Synthetic local KG workflow complete." }],
        api: model.api, provider: model.provider, model: model.id, timestamp: 1,
        stopReason: call ? "toolUse" : "stop", usage: { input: 1, output: 1, cacheRead: 0, cacheWrite: 0, totalTokens: 2,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } };
      const stream = createAssistantMessageEventStream();
      stream.push({ type: "start", partial: message });
      if (call) stream.push({ type: "toolcall_end", contentIndex: 0, toolCall: call, partial: message });
      stream.push({ type: "done", reason: message.stopReason as "stop" | "toolUse", message });
      return stream;
    };
    const parse = createEventLogParser();
    const rows: ReturnType<typeof parse>[] = [];
    session.subscribe(event => rows.push(parse(event, "synthetic-kg-session")));
    await handle.start();
    await session.prompt("original marine question");
    assert.deepEqual(received, ["kg.hybrid_search", "kg.expand_hyperedge_multihop", "kg.sql_aggregate"]);
    assert.equal(requests[0].query, "original marine question");
    assert.deepEqual(requests[0].filters, filters);
    assert.deepEqual(requests[1], { object_ids: ["A::DOC::S1"], evidence_mode: "full" });
    assert.deepEqual(requests[2].group_by, ["assignee", "publication_year"]);
    assert.equal(session.messages.filter(message => message.role === "toolResult" && message.isError).length, 0);
    assert.ok(rows.some(row => row.tool_name === "kg_hybrid_search" && row.tool_call_id === "kg_search"));
    assert.ok(rows.every(row => Object.keys(row).length === 11));
    assert.doesNotMatch(JSON.stringify(rows), /synthetic evidence|original marine question|wt%/);
  } finally {
    await handle?.close?.();
    if (previous.key === undefined) delete process.env.PI_LLM_API_KEY; else process.env.PI_LLM_API_KEY = previous.key;
    if (previous.url === undefined) delete process.env.PI_KG_TOOLS_URL; else process.env.PI_KG_TOOLS_URL = previous.url;
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    rmSync(directory, { recursive: true, force: true });
  }
});
