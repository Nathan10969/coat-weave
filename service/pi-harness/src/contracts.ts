import type { AgentSession, AgentSessionEvent, PromptOptions } from "@earendil-works/pi-coding-agent";

// Types come directly from the pinned official SDK. No copied event/message union.
export type PiEvent = AgentSessionEvent;
export type PiMessages = AgentSession["messages"];
export type PromptRequest = {
  text: string;
  options?: Pick<PromptOptions, "images" | "expandPromptTemplates" | "streamingBehavior">;
};
// These are HTTP transport metadata, not Pi model output.
export interface CreateSessionResponse { sessionId: string }
export interface HttpErrorResponse { error: { code: string; message: string } }

export interface SessionPort {
  readonly sessionId: string;
  readonly messages: PiMessages;
  readonly isStreaming: boolean;
  subscribe(listener: (event: PiEvent) => void): () => void;
  prompt(text: string, options?: PromptOptions): Promise<void>;
  abort(): Promise<void>;
  waitForIdle?(): Promise<void>;
  dispose(): void;
}
export interface SessionHandle {
  session: SessionPort;
  start(): Promise<void>;
  close?(): Promise<void>;
}
// A completion owns an isolated context; the caller supplies its prior history.
// Native /sessions calls do not pass a seed and retain SDK persistence semantics.
export interface CompletionSeed {
  systemPrompt: string;
  history: { role: "user" | "assistant"; text: string }[];
  temperature?: number;
}
export type SessionFactory = (seed?: CompletionSeed) => Promise<SessionHandle>;
