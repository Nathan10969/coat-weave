from __future__ import annotations

import re
from pathlib import Path

from .config import CoatingApiSettings


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def sanitize_workspace_name(value: str) -> str:
    cleaned = SAFE_NAME_RE.sub("_", value.strip()).strip("._")
    return cleaned[:120] or "default"


def conversation_workspace(settings: CoatingApiSettings, conversation_id: str) -> Path:
    name = sanitize_workspace_name(conversation_id)
    root = settings.data_root / "conversations"
    path = (root / name).resolve()
    root_resolved = root.resolve()
    if root_resolved not in path.parents and path != root_resolved:
        raise ValueError("conversation workspace escaped data root")
    return path
