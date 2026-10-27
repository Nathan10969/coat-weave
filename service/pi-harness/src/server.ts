import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { timingSafeEqual } from "node:crypto";
import { createEventLogger } from "./events.js";
import type { HttpErrorResponse, PiEvent, PromptRequest, SessionFactory, SessionPort } from "./contracts.js";
import { ChatCompletions, DEFAULT_CHAT_MODELS, parseCompletion } from "./completions.js";
import { HttpFailure } from "./http-error.js";
type RecordValue = Record<string, unknown>;
function record(value: unknown): value is RecordValue {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function onlyKeys(value: RecordValue, keys: string[]): void {
  if (Object.keys(value).some(key => !keys.includes(key))) throw new HttpFailure(400, "INVALID_REQUEST", "Unknown request field.");
}
export function parsePrompt(value: unknown): PromptRequest {
  if (!record(value)) throw new HttpFailure(400, "INVALID_REQUEST", "Expected a JSON object.");
  onlyKeys(value, ["text", "options"]);
  if (typeof value.text !== "string" || !value.text.trim()) throw new HttpFailure(400, "INVALID_REQUEST", "text must be a nonempty string.");
  const options = value.options;
  if (options !== undefined) {
    if (!record(options)) throw new HttpFailure(400, "INVALID_REQUEST", "options must be an object.");
    onlyKeys(options, ["images", "expandPromptTemplates", "streamingBehavior"]);
    if (options.expandPromptTemplates !== undefined && typeof options.expandPromptTemplates !== "boolean") throw new HttpFailure(400, "INVALID_REQUEST", "Invalid expandPromptTemplates.");
    if (options.streamingBehavior !== undefined && options.streamingBehavior !== "steer" && options.streamingBehavior !== "followUp") throw new HttpFailure(400, "INVALID_REQUEST", "Invalid streamingBehavior.");
    if (options.images !== undefined) {
      if (!Array.isArray(options.images)) throw new HttpFailure(400, "INVALID_REQUEST", "images must be an array.");
      for (const image of options.images) {
        if (!record(image)) throw new HttpFailure(400, "INVALID_REQUEST", "Invalid image.");
        onlyKeys(image, ["type", "data", "mimeType"]);
        if (image.type !== "image" || typeof image.data !== "string" || !image.data || typeof image.mimeType !== "string" || !image.mimeType.startsWith("image/")) throw new HttpFailure(400, "INVALID_REQUEST", "Use SDK ImageContent: type, data, mimeType.");
      }
    }
  }
  return value as PromptRequest;
}
async function body(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 8 * 1024 * 1024) throw new HttpFailure(413, "BODY_TOO_LARGE", "Request exceeds 8 MiB.");
    chunks.push(Buffer.from(chunk));
  }
  if (!size) return {};
  if (!request.headers["content-type"]?.toLowerCase().startsWith("application/json")) throw new HttpFailure(415, "CONTENT_TYPE", "Use application/json.");
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw new HttpFailure(400, "INVALID_JSON", "Invalid JSON."); }
}
function json(response: ServerResponse, status: number, value: unknown): void {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" });
  response.end(JSON.stringify(value));
}
interface Frame { id: number; text: string; bytes: number }
class EventJournal {
  frames: Frame[] = [];
  sequence = 0;
  bytes = 0;
  clients = new Set<ServerResponse>();
  constructor(readonly maxBytes: number) {}
  publish(event: PiEvent): void {
    // Serialize immediately: the SDK can mutate streaming partials after this callback.
    const text = `id: ${++this.sequence}\nevent: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
    const frame = { id: this.sequence, text, bytes: Buffer.byteLength(text) };
    this.frames.push(frame);
    this.bytes += frame.bytes;
    while (this.bytes > this.maxBytes && this.frames.length) this.bytes -= this.frames.shift()!.bytes;
    for (const client of this.clients) {
      // Disconnect slow clients rather than accumulating unbounded snapshots.
      if (client.writableLength > this.maxBytes) client.destroy();
      else client.write(text);
    }
  }
  connect(response: ServerResponse, cursor: number): void {
    const earliest = this.frames[0]?.id ?? this.sequence + 1;
    if (cursor < earliest - 1 || cursor > this.sequence) throw new HttpFailure(409, "EVENT_CURSOR_EXPIRED", "Read messages and reconnect without Last-Event-ID; old event history is unavailable.");
    response.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no" });
    response.write(": connected\n\n");
    for (const frame of this.frames) if (frame.id > cursor) response.write(frame.text);
    this.clients.add(response);
    const heartbeat = setInterval(() => {
      if (response.writableLength > this.maxBytes) response.destroy();
      else response.write(": keepalive\n\n");
    }, 15000);
    heartbeat.unref();
    response.once("close", () => { clearInterval(heartbeat); this.clients.delete(response); });
  }
  close(): void { for (const client of this.clients) client.end(); this.clients.clear(); }
}
interface Entry { session: SessionPort; journal: EventJournal; unsubscribe: () => void; close: () => Promise<void>; prompts: number; deleting: boolean }
export interface ServerOptions {
  factory: SessionFactory;
  token?: string;
  maxSessions?: number;
  journalBytes?: number;
  chatModels?: readonly string[];
  completionTimeoutMs?: number;
  eventLogPath?: string;
}

export function createPiHttpServer(options: ServerOptions) {
  const sessions = new Map<string, Entry>();
  let creating = 0;
  let stopping = false;
  const writeEventLog = createEventLogger(options.eventLogPath);
  const completions = new ChatCompletions(options.factory, options.completionTimeoutMs, writeEventLog);
  function authorized(request: IncomingMessage): boolean {
    if (!options.token) return true;
    const actual = Buffer.from(request.headers.authorization ?? "");
    const expected = Buffer.from(`Bearer ${options.token}`);
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  }
  async function route(request: IncomingMessage, response: ServerResponse): Promise<void> {
    if (!authorized(request)) throw new HttpFailure(401, "UNAUTHORIZED", "Invalid API token.");
    if (stopping) throw new HttpFailure(503, "STOPPING", "Service is stopping.");
    const url = new URL(request.url ?? "/", "http://localhost");
    if (url.search) throw new HttpFailure(400, "INVALID_REQUEST", "Query parameters are not supported.");
    if (url.pathname === "/health" && request.method === "GET") {
      json(response, 200, { ok: true });
      return;
    }
    if (url.pathname === "/chat/completions" || url.pathname === "/v1/chat/completions") {
      if (request.method !== "POST") throw new HttpFailure(405, "METHOD_NOT_ALLOWED", "Unsupported method for this endpoint.");
      const input = parseCompletion(await body(request), options.chatModels ?? DEFAULT_CHAT_MODELS);
      if (stopping) throw new HttpFailure(503, "STOPPING", "Service is stopping.");
      if (sessions.size + creating + completions.size >= (options.maxSessions ?? 16)) throw new HttpFailure(503, "SESSION_LIMIT", "Session capacity reached; retry after an active request completes.");
      await completions.handle(request, response, input);
      return;
    }
    if (url.pathname === "/sessions" && request.method === "POST") {
      const value = await body(request);
      if (!record(value) || Object.keys(value).length) throw new HttpFailure(400, "INVALID_REQUEST", "Session creation expects {}. Runtime configuration belongs to the server.");
      if (sessions.size + creating + completions.size >= (options.maxSessions ?? 16)) throw new HttpFailure(503, "SESSION_LIMIT", "Delete an unused session before creating another.");
      creating++;
      let entry: Entry | undefined;
      try {
        const handle = await options.factory();
        if (stopping) { handle.session.dispose(); throw new HttpFailure(503, "STOPPING", "Service is stopping."); }
        const journal = new EventJournal(options.journalBytes ?? 16 * 1024 * 1024);
        entry = { session: handle.session, journal, unsubscribe: handle.session.subscribe(event => { journal.publish(event); writeEventLog(event, handle.session.sessionId); }),
          close: handle.close ?? (async () => { try { await handle.session.abort(); } finally { handle.session.dispose(); } }),
          prompts: 0, deleting: false };
        await handle.start();
        if (stopping) throw new HttpFailure(503, "STOPPING", "Service is stopping.");
        sessions.set(handle.session.sessionId, entry);
        json(response, 201, { sessionId: handle.session.sessionId });
      } catch (error) {
        entry?.unsubscribe();
        if (entry) await entry.close().catch(() => {});
        if (error instanceof HttpFailure) throw error;
        throw new HttpFailure(500, "SESSION_CREATE_FAILED", "Pi session initialization failed; check server model and extension configuration.");
      } finally { creating--; }
      return;
    }
    const match = /^\/sessions\/([A-Za-z0-9_-]+)(?:\/(events|messages|prompt|abort))?$/.exec(url.pathname);
    if (!match) throw new HttpFailure(404, "NOT_FOUND", "Unknown endpoint.");
    const entry = sessions.get(match[1]);
    if (!entry || entry.deleting) throw new HttpFailure(404, "SESSION_NOT_FOUND", "Session is not active in this API process.");
    if (match[2] === "events" && request.method === "GET") {
      const header = request.headers["last-event-id"];
      if (header !== undefined && (typeof header !== "string" || !/^\d+$/.test(header) || !Number.isSafeInteger(Number(header)))) throw new HttpFailure(400, "INVALID_CURSOR", "Last-Event-ID must be a nonnegative integer.");
      entry.journal.connect(response, header === undefined ? entry.journal.sequence : Number(header));
    } else if (match[2] === "messages" && request.method === "GET") {
      json(response, 200, entry.session.messages);
    } else if (match[2] === "prompt" && request.method === "POST") {
      const input = parsePrompt(await body(request));
      if (entry.deleting) throw new HttpFailure(404, "SESSION_NOT_FOUND", "Session is closing.");
      if (entry.prompts && !entry.session.isStreaming) throw new HttpFailure(409, "SESSION_BUSY", "Pi is preparing or finishing a prompt; retry after the current request returns.");
      if ((entry.prompts || entry.session.isStreaming) && !input.options?.streamingBehavior) throw new HttpFailure(409, "SESSION_BUSY", "Provide options.streamingBehavior: steer or followUp while busy.");
      entry.prompts++;
      try {
        await entry.session.prompt(input.text, { ...input.options, source: "rpc" });
        response.writeHead(204); response.end();
      } catch {
        throw new HttpFailure(500, "PI_PROMPT_REJECTED", "Pi prompt rejected or failed. Inspect native events/messages for model errors; do not automatically resubmit.");
      } finally { entry.prompts--; }
    } else if (match[2] === "abort" && request.method === "POST") {
      const value = await body(request);
      if (!record(value) || Object.keys(value).length) throw new HttpFailure(400, "INVALID_REQUEST", "Abort expects {}.");
      await entry.session.abort();
      response.writeHead(204); response.end();
    } else if (!match[2] && request.method === "DELETE") {
      entry.deleting = true;
      try { await entry.close(); }
      finally { entry.unsubscribe(); entry.journal.close(); sessions.delete(match[1]); }
      response.writeHead(204); response.end();
    } else throw new HttpFailure(405, "METHOD_NOT_ALLOWED", "Unsupported method for this endpoint.");
  }
  const server = createServer((request, response) => {
    void route(request, response).catch(error => {
      if (response.destroyed) return;
      if (response.headersSent) { response.destroy(); return; }
      const failure = error instanceof HttpFailure ? error : new HttpFailure(500, "INTERNAL_ERROR", "API request failed.");
      const data: HttpErrorResponse = { error: { code: failure.code, message: failure.message } };
      json(response, failure.status, data);
    });
  });
  server.timeout = 0;
  return {
    server,
    async close() {
      stopping = true;
      await completions.close();
      await Promise.allSettled([...sessions.values()].map(async entry => {
        entry.deleting = true;
        try { await entry.close(); }
        finally { entry.unsubscribe(); entry.journal.close(); }
      }));
      sessions.clear();
      await new Promise<void>((resolve, reject) => {
        server.close(error => error ? reject(error) : resolve());
        server.closeAllConnections();
      });
    },
  };
}
