import { existsSync, realpathSync } from "node:fs";
import { basename, dirname, isAbsolute, relative, resolve, sep } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export function toolNamesForMode(mode = "read-only"): string[] {
  if (!["read-only", "workspace-write", "shell"].includes(mode)) throw new Error("Invalid PI_TOOL_MODE.");
  return ["read", "patent_search", ...(mode !== "read-only" ? ["edit", "write"] : []), ...(mode === "shell" ? ["bash"] : [])];
}
function inside(root: string, path: string): boolean {
  const value = relative(root, path);
  return !isAbsolute(value) && value !== ".." && !value.startsWith(`..${sep}`);
}
export function assertToolPath(workspace: string, tool: string, path: unknown): void {
  if (typeof path !== "string" || !path || path.includes("\0")) throw new Error("File access denied.");
  const root = realpathSync(workspace);
  const target = resolve(root, path);
  const parts = relative(root, target).split(sep);
  if (!inside(root, target) || parts.some(part => part.startsWith(".")) ||
      /^(?:auth|models|settings|credentials)\.json$/i.test(basename(target)) ||
      /\.(?:pem|key|p12|pfx)$/i.test(target)) throw new Error("File access denied.");
  if (tool === "write" || tool === "edit") {
    const outputs = resolve(root, "outputs");
    if (target === outputs || !inside(outputs, target)) throw new Error("Writes are restricted to workspace outputs.");
  }
  let ancestor = target;
  while (!existsSync(ancestor) && ancestor !== dirname(ancestor)) ancestor = dirname(ancestor);
  if (!inside(root, realpathSync(ancestor))) throw new Error("File access denied.");
  if ((tool === "write" || tool === "edit") && !inside(resolve(root, "outputs"), realpathSync(ancestor)))
    throw new Error("File access denied outside outputs.");
}
export function workspaceGuard(workspace: string) {
  return (pi: ExtensionAPI) => {
    pi.on("tool_call", event => {
      if (!["read", "write", "edit"].includes(event.toolName)) return;
      try { assertToolPath(workspace, event.toolName, (event.input as { path?: unknown }).path); }
      catch { return { block: true, reason: "File access denied by the workspace policy." }; }
    });
  };
}
