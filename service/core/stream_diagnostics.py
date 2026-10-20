"""Observation-only live-stream diagnostics. BEST-EFFORT — never affects the stream.

Purpose: localize the live-stream hang without guessing. Each instrumentation
point appends one small JSONL line to runtime_data/stream_diagnostics.jsonl.

It answers two questions the current code cannot distinguish:
  1. WHERE a stream stalls — upstream LLM read / SSE yield / assistant save / lock.
  2. WHY one stuck stream can take down the whole platform — sync `def` endpoints +
     sync generators run in FastAPI's anyio threadpool (default ~40 threads); a
     blocked stream holds a worker, and enough of them exhaust the pool so even
     /health (if sync) cannot get scheduled. `active_stream_count` + thread info
     make that visible.

HARD RULE: every write is wrapped in try/except and swallows its own failure.
A diagnostics bug must NEVER propagate into the SSE stream — that is exactly the
api_traces.metadata mistake we are not repeating. This module also NEVER touches
Postgres or any shared schema; it only appends to a local JSONL file.

LOCATION: this lives in `core/` (not `api/`) so it can be imported with the same
bare-import convention the other core modules use (e.g. `from demo_config import`).
`api/` code reaches it the same way it reaches the other core modules.

KILL SWITCH: set COATING_STREAM_DIAG_DISABLE=1 to make every file write a no-op
(the in-memory active-stream counter keeps working). For turning the patch off in
production without a redeploy.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

# Module-level live-stream counter (thread-safe). Reveals threadpool occupancy:
# if this climbs and stays high while requests hang, the pool is exhausted.
_active_streams = 0
_active_lock = threading.Lock()

_DIAG_PATH_ENV = "COATING_STREAM_DIAG_PATH"
_DIAG_DISABLE_ENV = "COATING_STREAM_DIAG_DISABLE"


def _diag_disabled() -> bool:
    # Evaluated per-call (cheap dict lookup) so the switch can be flipped without
    # reimporting. Returns True for any truthy-ish value except 0/false/no.
    val = os.environ.get(_DIAG_DISABLE_ENV)
    return val not in (None, "", "0", "false", "False", "no", "NO")


def _runtime_context() -> dict[str, Any]:
    """Capture where this code is actually running — the key signal for the
    threadpool-vs-event-loop question. For a sync `def` StreamingResponse this
    runs in an anyio worker thread (is_main_thread=False, no running loop)."""
    thread = threading.current_thread()
    try:
        asyncio.get_running_loop()
        has_loop = True
    except RuntimeError:
        has_loop = False
    return {
        "thread_id": thread.ident,
        "thread_name": thread.name,
        "is_main_thread": thread is threading.main_thread(),
        "has_running_asyncio_loop": has_loop,
    }


def _diag_path() -> Path:
    override = os.environ.get(_DIAG_PATH_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "runtime_data" / "stream_diagnostics.jsonl"


def active_stream_count() -> int:
    with _active_lock:
        return _active_streams


class StreamDiagnostics:
    """Per-stream recorder. Instantiate at stream start, call .event(...) at each
    instrumentation point, and .incr_active()/.decr_active() around the stream body.
    All methods are best-effort and never raise.

    Each layer (api SSE handler / orchestrator / core read loop) constructs its own
    recorder. When A/B pass a shared diag_request_id, all layers share that id.
    Without that handoff, fall back to `thread_id` + absolute `ts`; a sync generator
    can hop anyio worker threads between __next__ calls, so a thread-local "current
    recorder" is unsafe.
    """

    def __init__(
        self,
        request_id: str | None = None,
        conversation_id: str | None = None,
        layer: str | None = None,
    ) -> None:
        self.request_id = request_id or uuid.uuid4().hex
        self.conversation_id = conversation_id or ""
        self._t0 = time.monotonic()
        self._seq = 0
        # Throttle state for the per-line upstream logger (see line_event).
        self._line_count = 0
        self._last_line_kind: str | None = None
        self._last_line_log_t = self._t0
        try:
            self._ctx = _runtime_context()
        except Exception:
            self._ctx = {}
        # `layer` identifies which code layer emitted the event (api SSE handler /
        # orchestrator / core read loop). Set per call-site — more honest than a
        # hardcoded role constant now that this module is shared across layers.
        self._ctx["layer"] = layer or "unknown"
        self._path = _diag_path()

    def incr_active(self) -> int:
        global _active_streams
        try:
            with _active_lock:
                _active_streams += 1
                return _active_streams
        except Exception:
            return -1

    def decr_active(self) -> int:
        global _active_streams
        try:
            with _active_lock:
                _active_streams = max(0, _active_streams - 1)
                return _active_streams
        except Exception:
            return -1

    def line_event(self, kind: str) -> None:
        """Throttled per-line logger for the upstream read loop. Never logs
        once-per-line: an SSE stream alternates `data:` lines with blank separator
        lines, so a naive 'log on kind change' fires on EVERY line
        (data_content <-> empty_line) and the diagnostic itself becomes the I/O
        bottleneck it is meant to observe (observed: ~977 writes for one answer).

        So the high-frequency streaming kinds (data_content / empty_line /
        data_no_content) only emit on the first line and a ~1.5s heartbeat; the rare
        abnormal kinds (non_data / data_unparseable) and the terminator (data_done)
        always emit. The heartbeat still distinguishes an 'alive-but-never-terminates'
        upstream (heartbeats keep coming, no data_done) from a true stall (no lines)."""
        try:
            self._line_count += 1
            now = time.monotonic()
            high_freq = kind in ("data_content", "empty_line", "data_no_content")
            elapsed = now - self._last_line_log_t
            if self._line_count == 1 or kind == "data_done" or not high_freq or elapsed >= 1.5:
                self._last_line_kind = kind
                self._last_line_log_t = now
                self.event("upstream_line_received", kind=kind, line_count=self._line_count)
        except Exception:
            pass

    def event(
        self,
        event: str,
        *,
        kind: str | None = None,
        chars: int | None = None,
        bytes_: int | None = None,
        error: str | None = None,
        **extra: Any,
    ) -> None:
        """Append one diagnostic line. `kind` is for upstream_line_received:
        empty_line / data_done / data_content / data_no_content / non_data /
        data_unparseable. `extra` carries lock_name, held_ms, etc. NEVER raises."""
        if _diag_disabled():
            return
        try:
            self._seq += 1
            with _active_lock:
                active = _active_streams
            row: dict[str, Any] = {
                "ts": time.time(),
                "request_id": self.request_id,
                "conversation_id": self.conversation_id,
                "event": event,
                "seq": self._seq,
                "t_ms": int((time.monotonic() - self._t0) * 1000),
                "active_stream_count": active,
            }
            row.update(self._ctx)
            if kind is not None:
                row["kind"] = kind
            if chars is not None:
                row["chars"] = chars
            if bytes_ is not None:
                row["bytes"] = bytes_
            if error is not None:
                row["error"] = str(error)[:300]
            if extra:
                row.update(extra)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            # A diagnostics failure must never break the stream. Swallow.
            pass
