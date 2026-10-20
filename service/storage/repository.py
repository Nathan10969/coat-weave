from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
import redis

from api.config import CoatingApiSettings


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT,
    active_doc_scope JSONB,
    scope_history JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS turns (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id TEXT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    provider TEXT,
    model TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tool_observations (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS memory_items (
    memory_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS scope_events (
    id BIGSERIAL PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS api_traces (
    request_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    route JSONB NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class ServiceRepository:
    def __init__(self, settings: CoatingApiSettings) -> None:
        self.settings = settings
        self._redis = None
        if settings.redis_url:
            try:
                self._redis = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=3)
            except Exception:
                self._redis = None

    def _connect(self):
        return psycopg.connect(
            self.settings.postgres_dsn,
            connect_timeout=2,
            options="-c statement_timeout=5000",
        )

    def ready(self) -> dict[str, Any]:
        postgres_ok = False
        redis_ok = False
        postgres_error = None
        redis_error = None
        if self.settings.postgres_dsn:
            try:
                with self._connect() as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT 1")
                        postgres_ok = cur.fetchone()[0] == 1
            except Exception as exc:  # noqa: BLE001
                postgres_error = str(exc)
        if self._redis is not None:
            try:
                redis_ok = bool(self._redis.ping())
            except Exception as exc:  # noqa: BLE001
                redis_error = str(exc)
        return {
            "postgres": {"ok": postgres_ok, "required": self.settings.require_postgres, "error": postgres_error},
            "redis": {"ok": redis_ok, "required": self.settings.require_redis, "error": redis_error},
        }

    def init_schema(self) -> None:
        if not self.settings.postgres_dsn:
            if self.settings.require_postgres:
                raise RuntimeError("COATING_POSTGRES_DSN is required")
            return
        with self._connect() as conn:
            conn.execute(SCHEMA_SQL)
            conn.commit()

    @contextmanager
    def conversation_lock(self, conversation_id: str) -> Iterator[None]:
        key = f"coating:conversation-lock:{conversation_id}"
        token = f"{time.time_ns()}"
        locked = False
        if self._redis is not None:
            try:
                locked = bool(self._redis.set(key, token, nx=True, ex=180))
                if not locked:
                    deadline = time.time() + 10
                    while time.time() < deadline and not locked:
                        time.sleep(0.1)
                        locked = bool(self._redis.set(key, token, nx=True, ex=180))
                    if not locked:
                        raise RuntimeError("conversation is busy")
            except Exception:
                locked = False
        try:
            yield
        finally:
            if locked and self._redis is not None:
                try:
                    if self._redis.get(key) == token.encode("utf-8"):
                        self._redis.delete(key)
                except Exception:
                    pass

    def record_turn(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        role: str,
        content: str,
        provider: str | None = None,
        model: str | None = None,
        ) -> None:
        if not self.settings.postgres_dsn:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO turns (conversation_id, user_id, role, content, provider, model)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (conversation_id, user_id, role, content, provider, model),
            )
            conn.commit()

    def sync_conversation_snapshot(
        self,
        *,
        conversation_id: str,
        user_id: str | None,
        workspace: Path,
        packet: dict[str, Any],
    ) -> None:
        if not self.settings.postgres_dsn:
            return
        scope = packet.get("active_scope")
        scope_history = packet.get("scope_history") or []
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (conversation_id, user_id, active_doc_scope, scope_history)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (conversation_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    active_doc_scope = EXCLUDED.active_doc_scope,
                    scope_history = EXCLUDED.scope_history,
                    updated_at = now()
                """,
                (conversation_id, user_id, json.dumps(scope), json.dumps(scope_history)),
            )
            for row in _read_jsonl(workspace / "tool_observations.jsonl")[-10:]:
                conn.execute(
                    """
                    INSERT INTO tool_observations (conversation_id, tool, status, payload)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        conversation_id,
                        str(row.get("tool") or "unknown"),
                        str(row.get("status") or "unknown"),
                        json.dumps(row),
                    ),
                )
            resolution = packet.get("last_scope_resolution")
            if resolution:
                conn.execute(
                    "INSERT INTO scope_events (conversation_id, payload) VALUES (%s, %s)",
                    (conversation_id, json.dumps(resolution)),
                )
            conn.commit()

    def record_trace(
        self,
        *,
        request_id: str,
        conversation_id: str,
        route: list[dict[str, Any]],
        elapsed_ms: int,
        error: str | None = None,
        ) -> None:
        if not self.settings.postgres_dsn:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_traces (request_id, conversation_id, route, elapsed_ms, error)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (request_id) DO NOTHING
                """,
                (request_id, conversation_id, json.dumps(route), elapsed_ms, error),
            )
            conn.commit()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
