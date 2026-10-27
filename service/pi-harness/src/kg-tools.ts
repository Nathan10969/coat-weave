import { existsSync, readFileSync } from "node:fs";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { Type, type TSchema } from "typebox";
import { loadKgConfig } from "./retrieval-config.js";
export { loadKgConfig, retrievalBackend } from "./retrieval-config.js";

interface KgDefinition {
  name: string;
  endpoint: string;
  description: string;
  parameters: TSchema;
}

const schemaFile = new URL("./kg-tools-schema.json", import.meta.url);
if (!existsSync(schemaFile)) throw new Error("KG schemas are missing; run npm run build:kg from the full coat-weave repository.");

export const kgCatalog = JSON.parse(readFileSync(schemaFile, "utf8")) as {
  contract_version: string;
  classification_registry_version: string;
  retrieval_policy: { candidate_k: number; top_k: number; expand_top_k: number };
  tools: KgDefinition[];
};

// Only fixed input-validation messages from the KG service are public.
const validationReasons = new Set([
  "object_ids must be a non-empty list", "query must be a non-empty string",
  "doc_ids is required", "evidence_mode must be compact or full",
]);

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
      if (response.status === 400) {
        const data = await response.json().catch(() => null) as { error?: unknown } | null;
        if (data && typeof data.error === "string" && validationReasons.has(data.error))
          throw new Error(`KG service returned HTTP 400: ${data.error}`);
      } else await response.body?.cancel();
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
