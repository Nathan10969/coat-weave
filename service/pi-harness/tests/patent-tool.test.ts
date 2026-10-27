import assert from "node:assert/strict";
import { createServer } from "node:http";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { loadPatentSearchConfig } from "../src/patent-search/config.js";
import { queryPatentMilvus } from "../src/patent-search/tool.js";

test("tool configuration does not auto-discover account files or URL credentials", () => {
  const root = mkdtempSync(join(tmpdir(), "coat-weave-tool-config-"));
  mkdirSync(join(root, ".pi", "agent"), { recursive: true });
  writeFileSync(join(root, ".pi", "agent", "patent-search.json"), '{"url":"https://private.invalid"}');
  try {
    assert.equal(loadPatentSearchConfig({}, root).url.origin, "http://127.0.0.1:8002");
    assert.throws(() => loadPatentSearchConfig({ PATENT_SEARCH_URL: "https://user:synthetic@example.invalid" }), /HTTP/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test("patent tool uses the nested HTTP contract and never forwards raw backend failure", async () => {
  const saved = { url: process.env.PATENT_SEARCH_URL, token: process.env.PATENT_SEARCH_API_TOKEN,
    config: process.env.PATENT_SEARCH_CONFIG };
  let failed = false;
  const server = createServer(async (request, response) => {
    assert.equal(request.url, "/tools/patent_search");
    assert.equal(request.headers.authorization, "Bearer synthetic-tool-token");
    let body = "";
    for await (const chunk of request) body += chunk;
    assert.deepEqual(JSON.parse(body), { statement: { op: "describe" } });
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ result_id: "demo:1", collection: "demo_chunks", success: !failed,
      ...(failed ? { message: "PRIVATE_FAILURE_MUST_NOT_APPEAR" } : { schema: { fields: [] } }) }));
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  process.env.PATENT_SEARCH_URL = `http://127.0.0.1:${address.port}`;
  process.env.PATENT_SEARCH_API_TOKEN = "synthetic-tool-token";
  delete process.env.PATENT_SEARCH_CONFIG;
  try {
    assert.equal((await queryPatentMilvus({ op: "describe" })).success, true);
    failed = true;
    await assert.rejects(queryPatentMilvus({ op: "describe" }), error =>
      error instanceof Error && !error.message.includes("PRIVATE_FAILURE_MUST_NOT_APPEAR"));
    const abort = new AbortController(); abort.abort();
    await assert.rejects(queryPatentMilvus({ op: "describe" }, abort.signal), /取消/);
  } finally {
    for (const [key, value] of Object.entries({ PATENT_SEARCH_URL: saved.url,
      PATENT_SEARCH_API_TOKEN: saved.token, PATENT_SEARCH_CONFIG: saved.config })) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
