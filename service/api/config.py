from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_A100_ROOT = SERVICE_ROOT


def _load_env_file(path: Path, target: dict[str, str]) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in target:
            continue
        target[key] = value.strip().strip('"').strip("'")


def _bool_env(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(value: str | None, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _list_env(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class CoatingApiSettings:
    host: str = "0.0.0.0"
    port: int = 8031
    service_root: Path = DEFAULT_A100_ROOT
    data_root: Path = DEFAULT_A100_ROOT / "runtime_data"
    api_token: str = ""
    disable_auth: bool = False
    allowed_ips: list[str] = field(default_factory=list)
    postgres_dsn: str = ""
    redis_url: str = "redis://127.0.0.1:6379/2"
    require_postgres: bool = False
    require_redis: bool = False
    kg_expand_url: str = "http://127.0.0.1:8021/tools/kg.expand_hyperedge_multihop"
    kg_hybrid_search_url: str = "http://127.0.0.1:8021/tools/kg.hybrid_search"
    kg_sql_aggregate_url: str = "http://127.0.0.1:8021/tools/kg.sql_aggregate"
    kg_doc_field_scan_url: str = "http://127.0.0.1:8021/tools/kg.doc_field_scan"
    kg_expand_token: str = ""
    kg_timeout_seconds: int = 60
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-pro"
    llm_protocol: str = "openai"
    context_window_tokens: int = 1_000_000
    max_output_tokens: int = 65_536
    stream_chunk_chars: int = 4
    enable_thinking: bool = False

    @property
    def api_token_set(self) -> bool:
        return bool(self.api_token)

    @property
    def auth_required(self) -> bool:
        return not self.disable_auth

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "CoatingApiSettings":
        values = dict(os.environ)
        _load_env_file(env_file or (SERVICE_ROOT / ".env"), values)
        return cls.from_mapping(values)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "CoatingApiSettings":
        service_root = Path(values.get("COATING_SERVICE_ROOT", str(DEFAULT_A100_ROOT)))
        data_root = Path(values.get("COATING_DATA_ROOT", str(service_root / "runtime_data")))
        api_key = (
            values.get("ZENMUX_API_KEY")
            or values.get("ANTHROPIC_API_KEY")
            or values.get("LLM_API_KEY")
            or values.get("DEEPSEEK_API_KEY")
            or values.get("QWEN_API_KEY")
            or values.get("DASHSCOPE_API_KEY")
            or values.get("OPENAI_API_KEY")
            or ""
        )
        base_url = (
            values.get("ZENMUX_BASE_URL")
            or values.get("ANTHROPIC_BASE_URL")
            or values.get("LLM_BASE_URL")
            or values.get("DEEPSEEK_BASE_URL")
            or values.get("QWEN_BASE_URL")
            or values.get("DASHSCOPE_BASE_URL")
            or values.get("OPENAI_BASE_URL")
            or "https://api.deepseek.com"
        ).rstrip("/")
        model = (
            values.get("ZENMUX_MODEL")
            or values.get("ANTHROPIC_MODEL")
            or values.get("LLM_MODEL")
            or values.get("DEEPSEEK_MODEL")
            or values.get("QWEN_MODEL")
            or values.get("QWEN_TEXT_MODEL")
            or "deepseek-v4-pro"
        )
        protocol = (
            values.get("LLM_API_PROTOCOL")
            or values.get("MODEL_API_PROTOCOL")
            or values.get("ZENMUX_API_PROTOCOL")
            or ("anthropic" if "/anthropic" in base_url.lower() else "openai")
        ).strip().lower()
        return cls(
            host=values.get("COATING_API_HOST", "0.0.0.0"),
            port=_int_env(values.get("COATING_API_PORT"), 8031),
            service_root=service_root,
            data_root=data_root,
            api_token=values.get("COATING_API_TOKEN", ""),
            disable_auth=_bool_env(values.get("COATING_DISABLE_AUTH"), False),
            allowed_ips=_list_env(values.get("COATING_ALLOWED_IPS")),
            postgres_dsn=values.get("COATING_POSTGRES_DSN", ""),
            redis_url=values.get("COATING_REDIS_URL", "redis://127.0.0.1:6379/2"),
            require_postgres=_bool_env(values.get("COATING_REQUIRE_POSTGRES"), False),
            require_redis=_bool_env(values.get("COATING_REQUIRE_REDIS"), False),
            kg_expand_url=values.get(
                "KG_EXPAND_URL",
                "http://127.0.0.1:8021/tools/kg.expand_hyperedge_multihop",
            ),
            kg_hybrid_search_url=values.get(
                "KG_HYBRID_SEARCH_URL",
                "http://127.0.0.1:8021/tools/kg.hybrid_search",
            ),
            kg_sql_aggregate_url=values.get(
                "KG_SQL_AGGREGATE_URL",
                "http://127.0.0.1:8021/tools/kg.sql_aggregate",
            ),
            kg_doc_field_scan_url=values.get(
                "KG_DOC_FIELD_SCAN_URL",
                "http://127.0.0.1:8021/tools/kg.doc_field_scan",
            ),
            kg_expand_token=values.get("KG_EXPAND_TOKEN", ""),
            kg_timeout_seconds=_int_env(values.get("KG_EXPAND_TIMEOUT_SECONDS"), 60),
            llm_api_key=api_key,
            llm_base_url=base_url,
            llm_model=model,
            llm_protocol=protocol,
            context_window_tokens=_int_env(
                values.get("LLM_CONTEXT_WINDOW_TOKENS") or values.get("QWEN_CONTEXT_WINDOW_TOKENS"),
                1_000_000,
            ),
            max_output_tokens=_int_env(
                values.get("LLM_MAX_OUTPUT_TOKENS") or values.get("QWEN_MAX_OUTPUT_TOKENS"),
                65_536,
            ),
            stream_chunk_chars=_int_env(values.get("LLM_STREAM_CHUNK_CHARS"), 4),
            enable_thinking=_bool_env(
                values.get("DEEPSEEK_ENABLE_THINKING") or values.get("QWEN_ENABLE_THINKING"),
                False,
            ),
        )

    def apply_to_environment(self) -> None:
        env = {
            "KG_EXPAND_URL": self.kg_expand_url,
            "KG_HYBRID_SEARCH_URL": self.kg_hybrid_search_url,
            "KG_SQL_AGGREGATE_URL": self.kg_sql_aggregate_url,
            "KG_DOC_FIELD_SCAN_URL": self.kg_doc_field_scan_url,
            "KG_EXPAND_TOKEN": self.kg_expand_token,
            "KG_EXPAND_TIMEOUT_SECONDS": str(self.kg_timeout_seconds),
            "KG_HYBRID_SEARCH_TIMEOUT_SECONDS": str(self.kg_timeout_seconds),
            "KG_SQL_AGGREGATE_TIMEOUT_SECONDS": str(self.kg_timeout_seconds),
            "KG_DOC_FIELD_SCAN_TIMEOUT_SECONDS": str(self.kg_timeout_seconds),
            "LLM_API_PROTOCOL": self.llm_protocol,
            "LLM_BASE_URL": self.llm_base_url,
            "LLM_MODEL": self.llm_model,
            "DEEPSEEK_BASE_URL": self.llm_base_url,
            "DEEPSEEK_MODEL": self.llm_model,
            "DEEPSEEK_ENABLE_THINKING": "true" if self.enable_thinking else "false",
            "LLM_CONTEXT_WINDOW_TOKENS": str(self.context_window_tokens),
            "LLM_MAX_OUTPUT_TOKENS": str(self.max_output_tokens),
            "LLM_STREAM_CHUNK_CHARS": str(self.stream_chunk_chars),
            "QWEN_CONTEXT_WINDOW_TOKENS": str(self.context_window_tokens),
            "QWEN_MAX_OUTPUT_TOKENS": str(self.max_output_tokens),
            "QWEN_ENABLE_THINKING": "true" if self.enable_thinking else "false",
        }
        if self.llm_api_key:
            env["LLM_API_KEY"] = self.llm_api_key
            env["DEEPSEEK_API_KEY"] = self.llm_api_key
            if self.llm_protocol == "anthropic":
                env["ANTHROPIC_API_KEY"] = self.llm_api_key
                env["ZENMUX_API_KEY"] = self.llm_api_key
        for key, value in env.items():
            os.environ[key] = value
