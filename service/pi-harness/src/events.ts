import { appendFileSync, chmodSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export interface EventLogRecord {
  session_id: string;
  event_type: string;
  tool_name: string | null;
  tool_call_id: string | null;
  op: string | null;
  doc_id: string | null;
  start_line: number | null;
  has_more: boolean | null;
  next_start_line: number | null;
  tool_result_success: boolean | null;
  timestamp: string;
}
type ObjectValue = Record<string, unknown>;
function object(value: unknown): ObjectValue {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as ObjectValue : {};
}
function text(value: unknown): string | null {
  return typeof value === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(value) ? value : null;
}
function integer(value: unknown): number | null {
  return Number.isSafeInteger(value) && Number(value) >= 0 ? value as number : null;
}
function boolean(value: unknown): boolean | null { return typeof value === "boolean" ? value : null; }
function payload(result: ObjectValue): ObjectValue {
  if (typeof result.success === "boolean") return result;
  const details = object(result.details);
  if (typeof details.success === "boolean") return details;
  for (const part of Array.isArray(result.content) ? result.content : []) {
    const value = object(part).text;
    if (typeof value !== "string") continue;
    try { const parsed = object(JSON.parse(value)); if (Object.keys(parsed).length) return parsed; } catch { /* Not structured tool data. */ }
  }
  return details;
}

export function createEventLogParser() {
  const calls = new Map<string, Pick<EventLogRecord, "op" | "doc_id" | "start_line">>();
  return (event: unknown, sessionId: string): EventLogRecord => {
    const raw = object(event);
    const nested = object(raw.assistantMessageEvent);
    const message = object(raw.message ?? nested.message ?? nested.partial);
    const messageTool = (Array.isArray(message.content) ? message.content : []).map(object)
      .find(item => item.type === "toolCall" || item.type === "toolResult");
    const tool = object(raw.toolCall ?? nested.toolCall ?? messageTool);
    const args = object(raw.args ?? raw.arguments ?? nested.args ?? nested.arguments ?? tool.arguments ?? tool.args);
    const statement = object(args.statement ?? args);
    const data = payload(object(raw.result ?? nested.result));
    const callId = text(raw.toolCallId ?? nested.toolCallId ?? tool.id);
    const key = `${sessionId}:${callId}`;
    const previous = callId ? calls.get(key) : undefined;
    const rawOp = text(statement.op ?? data.op) ?? previous?.op ?? null;
    const op = rawOp && ["describe", "query", "count", "count_distinct", "search", "read_document"].includes(rawOp) ? rawOp : null;
    const rawDoc = text(statement.doc_id ?? data.doc_id) ?? previous?.doc_id ?? null;
    const docId = rawDoc && /^(?:[A-Z]{2}\d{5,20}[A-Z]\d?|SYNTHETIC-[A-Z0-9_-]+)$/.test(rawDoc) ? rawDoc : null;
    const startLine = integer(statement.start_line ?? data.start_line) ?? previous?.start_line ?? null;
    const metadata = { op, doc_id: docId, start_line: startLine };
    if (callId && (op || docId || startLine !== null)) {
      if (calls.size >= 4096) calls.delete(calls.keys().next().value!);
      calls.set(key, metadata);
    }
    const error = boolean(raw.isError ?? nested.isError ?? tool.isError);
    const row: EventLogRecord = {
      session_id: text(sessionId) ?? "redacted",
      event_type: text(nested.type ?? raw.type) ?? "unknown",
      tool_name: text(raw.toolName ?? nested.toolName ?? tool.name),
      tool_call_id: callId,
      ...metadata,
      has_more: boolean(data.has_more),
      next_start_line: integer(object(data.next_statement).start_line),
      tool_result_success: boolean(data.success) ?? (error === null ? null : !error),
      timestamp: new Date().toISOString(),
    };
    if (raw.type === "tool_execution_end") calls.delete(key);
    if (raw.type === "agent_end") for (const key of calls.keys()) if (key.startsWith(`${sessionId}:`)) calls.delete(key);
    return row;
  };
}

export function createEventLogger(path?: string) {
  const parse = createEventLogParser();
  return (event: unknown, sessionId: string) => {
    if (!path) return;
    try {
      mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
      appendFileSync(path, `${JSON.stringify(parse(event, sessionId))}\n`, { encoding: "utf8", mode: 0o600 });
      chmodSync(path, 0o600);
    } catch { console.error("Pi event metadata could not be written."); }
  };
}
