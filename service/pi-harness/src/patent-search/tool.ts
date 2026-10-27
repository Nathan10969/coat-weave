import { readFileSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { defineTool } from "@earendil-works/pi-coding-agent";
import { type TSchema, Type } from "typebox";
import { loadPatentSearchConfig } from "./config.js";

// The release carries its own schema; no development workspace is needed at runtime.
export const patentMilvusSchemaPath =
	process.env.PATENT_SEARCH_SCHEMA || process.env.PATENT_MILVUS_SCHEMA || resolve(import.meta.dirname, "tool_schema.json");
const definition = JSON.parse(readFileSync(patentMilvusSchemaPath, "utf8")) as {
	name: string;
	description: string;
	inputSchema: TSchema;
};

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

export async function queryPatentMilvus(
	statement: Record<string, unknown>,
	signal?: AbortSignal,
): Promise<Record<string, unknown>> {
	const config = loadPatentSearchConfig();
	const { url, timeoutMs } = config;
	url.pathname = `${url.pathname.replace(/\/$/, "")}/tools/patent_search`;
	const timeout = AbortSignal.timeout(timeoutMs);
	const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
	try {
		requestSignal.throwIfAborted();
		const response = await fetch(url, {
			method: "POST",
			headers: {
				"Content-Type": "application/json",
				...(config.apiToken ? { Authorization: `Bearer ${config.apiToken}` } : {}),
			},
			body: JSON.stringify({ statement }),
			signal: requestSignal,
			redirect: "error",
		});
		if (!response.ok) {
			await response.body?.cancel();
			if (response.status === 401 || response.status === 403) {
				throw new Error("专利查询服务拒绝访问，请检查 PATENT_SEARCH_API_TOKEN 或配置的 tokenEnv。");
			}
			throw new Error(`Milvus 查询服务返回 HTTP ${response.status}，请检查服务状态。`);
		}
		if (!response.body) throw new Error("Milvus 查询服务返回空响应。");
		const reader = response.body.getReader();
		const chunks: Uint8Array[] = [];
		let size = 0;
		while (true) {
			const { done, value } = await reader.read();
			if (done) break;
			size += value.byteLength;
			if (size > 2_000_000) {
				await reader.cancel();
				throw new Error("Milvus 查询响应超过 2 MB，请减少 limit 或 output_fields。");
			}
			chunks.push(value);
		}
		const result: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
		if (
			!isRecord(result) ||
			typeof result.success !== "boolean" ||
			typeof result.result_id !== "string" ||
			typeof result.collection !== "string"
		) {
			throw new Error("Milvus 查询响应格式不匹配，需要 success、result_id 和 collection。");
		}
		// Throwing lets pi mark a failed query as a tool error instead of an empty result.
        if (!result.success) throw new Error("Patent query failed; inspect the statement and service configuration. Raw backend errors are not forwarded.");
		const markdownRoot = config.markdownRoot;
		if (markdownRoot && Array.isArray(result.results)) {
			for (const row of result.results) {
				if (!isRecord(row) || !isRecord(row.full_text) || typeof row.full_text.path !== "string") continue;
				if (row.full_text.source === "tos") continue;
				const root = resolve(markdownRoot);
				const path = resolve(root, row.full_text.path.replace(/\\/g, "/"));
				const withinRoot = relative(root, path);
				if (isAbsolute(withinRoot) || withinRoot === ".." || withinRoot.startsWith(`..${sep}`)) {
					throw new Error("Milvus 返回的全文相对路径超出 PATENT_SEARCH_MD_ROOT。");
				}
				row.full_text.read_path = path;
			}
		}
		return result;
	} catch (error) {
		if (signal?.aborted) throw new Error("Milvus 查询已取消。");
		if (timeout.aborted) throw new Error("Milvus 查询超时，请检查服务状态或缩小查询范围。");
		if (error instanceof TypeError) throw new Error("无法连接 Milvus 查询服务，请检查 PATENT_SEARCH_URL。");
		if (error instanceof SyntaxError) throw new Error("Patent query service returned invalid JSON; response content is not exposed.");
		throw error;
	}
}

export const patentMilvusTool = defineTool({
	name: definition.name,
	label: "Coating patent search",
	description: definition.description,
	promptSnippet: "Search the configured coating patent collection; inspect original evidence and use describe for current field semantics.",
	parameters: Type.Unsafe<{ statement: Record<string, unknown> }>(definition.inputSchema),
	async execute(_toolCallId, { statement }, signal) {
		const result = await queryPatentMilvus(statement, signal);
		return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
	},
});
