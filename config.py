"""Local settings for the coating KG pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
        os.environ.setdefault(key, value)
    return values


PROJECT_ROOT = Path(__file__).resolve().parent
_DOTENV = _load_dotenv(PROJECT_ROOT / ".env")


def _get(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name) or _DOTENV.get(name)
        if value not in (None, ""):
            return str(value)
    return default


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str = _get("OPENAI_API_KEY", "openai.key")
    base_url: str = _get("OPENAI_BASE_URL", "openai.host", default="https://dashscope.aliyuncs.com/compatible-mode/v1")


@dataclass(frozen=True)
class MinerUSettings:
    token: str = _get("MINERU_TOKEN", "mineru.token")
    api_base: str = _get("MINERU_API_BASE", "mineru.api_base", default="https://mineru.net/api/v4")
    access_key_id: str = _get("MINERU_ACCESS_KEY_ID", "mineru.access_key_id")
    secret_access_key: str = _get("MINERU_SECRET_ACCESS_KEY", "mineru.secret_access_key")


@dataclass(frozen=True)
class ModelSettings:
    qwen_text: str = _get("QWEN_TEXT_MODEL", "openai.model", default="qwen-plus")
    qwen_vl: str = _get("QWEN_VL_MODEL", default="qwen-vl-plus")


@dataclass(frozen=True)
class PathSettings:
    project_root: Path = PROJECT_ROOT
    repo_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    mineru_output_dir: Path = Path(_get(
        "MINERU_OUTPUT_DIR",
        "paths.mineru_output_dir",
        default=str(PROJECT_ROOT.parent.parent / "mineru_output"),
    ))


@dataclass(frozen=True)
class DBSettings:
    dsn: str = _get("DATABASE_URL", "db.dsn")


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    ontology_version: str = _get("ONTOLOGY_VERSION", default="local-dev")
    openai: OpenAISettings = OpenAISettings()
    mineru: MinerUSettings = MinerUSettings()
    models: ModelSettings = ModelSettings()
    paths: PathSettings = PathSettings()
    db: DBSettings = DBSettings()


SETTINGS = Settings()
