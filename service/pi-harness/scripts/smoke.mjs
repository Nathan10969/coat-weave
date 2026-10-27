import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const directory = mkdtempSync(join(tmpdir(), "coat-weave-smoke-"));
const probe = createServer();
probe.listen(0, "127.0.0.1");
await once(probe, "listening");
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
const token = "synthetic-smoke-service-token";
const child = spawn(process.execPath, [fileURLToPath(new URL("../dist/main.js", import.meta.url))], {
  // Do not inherit model keys, private endpoints or an existing account state.
  env: { PATH: process.env.PATH, HOME: directory, PI_API_HOST: "127.0.0.1", PI_API_PORT: String(port),
    PI_API_TOKEN: token, PI_PROJECT_CWD: join(directory, "workspace"),
    PI_AGENT_DIR: join(directory, "agent"), PI_EVENT_LOG_PATH: join(directory, "events.jsonl") },
  stdio: "ignore",
});
const exited = once(child, "exit");
try {
  const base = `http://127.0.0.1:${port}`;
  const headers = { Authorization: `Bearer ${token}` };
  let healthy = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error("Compiled service exited before becoming ready.");
    try { healthy = (await fetch(base + "/health", { headers, signal: AbortSignal.timeout(1000) })).ok; } catch {}
    if (healthy) break;
    await delay(100);
  }
  assert.equal(healthy, true);
  assert.equal((await fetch(base + "/health")).status, 401);
  const response = await fetch(base + "/v1/chat/completions", { method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ model: "coat-weave-pi", stream: false, messages: [{ role: "user", content: "Hello" }] }),
    signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.match(body.choices[0].message.content, /CoatWeave/);
  console.log("Compiled API smoke passed: health, authentication and offline greeting; no external model or database call.");
} finally {
  child.kill("SIGTERM");
  const timer = setTimeout(() => child.kill("SIGKILL"), 3000);
  try { await exited; } finally { clearTimeout(timer); rmSync(directory, { recursive: true, force: true }); }
}
