import assert from "node:assert/strict";
import test from "node:test";
import { serverConfiguration } from "../src/config.js";

test("private state and privileged API modes require explicit safe configuration", () => {
  assert.equal(serverConfiguration({}, "/tmp/coat-weave").host, "127.0.0.1");
  assert.throws(() => serverConfiguration({ PI_API_HOST: "0.0.0.0" }), /PI_API_TOKEN/);
  assert.throws(() => serverConfiguration({ PI_TOOL_MODE: "workspace-write" }), /PI_API_TOKEN/);
  assert.throws(() => serverConfiguration({ PI_PROJECT_CWD: "/tmp/a", PI_AGENT_DIR: "/tmp/a/state" }), /isolated/);
  assert.throws(() => serverConfiguration({ PI_PROJECT_CWD: "/tmp/a", PI_EVENT_LOG_PATH: "/tmp/a/log.jsonl" }), /isolated/);
});
