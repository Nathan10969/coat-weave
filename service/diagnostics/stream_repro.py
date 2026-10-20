"""Local repro + analyzer for the live-stream hang. Observation-only companion to
core/stream_diagnostics.py. No external deps (urllib + threading + argparse).

WHAT IT NEEDS TO BE USEFUL
--------------------------
The instrumented `coating_api_service` must run in LIVE stream mode:

    COATING_PLATFORM_STREAM_MODE=live

Buffered mode calls engine.chat() (one-shot, non-streaming) and never reaches
stream_qwen, so NOTHING is logged. The morning hang was the live path — repro it
in live mode only.

Processing C (core/answering.py) is wired now → you already get the upstream
read-loop signal (true-stall vs alive-but-no-terminator). Processing A/B
(api/app.py, api/orchestrator.py — Codex's files) add the downstream-yield and
lock signals; until they are wired the analyzer simply reports them as absent.

SCENARIOS (client side)
-----------------------
  normal      read the SSE to completion ([DONE]); baseline, confirms the chain.
  disconnect  read a few chunks then drop the socket; does the worker get freed?
  slow        read with a delay between reads; observe backpressure.
  concurrent  N streams (one that opens but never reads) + parallel /health pings;
              measures whether ONE stuck stream starves the others — the
              threadpool-exhaustion question. Uses distinct x-session-id per
              stream so they do NOT serialize on the per-conversation lock.
              BACKPRESSURE CAVEAT: "never read" only blocks the server's yield once
              the kernel TCP buffers fill — i.e. only if the response is larger than
              ~socket-buffer size. A small answer fits entirely in buffers and the
              server finishes without ever blocking. For a DECISIVE backpressure
              repro, pass a large-output --question (or use a raw socket / slow
              proxy). This scenario is a smoke, NOT proof of the hang.

ANALYZE (reads the JSONL the server wrote)
------------------------------------------
  analyze     classify each stream's terminal state + the global threadpool
              signal, and map to the discrimination matrix.

Examples
--------
  python stream_repro.py normal      --base http://127.0.0.1:8031 --token "$TOK"
  python stream_repro.py concurrent  --base http://127.0.0.1:8031 --token "$TOK" --streams 3
  python stream_repro.py analyze     --path ../runtime_data/stream_diagnostics.jsonl
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "runtime_data" / "stream_diagnostics.jsonl"
STREAM_ENDPOINT = "/api/ask/stream/chat/completions"
HEALTH_ENDPOINT = "/health"
HEARTBEAT_KINDS = {"empty_line", "data_no_content"}


# --------------------------------------------------------------------------- #
# Client helpers
# --------------------------------------------------------------------------- #
def _open_stream(base: str, token: str, session: str, question: str, model: str):
    """POST a streaming chat request, return the open response object (headers
    received, body not yet read). Caller drives the read so we can simulate
    slow/partial/no consumption."""
    body = json.dumps(
        {"model": model, "messages": [{"role": "user", "content": question}], "stream": True}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-session-id": session}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base.rstrip("/") + STREAM_ENDPOINT, data=body, headers=headers, method="POST")
    return urllib.request.urlopen(req, timeout=300)


def _read_stream(resp, *, max_chunks: int | None, read_delay: float) -> dict[str, Any]:
    """Read SSE lines from an open response. Stops after max_chunks data lines
    (None = until [DONE]/EOF). Returns simple stats."""
    t0 = time.monotonic()
    data_lines = 0
    got_done = False
    total_bytes = 0
    for raw in resp:
        total_bytes += len(raw)
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("data:"):
            data_lines += 1
            if line[len("data:"):].strip() == "[DONE]":
                got_done = True
                break
        if read_delay:
            time.sleep(read_delay)
        if max_chunks is not None and data_lines >= max_chunks:
            break
    return {
        "data_lines": data_lines,
        "got_done": got_done,
        "total_bytes": total_bytes,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }


def _ping_health(base: str, token: str) -> float:
    """Return /health latency in ms (-1 on failure). Reveals whether a stuck
    stream is starving unrelated requests (threadpool exhaustion)."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base.rstrip("/") + HEALTH_ENDPOINT, headers=headers, method="GET")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        return round((time.monotonic() - t0) * 1000, 1)
    except Exception as exc:  # noqa: BLE001
        print(f"  /health FAILED after {round((time.monotonic()-t0)*1000,1)}ms: {exc}")
        return -1.0


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #
def scenario_normal(args) -> None:
    print("[normal] read to completion")
    resp = _open_stream(args.base, args.token, args.session, args.question, args.model)
    stats = _read_stream(resp, max_chunks=None, read_delay=0.0)
    resp.close()
    print(f"  {stats}")
    print("  expect: got_done=True, and a clean upstream_loop_done in the JSONL")


def scenario_disconnect(args) -> None:
    print(f"[disconnect] read {args.chunks} chunks then drop the socket")
    resp = _open_stream(args.base, args.token, args.session, args.question, args.model)
    stats = _read_stream(resp, max_chunks=args.chunks, read_delay=0.0)
    resp.close()  # abrupt drop
    print(f"  {stats}; socket closed mid-stream")
    print("  expect: server should NOT keep the worker forever; with A wired, look")
    print("          for before_sse_yield without after_sse_yield (downstream stuck)")


def scenario_slow(args) -> None:
    print(f"[slow] read with {args.delay}s between lines")
    resp = _open_stream(args.base, args.token, args.session, args.question, args.model)
    stats = _read_stream(resp, max_chunks=None, read_delay=args.delay)
    resp.close()
    print(f"  {stats}")
    print("  expect: backpressure — server yields stall while we are not reading")


def scenario_concurrent(args) -> None:
    n = args.streams
    print(f"[concurrent] {n} streams (stream #0 opens but never reads) + /health pings")
    results: dict[int, Any] = {}

    def run_reader(i: int, never_read: bool) -> None:
        sess = f"{args.session}-{i}"
        try:
            resp = _open_stream(args.base, args.token, sess, args.question, args.model)
            if never_read:
                # Hold the connection open WITHOUT reading -> TCP backpressure makes
                # the server block on yield. This is the "stuck consumer" worst case.
                time.sleep(args.hold)
                resp.close()
                results[i] = {"never_read": True, "held_s": args.hold}
            else:
                results[i] = _read_stream(resp, max_chunks=None, read_delay=0.0)
                resp.close()
        except Exception as exc:  # noqa: BLE001
            results[i] = {"error": str(exc)}

    threads = [threading.Thread(target=run_reader, args=(i, i == 0), daemon=True) for i in range(n)]
    for t in threads:
        t.start()

    # While streams are in flight, ping /health repeatedly.
    time.sleep(1.0)
    latencies = []
    for _ in range(args.health_pings):
        latencies.append(_ping_health(args.base, args.token))
        time.sleep(0.5)

    for t in threads:
        t.join(timeout=args.hold + 30)

    healthy = [x for x in latencies if x >= 0]
    print(f"  /health latencies(ms): {latencies}")
    if healthy:
        print(f"  /health max={max(healthy)}ms  (a big jump while #0 is stuck => worker starvation)")
        if max(healthy) < 500:
            print("  NOTE: /health stayed fast. Either there is no starvation, OR stream#0's")
            print("        response was too small to fill TCP buffers (not a decisive test).")
            print("        Retry with a large-output --question before concluding.")
    for i in sorted(results):
        print(f"  stream#{i}: {results[i]}")
    print("  then run: python stream_repro.py analyze   (look at active_stream_count peak)")


# --------------------------------------------------------------------------- #
# Analyzer
# --------------------------------------------------------------------------- #
def _classify_core_stream(events: list[dict[str, Any]]) -> str:
    names = [e.get("event") for e in events]
    if "upstream_loop_done" in names:
        return "COMPLETED (upstream_loop_done seen -> stream finished cleanly)"
    exc = next((e for e in events if e.get("event") == "upstream_exception"), None)
    if exc:
        return f"EXCEPTION (upstream raised: {exc.get('error')!r}) -> e.g. read timeout fired"
    last = events[-1].get("event") if events else None
    if last == "upstream_read_start":
        return "STALL_BEFORE_FIRST_LINE (blocked on connect/first read; upstream sent nothing)"
    line_events = [e for e in events if e.get("event") == "upstream_line_received"]
    heartbeat_tail = [e for e in line_events if e.get("kind") in HEARTBEAT_KINDS]
    if last == "upstream_line_received" and len(heartbeat_tail) >= 2:
        return ("ALIVE_NO_TERMINATOR (continuous empty/keepalive lines, never [DONE]) "
                "-> upstream keeps the socket non-idle; needs a TOTAL-time valve")
    if last == "upstream_line_received":
        return ("STALL_MID_STREAM (lines stopped arriving, no terminator) "
                "-> upstream stalled; needs an IDLE-time valve")
    return f"INCOMPLETE (last event={last!r}; no terminator)"


def _classify_api_stream(events: list[dict[str, Any]]) -> str:
    names = [e.get("event") for e in events]
    befores = names.count("before_sse_yield")
    afters = names.count("after_sse_yield")
    if befores > afters:
        return (f"DOWNSTREAM_YIELD_STUCK (before_sse_yield={befores} > after_sse_yield={afters}) "
                "-> generator handed a chunk to the send layer and never resumed")
    if "after_done_yield" in names:
        return "OK (after_done_yield seen)"
    return f"INCOMPLETE (before={befores} after={afters}, no after_done_yield)"


def _classify_orch_stream(events: list[dict[str, Any]]) -> str:
    names = [e.get("event") for e in events]
    if "lock_wait_start" in names and "lock_acquired" not in names:
        return "LOCK_NOT_ACQUIRED (waiting on conversation_lock, never got it)"
    rel = next((e for e in events if e.get("event") == "lock_released"), None)
    if rel and isinstance(rel.get("held_ms"), (int, float)) and rel["held_ms"] > 30000:
        return f"LOCK_HELD_LONG (held_ms={rel['held_ms']})"
    if "assistant_save_done" in names:
        return "OK (assistant_save_done seen)"
    return "INCOMPLETE (no assistant_save_done)"


def analyze(path: Path) -> int:
    if not path.exists():
        print(f"no diagnostics file at {path}")
        print("(run the server in COATING_PLATFORM_STREAM_MODE=live and send a request first)")
        return 1
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for ln in handle:
            ln = ln.strip()
            if not ln:
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    if not rows:
        print(f"{path} is empty")
        return 1

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[(str(r.get("request_id")), str(r.get("layer") or "unknown"))].append(r)
    for evs in groups.values():
        evs.sort(key=lambda e: e.get("seq", 0))

    max_active = max((r.get("active_stream_count", 0) or 0) for r in rows)
    classifiers = {
        "core.answering.stream_qwen": _classify_core_stream,
        "api.app.platform_stream": _classify_api_stream,
        "api.orchestrator.stream": _classify_orch_stream,
    }

    print(f"=== stream_diagnostics analysis: {len(rows)} events / {len(groups)} streams ===")
    print(f"peak active_stream_count = {max_active}"
          + ("   <-- >1 with stuck streams suggests threadpool pressure" if max_active > 1 else ""))
    layers_seen = {layer for _, layer in groups}
    for needed in ("api.app.platform_stream", "api.orchestrator.stream"):
        if needed not in layers_seen:
            print(f"note: layer {needed!r} absent (processing {'A' if 'app' in needed else 'B'} not wired yet)")

    for (rid, layer), evs in sorted(groups.items(), key=lambda kv: (kv[1][0].get("ts", 0), kv[0])):
        thread = evs[0].get("thread_id")
        fn = classifiers.get(layer)
        verdict = fn(evs) if fn else f"(no classifier for layer {layer!r})"
        line_n = next((e.get("line_count") for e in reversed(evs) if e.get("line_count") is not None), None)
        extra = f", last_line_count={line_n}" if line_n is not None else ""
        print(f"\n[{layer}] request_id={rid[:12]} thread={thread} events={len(evs)}{extra}")
        print(f"   -> {verdict}")
    return 0


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["normal", "disconnect", "slow", "concurrent", "analyze"])
    p.add_argument("--base", default="http://127.0.0.1:8031", help="service base URL")
    p.add_argument("--token", default="", help="bearer token (Authorization header)")
    p.add_argument("--session", default="repro", help="x-session-id base")
    p.add_argument("--question", default="盐雾测试方法有多少种", help="question to send")
    p.add_argument("--model", default="deepseek-v4-pro", help="model field")
    p.add_argument("--chunks", type=int, default=3, help="[disconnect] chunks before drop")
    p.add_argument("--delay", type=float, default=2.0, help="[slow] seconds between reads")
    p.add_argument("--streams", type=int, default=3, help="[concurrent] number of streams")
    p.add_argument("--hold", type=float, default=20.0, help="[concurrent] seconds stream#0 holds open")
    p.add_argument("--health-pings", type=int, default=10, help="[concurrent] number of /health pings")
    p.add_argument("--path", default=str(DEFAULT_PATH), help="[analyze] path to stream_diagnostics.jsonl")
    args = p.parse_args()

    if args.mode == "analyze":
        return analyze(Path(args.path))
    {
        "normal": scenario_normal,
        "disconnect": scenario_disconnect,
        "slow": scenario_slow,
        "concurrent": scenario_concurrent,
    }[args.mode](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
