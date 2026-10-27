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
