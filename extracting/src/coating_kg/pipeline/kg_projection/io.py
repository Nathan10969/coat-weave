"""Filesystem IO helpers for KG projection records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

JSON = dict[str, Any]


def _write_jsonl(path: Path, rows: Iterable[JSON]) -> int:
    rows_list = list(rows)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows_list:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows_list)

def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def _read_jsonl(path: Path) -> list[Any]:
    rows: list[Any] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows

def _read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None

def _rel(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)
