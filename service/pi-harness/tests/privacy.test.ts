import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { createEventLogParser } from "../src/events.js";
import { assertToolPath, toolNamesForMode } from "../src/tool-policy.js";

test("tool logs pair nested statement metadata without retaining raw content", () => {
  const parse = createEventLogParser();
  const start = parse({ type: "tool_execution_start", toolName: "patent_search", toolCallId: "call_1",
    args: { statement: { op: "read_document", doc_id: "WO2018148306A1", start_line: 201 },
      private_text: "PRIVATE_VALUE_MUST_NOT_APPEAR" } }, "session-1");
  assert.equal(start.op, "read_document");
  assert.equal(start.doc_id, "WO2018148306A1");
  const end = parse({ type: "tool_execution_end", toolName: "patent_search", toolCallId: "call_1", isError: false,
    result: { content: [{ type: "text", text: JSON.stringify({ success: true, has_more: true,
      content: "PRIVATE_VALUE_MUST_NOT_APPEAR", next_statement: { start_line: 401 } }) }] } }, "session-1");
  assert.equal(end.op, "read_document");
  assert.equal(end.start_line, 201);
  assert.equal(end.next_start_line, 401);
  assert.equal(end.tool_result_success, true);
  assert.equal(Object.keys(end).length, 11);
  assert.doesNotMatch(JSON.stringify(end), /PRIVATE_VALUE_MUST_NOT_APPEAR/);
});

test("nested Pi message events yield metadata; invalid document IDs are omitted", () => {
  const parse = createEventLogParser();
  const row = parse({ type: "message_update", assistantMessageEvent: { type: "toolcall_end",
    toolCall: { id: "call_2", name: "patent_search", arguments: { statement: { op: "search", doc_id: "private@example.invalid" } } } } }, "session-2");
  assert.equal(row.tool_name, "patent_search");
  assert.equal(row.op, "search");
  assert.equal(row.doc_id, null);
});

test("tool modes are explicit and read-only by default", () => {
  assert.deepEqual(toolNamesForMode(), ["read", "patent_search"]);
  assert.deepEqual(toolNamesForMode("workspace-write"), ["read", "patent_search", "edit", "write"]);
  assert.deepEqual(toolNamesForMode("shell"), ["read", "patent_search", "edit", "write", "bash"]);
  assert.throws(() => toolNamesForMode("invalid"), /PI_TOOL_MODE/);
});

test("file access rejects secrets, traversal and symlink escapes; writes target outputs", () => {
  const root = mkdtempSync(join(tmpdir(), "coat-weave-policy-"));
  const workspace = join(root, "workspace");
  mkdirSync(join(workspace, "outputs"), { recursive: true });
  writeFileSync(join(workspace, "paper.md"), "synthetic text");
  writeFileSync(join(workspace, ".env"), "LOCAL_FIXTURE=not-a-real-credential");
  writeFileSync(join(root, "outside.md"), "outside");
  symlinkSync(join(root, "outside.md"), join(workspace, "linked.md"));
  try {
    assert.doesNotThrow(() => assertToolPath(workspace, "read", "paper.md"));
    assert.doesNotThrow(() => assertToolPath(workspace, "write", "outputs/report.md"));
    for (const path of ["../outside.md", "linked.md", ".env", "auth.json", ".git/config"])
      assert.throws(() => assertToolPath(workspace, "read", path), /denied/);
    assert.throws(() => assertToolPath(workspace, "write", "paper.md"), /outputs/);
    symlinkSync(root, join(workspace, "outputs", "link"));
    assert.throws(() => assertToolPath(workspace, "write", "outputs/link/new.md"), /denied/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
