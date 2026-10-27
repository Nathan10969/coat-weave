import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";
import { kgCatalog, kgTools, loadKgConfig, queryKgTool, retrievalBackend } from "../src/kg-tools.js";

test("KG schemas reuse all five shared tools, classification axes and service budgets", () => {
  assert.equal(kgTools.length, 5);
  assert.deepEqual(kgCatalog.retrieval_policy, { candidate_k: 100, top_k: 50, expand_top_k: 30 });
  const schema = kgCatalog.tools.find(tool => tool.name === "kg_hybrid_search")!.parameters as any;
  for (const key of ["candidate_k", "top_k", "expand_top_k"]) assert.equal(schema.properties[key], undefined);
  for (const key of ["application_domains", "coating_functions", "coating_layers", "supply_forms", "film_processes"])
    assert.ok(schema.properties.filters.properties[key].items.enum.length);
  assert.ok(kgCatalog.classification_registry_version);
  assert.equal(retrievalBackend({}), "milvus");
  assert.equal(retrievalBackend({ PI_RETRIEVAL_BACKEND: "kg" }), "kg");
  assert.throws(() => retrievalBackend({ PI_RETRIEVAL_BACKEND: "automatic" }), /PI_RETRIEVAL_BACKEND/);
  for (const url of ["https://user:synthetic@offline.invalid", "http://offline.invalid", "http://127.0.0.1/?token=x"])
    assert.throws(() => loadKgConfig({ PI_KG_TOOLS_URL: url }));
  assert.throws(() => loadKgConfig({ PI_KG_TIMEOUT_MS: "0" }));
  assert.throws(() => loadKgConfig({ PI_KG_API_TOKEN: "bad\nheader" }));
});

test("KG adapter forwards original filters, source IDs and receipts with full evidence", async () => {
  const calls: { path: string; args: any }[] = [];
  const quote = "q".repeat(2_100_000);
  const server = createServer(async (request, response) => {
    assert.equal(request.headers.authorization, "Bearer synthetic-kg-token");
    let body = "";
    for await (const chunk of request) body += chunk;
    const args = JSON.parse(body);
    const path = request.url!;
    calls.push({ path, args });
    const tool = path.split("/").at(-1)!;
    const payload = tool === "kg.expand_hyperedge_multihop"
      ? { tool, status: "partial", items: [{ object_id: args.object_ids[0], facts: [{ quote }] }], failures: [{ object_id: "source::missing", reason: "not_found" }] }
      : { tool, status: tool === "kg.lookup_vocabulary" ? "unsupported" : "ok", items: [],
        query_interpretation: { group_by: args.group_by, count_unit: "patent" },
        applied_filters: args.filters, adjacent: { items: [{ doc_id: "SYNTHETIC-ADJACENT" }], relaxed_axes: ["materials"] },
        pagination: { next_offset: 100 }, unsupported_constraints: ["synthetic_unresolved"],
        route_adjustments: ["synthetic_receipt"] };
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(payload));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  const config = loadKgConfig({ PI_KG_TOOLS_URL: `http://127.0.0.1:${address.port}`, PI_KG_API_TOKEN: "synthetic-kg-token" });
  try {
    const filters = { application_domains: ["marine"], coating_functions: ["anticorrosion"], assignees: ["Synthetic Company"],
      material_groups: [{ values: ["GMA", "glycidyl methacrylate"], match: "any" }] };
    const search = await queryKgTool("kg_hybrid_search", { query: "original user question", offset: 50, filters }, undefined, config);
    assert.deepEqual(calls[0], { path: "/tools/kg.hybrid_search", args: { query: "original user question", offset: 50, filters, candidate_k: 100, top_k: 50 } });
    assert.deepEqual(search.applied_filters, filters);
    assert.deepEqual(search.route_adjustments, ["synthetic_receipt"]);
    assert.deepEqual(search.unsupported_constraints, ["synthetic_unresolved"]);
    assert.equal((search.adjacent as any).items[0].doc_id, "SYNTHETIC-ADJACENT");
    const expanded = await queryKgTool("kg_expand_hyperedge_multihop", { object_ids: ["source-a::doc::sample"] }, undefined, config);
    assert.equal(calls[1].args.evidence_mode, "full");
    assert.equal((expanded.items as any[])[0].facts[0].quote, quote);
    assert.equal(expanded.status, "partial");
    assert.equal((expanded.failures as any[]).length, 1);
    const group_by = ["assignee", "coating_functions", "publication_year"];
    const stats = await queryKgTool("kg_sql_aggregate", { intent: "group_count", target: "doc_id", group_by, filters }, undefined, config);
    assert.deepEqual((stats.query_interpretation as any).group_by, group_by);
    const unsupported = await queryKgTool("kg_lookup_vocabulary", { query: "synthetic unknown", dimension: "coating_functions" }, undefined, config);
    assert.equal(unsupported.status, "unsupported");
    await queryKgTool("kg_doc_field_scan", { doc_ids: ["source-a::doc"], query: "test conditions", include_evidence: true }, undefined, config);
    assert.equal(calls[4].args.doc_ids[0], "source-a::doc");
    await assert.rejects(queryKgTool("kg_expand_hyperedge_multihop", { object_ids: Array(31).fill("id") }, undefined, config), /30/);
    assert.equal(calls.length, 5);
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

test("KG failures, malformed JSON, cancellation and timeout are not empty results", async () => {
  let mode = "http";
  let calls = 0;
  const server = createServer((_request, response) => {
    calls++;
    if (mode === "timeout") return;
    response.writeHead(mode === "http" ? 400 : 200, { "Content-Type": "application/json" });
    response.end(mode === "json" ? "PRIVATE_INVALID_JSON" : JSON.stringify({ tool: "kg.sql_aggregate", status: "error", error: "PRIVATE_RAW_ERROR" }));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  const config = loadKgConfig({ PI_KG_TOOLS_URL: `http://127.0.0.1:${address.port}`, PI_KG_TIMEOUT_MS: "50" });
  const args = { intent: "distinct_count", target: "doc_id" };
  try {
    for (mode of ["http", "json", "error"]) {
      await assert.rejects(queryKgTool("kg_sql_aggregate", args, undefined, config), error =>
        error instanceof Error && !error.message.includes("PRIVATE_"));
    }
    assert.equal(calls, 3, "no hidden retry or fallback");
    const controller = new AbortController(); controller.abort();
    await assert.rejects(queryKgTool("kg_sql_aggregate", args, controller.signal, config), /cancelled/);
    assert.equal(calls, 3);
    mode = "timeout";
    await assert.rejects(queryKgTool("kg_sql_aggregate", args, undefined, config), /timed out/);
    await assert.rejects(queryKgTool("not_a_tool", {}, undefined, config), /Unsupported/);
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
