import type { AssistantMessage, UserMessage } from "@earendil-works/pi-ai";
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager, type AgentSession } from "@earendil-works/pi-coding-agent";
import type { CompletionSeed, SessionFactory } from "./contracts.js";
import { appendCompletionSystemPrompt, COATING_SYSTEM_PROMPT } from "./assistant-profile.js";
import { join } from "node:path";
import { patentMilvusTool } from "./patent-search/tool.js";
import { toolNamesForMode, workspaceGuard } from "./tool-policy.js";

export function prepareCompletionSession(session: AgentSession, seed: CompletionSeed): void {
  const model = session.model;
  if (!model) throw new Error("Pi has no configured model.");
  const messages: (UserMessage | AssistantMessage)[] = seed.history.map(message => message.role === "user"
    ? { role: "user", content: message.text, timestamp: Date.now() }
    : { role: "assistant", content: [{ type: "text", text: message.text }], api: model.api,
        provider: model.provider, model: model.id, timestamp: Date.now(), stopReason: "stop",
        usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
  for (const message of messages) session.sessionManager.appendMessage(message);
  session.agent.state.messages = messages;
  if (seed.temperature !== undefined) {
    const stream = session.agent.streamFunction;
    session.agent.streamFunction = (model, context, options) => stream(model, context, { ...options, temperature: seed.temperature });
  }
}

export function officialPiFactory(cwd: string, agentDir: string, toolMode = "read-only"): SessionFactory {
  return async seed => {
    const resolvedAgentDir = agentDir;
    const settingsManager = SettingsManager.create(cwd, resolvedAgentDir);
    const resourceLoader = new DefaultResourceLoader({
      cwd, agentDir: resolvedAgentDir, settingsManager,
      noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
      systemPromptOverride: () => COATING_SYSTEM_PROMPT,
      appendSystemPromptOverride: base => appendCompletionSystemPrompt(base, seed?.systemPrompt ?? ""),
      extensionFactories: [workspaceGuard(cwd)],
      // Use literal text, not the SDK's string-or-file-path prompt input.
    });
    if (resourceLoader) await resourceLoader.reload();
    const modelRuntime = await ModelRuntime.create({ authPath: join(agentDir, "auth.json"),
      modelsPath: join(agentDir, "models.json"), modelsStorePath: join(agentDir, "model-cache.json"),
      allowModelNetwork: false });
    const provider = settingsManager.getDefaultProvider();
    const modelId = settingsManager.getDefaultModel();
    const model = provider && modelId ? modelRuntime.getModel(provider, modelId) : undefined;
    if (!model) throw new Error("Configure an explicit server model; automatic account/provider fallback is disabled.");
    const result = await createAgentSession({
      cwd, model, modelRuntime,
      agentDir: resolvedAgentDir, settingsManager, resourceLoader,
      tools: toolNamesForMode(toolMode), customTools: [patentMilvusTool],
      sessionManager: seed ? SessionManager.inMemory(cwd) : SessionManager.create(cwd, join(resolvedAgentDir, "sessions")),
      thinkingLevel: "off",
      // Native /sessions keeps the SDK default persisted SessionManager.
    });
    if (result.extensionsResult.errors.length) {
      result.session.dispose();
      throw new Error("Pi extension loading failed; inspect the configured packages before starting the API.");
    }
    if (seed) {
      try { prepareCompletionSession(result.session, seed); }
      catch (error) { result.session.dispose(); throw error; }
    }
    return {
      session: result.session,
      // Subscribe before binding so startup events also enter the event journal.
      start: () => result.session.bindExtensions({ mode: "json" }),
      close: async () => {
        try {
          await result.session.abort();
          await result.session.extensionRunner.emit({ type: "session_shutdown", reason: "quit" });
        } finally { result.session.dispose(); }
      },
    };
  };
}
