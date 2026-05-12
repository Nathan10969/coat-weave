"""带类型的配置，从环境 / .env 加载。

paths / DB credentials / model 名称的单一来源。所有路径包成
``pathlib.Path``，让调用方在任何 OS 都能用 ``Path.exists()`` /
``Path.iterdir()``。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# import 时加载 .env 一次。调用方想换配置时也可以显式 ``load_dotenv(override=True)``。
load_dotenv()


def _env(key: str, default: str | None = None) -> str:
    val = os.getenv(key, default)
    if val is None:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return val


@dataclass(frozen=True)
class DBConfig:
    host: str
    port: int
    name: str
    user: str
    password: str

    @property
    def dsn(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.name} "
            f"user={self.user} password={self.password}"
        )


@dataclass(frozen=True)
class ModelConfig:
    """OpenAI-compatible endpoint 上 Qwen 模型的名称。

    - ``qwen_text``: 纯文本模型 (如 ``qwen-plus``)，用于 fact 抽取和
      :mod:`vlm_describe` 的纯文本路径 (table HTML)。
    - ``qwen_vl``: 视觉语言模型 (如 ``qwen-vl-plus`` / ``qwen-vl-max``)，
      用于 figure 描述。
    - ``embedding``: embedding 模型 (如 ``text-embedding-v3``)，
      用于 pgvector-based canonical-id 召回。
    """

    qwen_vl: str
    qwen_text: str
    embedding: str


@dataclass(frozen=True)
class OpenAIConfig:
    """OpenAI-compatible endpoint 凭证 (DashScope compatible-mode)。"""

    api_key: str
    base_url: str


@dataclass(frozen=True)
class MinerUConfig:
    """MinerU 在线 REST API 凭证。"""

    api_base: str
    token: str


@dataclass(frozen=True)
class Settings:
    project_root: Path
    pdf_input_dir: Path
    mineru_output_dir: Path
    db: DBConfig
    models: ModelConfig
    openai: OpenAIConfig
    mineru: MinerUConfig
    ontology_version: str
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[2]
        return cls(
            project_root=project_root,
            pdf_input_dir=Path(_env("PDF_INPUT_DIR", str(project_root / "data" / "pdf"))),
            mineru_output_dir=Path(_env("MINERU_OUTPUT_DIR", str(project_root / "data" / "mineru_output"))),
            db=DBConfig(
                host=_env("DB_HOST", "localhost"),
                port=int(_env("DB_PORT", "5432")),
                name=_env("DB_NAME", "coating_kg"),
                user=_env("DB_USER", "postgres"),
                password=_env("DB_PASSWORD", "postgres"),
            ),
            models=ModelConfig(
                qwen_vl=_env("QWEN_VL_MODEL", "qwen-vl-plus"),
                qwen_text=_env("QWEN_TEXT_MODEL", "qwen-plus"),
                embedding=_env("EMBEDDING_MODEL", "text-embedding-v3"),
            ),
            openai=OpenAIConfig(
                api_key=_env("OPENAI_API_KEY", ""),
                base_url=_env(
                    "OPENAI_BASE_URL",
                    "https://dashscope.aliyuncs.com/compatible-mode/v1",
                ),
            ),
            mineru=MinerUConfig(
                api_base=_env("MINERU_API_BASE", "https://mineru.net/api/v4"),
                token=_env("MINERU_TOKEN", ""),
            ),
            ontology_version=_env("ONTOLOGY_VERSION", "v0.1"),
        )


# 方便单例 — 多数调用点只要一个 Settings
SETTINGS = Settings.load()
