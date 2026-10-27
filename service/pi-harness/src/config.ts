import { mkdirSync, realpathSync, writeFileSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { toolNamesForMode } from "./tool-policy.js";

export function serverConfiguration(env: NodeJS.ProcessEnv, base = process.cwd()) {
  const toolMode = env.PI_TOOL_MODE || "read-only";
  toolNamesForMode(toolMode);
  const host = env.PI_API_HOST || "127.0.0.1";
  const token = env.PI_API_TOKEN?.trim() || undefined;
  if ((!['127.0.0.1', '::1', 'localhost'].includes(host) || toolMode !== "read-only") && !token)
    throw new Error("PI_API_TOKEN is required for non-loopback access or privileged tools.");
  if (token && (!/^[\x21-\x7e]{24,256}$/.test(token))) throw new Error("PI_API_TOKEN must contain 24-256 printable non-whitespace characters.");
  const port = Number(env.PI_API_PORT || "8003");
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error("Invalid PI_API_PORT.");
  const timeout = Number(env.PI_CHAT_TIMEOUT_MS || "300000");
  if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 2147483647) throw new Error("Invalid PI_CHAT_TIMEOUT_MS.");
  const cwd = resolve(base, env.PI_PROJECT_CWD || ".local/workspace");
  const agentDir = resolve(base, env.PI_AGENT_DIR || ".local/agent");
  const logPath = resolve(base, env.PI_EVENT_LOG_PATH || ".local/events.jsonl");
  const within = (parent: string, path: string) => {
    const value = relative(parent, path);
    return !isAbsolute(value) && value !== ".." && !value.startsWith(`..${sep}`);
  };
  if (within(cwd, agentDir) || within(agentDir, cwd) || within(cwd, logPath))
    throw new Error("Workspace, agent state and event log locations must be isolated.");
  const chatModel = env.PI_CHAT_MODEL?.trim() || "coat-weave-pi";
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(chatModel)) throw new Error("Invalid PI_CHAT_MODEL.");
  return { cwd, agentDir, logPath, host, port, token, timeout, toolMode, chatModel };
}

export function prepareLocalState(config: ReturnType<typeof serverConfiguration>, env: NodeJS.ProcessEnv): void {
  for (const path of [config.cwd, resolve(config.cwd, "outputs"), config.agentDir]) mkdirSync(path, { recursive: true, mode: 0o700 });
  const workspace = realpathSync(config.cwd);
  const state = realpathSync(config.agentDir);
  if (workspace === state || workspace.startsWith(state + sep) || state.startsWith(workspace + sep))
    throw new Error("Resolved agent state and workspace locations must be isolated.");
  const baseUrl = env.PI_LLM_BASE_URL;
  const model = env.PI_LLM_MODEL;
  if (!baseUrl && !model) return;
  if (!baseUrl || !model) throw new Error("Configure PI_LLM_BASE_URL and PI_LLM_MODEL together.");
  if (!env.PI_LLM_API_KEY?.trim()) throw new Error("Configure PI_LLM_API_KEY locally, or an explicit dummy key for a keyless local provider.");
  let url: URL;
  try { url = new URL(baseUrl); } catch { throw new Error("Invalid PI_LLM_BASE_URL."); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error("PI_LLM_BASE_URL must be an HTTP(S) address without embedded credentials.");
  if (url.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname))
    throw new Error("Remote model providers require HTTPS.");
  const limit = (key: string, fallback: number) => {
    const value = Number(env[key] || fallback);
    if (!Number.isSafeInteger(value) || value < 1 || value > 10000000) throw new Error(`Invalid ${key}.`);
    return value;
  };
  const contextWindow = limit("PI_LLM_CONTEXT_WINDOW", 128000);
  const maxTokens = limit("PI_LLM_MAX_TOKENS", 8192);
  if (maxTokens > contextWindow) throw new Error("PI_LLM_MAX_TOKENS must not exceed the context window.");
  const models = { providers: { "coat-weave": { baseUrl, api: "openai-completions", apiKey: "PI_LLM_API_KEY",
    models: [{ id: model, name: model, reasoning: false, input: ["text"],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow, maxTokens }] } } };
  writeFileSync(resolve(config.agentDir, "models.json"), JSON.stringify(models, null, 2) + "\n", { mode: 0o600 });
  writeFileSync(resolve(config.agentDir, "settings.json"), JSON.stringify({ defaultProvider: "coat-weave", defaultModel: model,
    defaultThinkingLevel: "off" }, null, 2) + "\n", { mode: 0o600 });
}
