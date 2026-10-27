import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { resolve } from "node:path";

interface FileConfig {
	url?: string;
	timeoutMs?: number;
	tokenEnv?: string;
	markdownRoot?: string;
}

export interface PatentSearchConfig {
	url: URL;
	timeoutMs: number;
	apiToken?: string;
	markdownRoot?: string;
}

export function loadPatentSearchConfig(
	env: NodeJS.ProcessEnv = process.env,
	home: string = homedir(),
): PatentSearchConfig {
	// Load only an explicitly selected file; never discover account configuration.
	const configPath = env.PATENT_SEARCH_CONFIG
		? resolve(env.PATENT_SEARCH_CONFIG.replace(/^~(?=[/\\])/, home))
		: undefined;
	let file: FileConfig = {};
	if (configPath && existsSync(configPath)) {
		let parsed: unknown;
		try {
			parsed = JSON.parse(readFileSync(configPath, "utf8"));
		} catch {
			throw new Error("无法读取 patent-search 配置，请检查 JSON 格式；配置内容不会输出。");
		}
		if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
			throw new Error("patent-search 配置必须是 JSON 对象。");
		}
		const raw = parsed as Record<string, unknown>;
		if (Object.keys(raw).some((key) => !["url", "timeoutMs", "tokenEnv", "markdownRoot"].includes(key))) {
			throw new Error("patent-search 配置仅支持 url、timeoutMs、tokenEnv、markdownRoot；令牌通过环境变量提供。");
		}
		for (const key of ["url", "tokenEnv", "markdownRoot"]) {
			if (key in raw && (typeof raw[key] !== "string" || raw[key] === "")) {
				throw new Error(`patent-search 配置的 ${key} 必须是非空字符串。`);
			}
		}
		if ("timeoutMs" in raw && typeof raw.timeoutMs !== "number") {
			throw new Error("patent-search 配置的 timeoutMs 必须是数字。");
		}
		file = raw as FileConfig;
	} else if (env.PATENT_SEARCH_CONFIG) {
		throw new Error("PATENT_SEARCH_CONFIG 指定的配置文件不存在。");
	}
	let url: URL;
	try {
		url = new URL(env.PATENT_SEARCH_URL ?? env.PATENT_MILVUS_URL ?? file.url ?? "http://127.0.0.1:8002");
	} catch {
		throw new Error("PATENT_SEARCH_URL 必须是有效的查询服务 URL。");
	}
	if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
		throw new Error("PATENT_SEARCH_URL 必须是无凭据、查询参数和片段的 HTTP(S) 服务地址。");
	}
	if (url.protocol === "http:" && !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)) {
		throw new Error("Remote patent query services require HTTPS.");
	}
	const timeoutMs = Number(env.PATENT_SEARCH_TIMEOUT_MS ?? env.PATENT_MILVUS_TIMEOUT_MS ?? file.timeoutMs ?? 120000);
	if (!Number.isSafeInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 3600000) {
		throw new Error("PATENT_SEARCH_TIMEOUT_MS 必须为 1–3600000 毫秒。");
	}
	const tokenEnv = file.tokenEnv ?? "PATENT_SEARCH_API_TOKEN";
	if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(tokenEnv)) {
		throw new Error("tokenEnv 必须是环境变量名称，不是令牌内容。");
	}
	const apiToken = env.PATENT_SEARCH_API_TOKEN ?? env[tokenEnv];
	if (apiToken && !/^[\x21-\x7e]+$/.test(apiToken)) {
		throw new Error("查询服务访问令牌不能含空白或控制字符。");
	}
	return {
		url,
		timeoutMs,
		apiToken: apiToken || undefined,
		markdownRoot: env.PATENT_SEARCH_MD_ROOT ?? env.PATENT_MILVUS_MD_ROOT ?? file.markdownRoot,
	};
}
