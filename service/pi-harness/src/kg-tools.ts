import { readFileSync } from "node:fs";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type, type TSchema } from "typebox";

interface KgDefinition {
  name: string;
  endpoint: string;
  description: string;
  parameters: TSchema;
}

export const kgCatalog = JSON.parse(readFileSync(new URL("./kg-tools-schema.json", import.meta.url), "utf8")) as {
  contract_version: string;
  classification_registry_version: string;
  retrieval_policy: { candidate_k: number; top_k: number; expand_top_k: number };
  tools: KgDefinition[];
};

export function retrievalBackend(env: NodeJS.ProcessEnv = process.env): "milvus" | "kg" {
  const backend = env.PI_RETRIEVAL_BACKEND || "milvus";
  if (backend !== "milvus" && backend !== "kg") throw new Error("Invalid PI_RETRIEVAL_BACKEND; use milvus or kg.");
  return backend;
}

export function loadKgConfig(env: NodeJS.ProcessEnv = process.env) {
  let url: URL;
  try { url = new URL(env.PI_KG_TOOLS_URL || "http://127.0.0.1:8021"); }
  catch { throw new Error("Invalid PI_KG_TOOLS_URL."); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error("PI_KG_TOOLS_URL must be an HTTP(S) address without embedded credentials.");
  if (url.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
    throw new Error("Remote KG services require HTTPS.");
  const timeoutMs = Number(env.PI_KG_TIMEOUT_MS || "120000");
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 3600000)
    throw new Error("Invalid PI_KG_TIMEOUT_MS.");
  const apiToken = env.PI_KG_API_TOKEN;
  if (apiToken && !/^[\x21-\x7e]+$/.test(apiToken)) throw new Error("Invalid PI_KG_API_TOKEN.");
  return { url, timeoutMs, apiToken };
}

export async function queryKgTool(
  name: string,
  parameters: Record<string, unknown>,
  signal?: AbortSignal,
  config = loadKgConfig(),
): Promise<Record<string, unknown>> {
  const definition = kgCatalog.tools.find(tool => tool.name === name);
  if (!definition) throw new Error("Unsupported KG tool.");
  let body = { ...parameters };
  if (name === "kg_hybrid_search") {
    const { candidate_k, top_k } = kgCatalog.retrieval_policy;
    body = { ...body, candidate_k, top_k };
  }
  if (name === "kg_expand_hyperedge_multihop") {
    if (!Array.isArray(body.object_ids) || body.object_ids.length > kgCatalog.retrieval_policy.expand_top_k)
      throw new Error(`Select at most ${kgCatalog.retrieval_policy.expand_top_k} object IDs per expansion call.`);
    body.evidence_mode = "full";
  }
  const url = new URL(config.url);
  url.pathname = `${url.pathname.replace(/\/$/, "")}/tools/${definition.endpoint}`;
  const timeout = AbortSignal.timeout(config.timeoutMs);
  const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
  try {
    requestSignal.throwIfAborted();
    const response = await fetch(url, {
      method: "POST", redirect: "error", signal: requestSignal,
      headers: { "Content-Type": "application/json", "Accept": "application/json",
        ...(config.apiToken ? { Authorization: `Bearer ${config.apiToken}` } : {}) },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new Error(`KG service returned HTTP ${response.status}; inspect the service configuration or request.`);
    }
    const result: unknown = await response.json();
    if (result === null || typeof result !== "object" || Array.isArray(result))
      throw new Error("KG service returned an invalid response shape.");
    const record = result as Record<string, unknown>;
    if (record.tool !== definition.endpoint || typeof record.status !== "string")
      throw new Error("KG service returned an unexpected tool response.");
    if (record.status === "error") throw new Error("KG tool execution failed; inspect the request and backend configuration.");
    // Keep empty, unsupported and partial receipts, plus all full evidence, intact.
    return record;
  } catch (error) {
    if (signal?.aborted) throw new Error("KG request cancelled.");
    if (timeout.aborted) throw new Error("KG request timed out.");
    if (error instanceof SyntaxError) throw new Error("KG service returned invalid JSON.");
    if (error instanceof TypeError) throw new Error("Unable to connect to the configured KG service.");
    throw error;
  }
}

export const kgTools = kgCatalog.tools.map(definition => defineTool({
  name: definition.name,
  label: definition.endpoint,
  description: definition.description,
  parameters: Type.Unsafe<Record<string, unknown>>(definition.parameters),
  async execute(_toolCallId, parameters, signal) {
    const result = await queryKgTool(definition.name, parameters, signal);
    return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
  },
}));
