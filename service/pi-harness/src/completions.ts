import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { AssistantMessage } from "@earendil-works/pi-ai";
import type { CompletionSeed, PiEvent, SessionFactory, SessionHandle } from "./contracts.js";
import { HttpFailure } from "./http-error.js";
import { isInitialGreeting, MATERIALS_WELCOME } from "./assistant-profile.js";

export const DEFAULT_CHAT_MODELS = ["coat-weave-pi", "coat-weave-harness"] as const;
const OUTPUT_LIMIT = 8 * 1024 * 1024;
const BUFFER_LIMIT = 1024 * 1024;
type ObjectValue = Record<string, unknown>;
function record(value: unknown): value is ObjectValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
export interface CompletionInput {
  model: string;
  stream: boolean;
  includeUsage: boolean;
  text: string;
  seed: CompletionSeed;
}

export function parseCompletion(value: unknown, models: readonly string[] = DEFAULT_CHAT_MODELS): CompletionInput {
  if (!record(value)) throw new HttpFailure(400, "INVALID_REQUEST", "Expected a JSON object.");
  const allowed = new Set(["model", "messages", "stream", "stream_options", "metadata", "enable_thinking", "n", "temperature"]);
  if (Object.keys(value).some(key => !allowed.has(key))) {
    throw new HttpFailure(400, "UNSUPPORTED_PARAMETER", "Unsupported completion parameter; see API.md for the supported subset.");
  }
  if (typeof value.model !== "string" || !models.includes(value.model)) {
    throw new HttpFailure(400, "MODEL_NOT_SUPPORTED", "Use a configured Pi chat model alias; the upstream model is server-owned.");
  }
  if (value.stream != null && typeof value.stream !== "boolean") throw new HttpFailure(400, "INVALID_REQUEST", "stream must be boolean.");
  if (value.n != null && value.n !== 1) throw new HttpFailure(400, "UNSUPPORTED_PARAMETER", "Only n=1 is supported.");
  if (value.temperature != null && (typeof value.temperature !== "number" || !Number.isFinite(value.temperature) || value.temperature < 0 || value.temperature > 2)) {
    throw new HttpFailure(400, "INVALID_REQUEST", "temperature must be a number between 0 and 2.");
  }
  if (value.enable_thinking != null && value.enable_thinking !== false) {
    throw new HttpFailure(400, "UNSUPPORTED_PARAMETER", "The completion endpoint does not expose reasoning; enable_thinking must be false.");
  }
  let includeUsage = false;
  if (value.stream_options != null) {
    if (!record(value.stream_options) || Object.keys(value.stream_options).some(key => key !== "include_usage") ||
        (value.stream_options.include_usage !== undefined && typeof value.stream_options.include_usage !== "boolean")) {
      throw new HttpFailure(400, "INVALID_REQUEST", "stream_options supports only boolean include_usage.");
    }
    includeUsage = value.stream_options.include_usage === true;
  }
  if (value.metadata != null) {
    if (!record(value.metadata)) throw new HttpFailure(400, "INVALID_REQUEST", "metadata must be an object.");
    for (const key of ["files", "docIds", "doc_ids", "legacyFileUrl", "legacy_file_url", "fileUrl", "dir_names"]) {
      const item = value.metadata[key];
      if (item != null && item !== "" && !(Array.isArray(item) && item.length === 0)) {
        throw new HttpFailure(400, "UNSUPPORTED_ATTACHMENTS", "External attachments/document scopes are not supported by this text completion endpoint.");
      }
    }
  }
  if (!Array.isArray(value.messages) || !value.messages.length || value.messages.length > 100) {
    throw new HttpFailure(400, "INVALID_MESSAGES", "messages must contain 1 to 100 text messages.");
  }
  const system: string[] = [];
  const conversation: CompletionSeed["history"] = [];
  for (const message of value.messages) {
    if (!record(message) || Object.keys(message).some(key => key !== "role" && key !== "content")) {
      throw new HttpFailure(400, "INVALID_MESSAGES", "Messages support only role and text content; client tool calls are not supported.");
    }
    let text: string;
    if (typeof message.content === "string") text = message.content;
    else if (Array.isArray(message.content) && message.content.length && message.content.every(part =>
      record(part) && part.type === "text" && typeof part.text === "string" && Object.keys(part).every(key => key === "type" || key === "text"))) {
      text = message.content.map(part => (part as { text: string }).text).join("");
    } else throw new HttpFailure(400, "INVALID_MESSAGES", "Only text content or text content-part arrays are supported.");
    if (!text.trim()) throw new HttpFailure(400, "INVALID_MESSAGES", "Message text must be nonempty.");
    if (message.role === "system" || message.role === "developer") {
      if (conversation.length) throw new HttpFailure(400, "INVALID_MESSAGES", "System/developer messages must precede conversation messages.");
      system.push(text);
    } else if (message.role === "user" || message.role === "assistant") conversation.push({ role: message.role, text });
    else throw new HttpFailure(400, "INVALID_MESSAGES", "Unsupported role; client tool messages are not supported.");
  }
  const latest = conversation.pop();
  if (!latest || latest.role !== "user") throw new HttpFailure(400, "INVALID_MESSAGES", "The last message must be a user message.");
  return { model: value.model, stream: value.stream !== false, includeUsage, text: latest.text,
    seed: { systemPrompt: system.join("\n\n"), history: conversation,
      ...(typeof value.temperature === "number" ? { temperature: value.temperature } : {}) } };
}

interface ActiveCompletion { cancel: (failure: HttpFailure) => void; done: Promise<void> }
export class ChatCompletions {
  private active = new Set<ActiveCompletion>();
  constructor(private readonly factory: SessionFactory, private readonly timeoutMs = 300_000,
    private readonly logEvent: (event: PiEvent, sessionId: string) => void = () => {}) {}
  get size(): number { return this.active.size; }

  async handle(request: IncomingMessage, response: ServerResponse, input: CompletionInput): Promise<void> {
    const id = `chatcmpl-${randomUUID()}`;
    const created = Math.floor(Date.now() / 1000);
    let handle: SessionHandle | undefined;
    let unsubscribe: (() => void) | undefined;
    let failure: HttpFailure | undefined;
    let rejectCancellation!: (failure: HttpFailure) => void;
    const cancelled = new Promise<never>((_resolve, reject) => { rejectCancellation = reject; });
    void cancelled.catch(() => {});
    let initializing: Promise<SessionHandle> | undefined;
    let starting: Promise<void> | undefined;
    let resolveDone!: () => void;
    const active: ActiveCompletion = {
      done: new Promise<void>(resolve => { resolveDone = resolve; }),
      cancel: error => {
        if (failure) return;
        failure = error;
        rejectCancellation(error);
        if (handle) void handle.session.abort().catch(() => {});
      },
    };
    this.active.add(active);
    const disconnected = () => {
      if (!response.writableEnded) active.cancel(new HttpFailure(499, "CLIENT_DISCONNECTED", "Client disconnected."));
    };
    response.on("close", disconnected);
    const timeout = setTimeout(() => active.cancel(new HttpFailure(504, "COMPLETION_TIMEOUT", "Pi completion timed out.")), this.timeoutMs);
    timeout.unref();

    let text = "";
    let messageText = "";
    let outputBytes = 0;
    let lastAssistant: AssistantMessage | undefined;
    let settled = false;
    const usage = { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
    let heartbeat: ReturnType<typeof setInterval> | undefined;
    const beginStream = () => {
      if (response.headersSent) return;
      response.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no" });
      response.flushHeaders();
      response.write(`data: ${JSON.stringify({ id, object: "chat.completion.chunk", created, model: input.model,
        choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }] })}\n\n`);
    };
    const frame = (choices: unknown[], finalUsage?: typeof usage) => {
      if (failure || response.destroyed) return;
      if (response.writableLength > BUFFER_LIMIT) {
        active.cancel(new HttpFailure(502, "SLOW_CLIENT", "Client cannot consume the completion stream."));
        response.destroy();
        return;
      }
      beginStream();
      response.write(`data: ${JSON.stringify({ id, object: "chat.completion.chunk", created, model: input.model,
        choices, ...(finalUsage ? { usage: finalUsage } : {}) })}\n\n`);
    };
    const append = (delta: string) => {
      if (failure || !delta) return;
      outputBytes += Buffer.byteLength(delta);
      if (outputBytes > OUTPUT_LIMIT) {
        active.cancel(new HttpFailure(502, "OUTPUT_LIMIT", "Completion exceeds the output limit."));
        return;
      }
      text += delta;
      messageText += delta;
      if (input.stream) frame([{ index: 0, delta: { content: delta }, finish_reason: null }]);
    };
    const onEvent = (event: PiEvent) => {
      if (handle) this.logEvent(event, handle.session.sessionId);
      if (failure) return;
      if (event.type === "agent_start") settled = false;
      if (event.type === "agent_settled") settled = true;
      if (event.type === "message_start" && event.message.role === "assistant") messageText = "";
      if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") append(event.assistantMessageEvent.delta);
      if (event.type === "auto_retry_start" && text) {
        active.cancel(new HttpFailure(502, "RETRY_AFTER_PARTIAL_OUTPUT", "Pi retried after emitting text; start a new request explicitly."));
      }
      if (event.type === "tool_execution_end") {
        const details = record(event.result) ? event.result.details : undefined;
        if (event.isError || (record(details) && details.success === false)) {
          active.cancel(new HttpFailure(502, "TOOL_FAILED", "A Pi tool failed; the completion was stopped. Check the backend before retrying."));
        }
      }
      if (event.type === "message_end" && event.message.role === "assistant") {
        const message = event.message;
        lastAssistant = message;
        usage.prompt_tokens += message.usage.input + message.usage.cacheRead + message.usage.cacheWrite;
        usage.completion_tokens += message.usage.output;
        usage.total_tokens = usage.prompt_tokens + usage.completion_tokens;
        if (message.stopReason !== "error" && message.stopReason !== "aborted") {
          const completeText = message.content.filter(part => part.type === "text").map(part => part.text).join("");
          // Some providers produce only a final snapshot. Never append both snapshot and deltas.
          if (!completeText.startsWith(messageText)) active.cancel(new HttpFailure(502, "INCONSISTENT_OUTPUT", "Pi changed text that was already streamed."));
          else append(completeText.slice(messageText.length));
        }
      }
    };
    try {
      if (request.aborted || response.destroyed) throw new HttpFailure(499, "CLIENT_DISCONNECTED", "Client disconnected.");
      let finishReason: "stop" | "length";
      if (isInitialGreeting(input.text, input.seed.history)) {
        // Fixed product copy: no model/tool call and no fabricated token usage.
        append(MATERIALS_WELCOME);
        finishReason = "stop";
      } else {
        initializing = this.factory(input.seed);
        handle = await Promise.race([initializing, cancelled]);
        if (failure || response.destroyed) throw failure ?? new HttpFailure(499, "CLIENT_DISCONNECTED", "Client disconnected.");
        unsubscribe = handle.session.subscribe(onEvent);
        starting = handle.start();
        await Promise.race([starting, cancelled]);
        if (failure) throw failure;
        if (input.stream) {
          heartbeat = setInterval(() => {
            if (failure || response.destroyed) return;
            if (response.writableLength > BUFFER_LIMIT) {
              active.cancel(new HttpFailure(502, "SLOW_CLIENT", "Client cannot consume the completion stream."));
              response.destroy();
              return;
            }
            beginStream();
            response.write(": keepalive\n\n");
          }, 15_000);
          heartbeat.unref();
        }
        await Promise.race([handle.session.prompt(input.text, { source: "rpc", expandPromptTemplates: false }), cancelled]);
        await Promise.race([handle.session.waitForIdle?.(), cancelled]);
        if (failure) throw failure;
        if (!settled || !lastAssistant || (lastAssistant.stopReason !== "stop" && lastAssistant.stopReason !== "length")) {
          throw new HttpFailure(502, "PI_COMPLETION_FAILED", "Pi did not complete a successful assistant response.");
        }
        if (!lastAssistant.content.some(part => part.type === "text" && part.text.trim())) {
          throw new HttpFailure(502, "EMPTY_RESPONSE", "Pi returned no final assistant text.");
        }
        finishReason = lastAssistant.stopReason;
      }
      if (input.stream) {
        frame([{ index: 0, delta: {}, finish_reason: finishReason }]);
        if (input.includeUsage) frame([], usage);
        if (failure) throw failure;
        response.end("data: [DONE]\n\n");
      } else {
        response.writeHead(200, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
        response.end(JSON.stringify({ id, object: "chat.completion", created, model: input.model,
          choices: [{ index: 0, message: { role: "assistant", content: text }, finish_reason: finishReason }], usage }));
      }
    } catch (error) {
      const problem = failure ?? (error instanceof HttpFailure ? error : new HttpFailure(502, "PI_COMPLETION_FAILED", "Pi completion failed; inspect server configuration."));
      if (!response.destroyed) {
        const data = { error: { code: problem.code, type: problem.status === 400 ? "invalid_request_error" : "upstream_error", message: problem.message } };
        if (!response.headersSent) {
          response.writeHead(problem.status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
          response.end(JSON.stringify(data));
        } else {
          // Incomplete chunked framing makes existing gateways record a failed stream,
          // rather than treating a clean EOF after an error as a successful answer.
          response.write(`data: ${JSON.stringify(data)}\n\n`, () => response.destroy());
        }
      }
    } finally {
      clearTimeout(timeout);
      if (heartbeat) clearInterval(heartbeat);
      response.off("close", disconnected);
      unsubscribe?.();
      // A disconnected client must not orphan a session that finishes loading later.
      if (!handle && initializing) handle = await initializing.catch(() => undefined);
      if (starting) await starting.catch(() => {});
      if (handle) {
        try {
          if (handle.close) await handle.close();
          else { try { await handle.session.abort(); } finally { handle.session.dispose(); } }
        } catch { try { handle.session.dispose(); } catch { /* Release the slot even if SDK cleanup fails. */ } }
      }
      this.active.delete(active);
      resolveDone();
    }
  }

  async close(): Promise<void> {
    const runs = [...this.active];
    for (const run of runs) run.cancel(new HttpFailure(503, "STOPPING", "Service is stopping."));
    await Promise.allSettled(runs.map(run => run.done));
  }
}
