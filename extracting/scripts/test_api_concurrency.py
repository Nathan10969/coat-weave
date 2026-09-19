"""Probe DashScope qwen-plus's actual rate limit / concurrency ceiling.

Sends N parallel "reply ok" minimal requests, measures success vs 429s.
Walks N from 2 to 32, finds the safe concurrent level for our key.

Run:
    cd G:\\coating_1\\coating_kg
    & "G:\\miniconda3\\envs\\coating\\python.exe" scripts\\test_api_concurrency.py

Output: a table per concurrency level showing
    requests / 200 OK / 429 rate-limited / mean latency / wall time
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parent.parent
ENV = REPO / ".env"

# Concurrency levels to try
LEVELS = [2, 4, 8, 16, 32]
# How many requests per level (more = more reliable measurement)
REQS_PER_LEVEL = 30
# Per-request timeout
TIMEOUT_S = 30


def load_env() -> tuple[str, str, str]:
    if not ENV.exists():
        print(f"ERROR: .env not found at {ENV}", file=sys.stderr)
        sys.exit(1)
    for line in ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("QWEN_TEXT_MODEL", "qwen-plus")
    if not api_key or not base_url:
        print("ERROR: OPENAI_API_KEY / OPENAI_BASE_URL not set in .env", file=sys.stderr)
        sys.exit(1)
    return api_key, base_url.rstrip("/"), model


async def one_request(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    body: dict,
    sem: asyncio.Semaphore,
    req_id: int,
) -> tuple[int, float, str]:
    """One ping. Returns (status_code, elapsed_s, error_or_short_response)."""
    async with sem:
        t0 = time.time()
        try:
            async with session.post(
                url, headers=headers, json=body,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as resp:
                status = resp.status
                # Drain body so connection is reusable
                text = await resp.text()
                elapsed = time.time() - t0
                short = text[:80].replace("\n", " ")
                return status, elapsed, short
        except asyncio.TimeoutError:
            return -1, time.time() - t0, "TIMEOUT"
        except Exception as exc:
            return -2, time.time() - t0, f"{type(exc).__name__}: {exc}"


async def run_at_concurrency(
    api_key: str, base_url: str, model: str,
    concurrency: int, n_requests: int,
) -> dict:
    """Fire ``n_requests`` against the qwen-plus chat endpoint with a max
    in-flight = ``concurrency``. Return summary stats."""
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 5,
        "temperature": 0,
    }
    sem = asyncio.Semaphore(concurrency)

    t_start = time.time()
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[
            one_request(session, url, headers, body, sem, i)
            for i in range(n_requests)
        ])
    wall = time.time() - t_start

    by_status: dict[int, int] = {}
    latencies: list[float] = []
    sample_429: str | None = None
    sample_other_err: str | None = None
    for status, elapsed, sample in results:
        by_status[status] = by_status.get(status, 0) + 1
        if 200 <= status < 300:
            latencies.append(elapsed)
        elif status == 429 and sample_429 is None:
            sample_429 = sample
        elif status not in (200, 429) and sample_other_err is None:
            sample_other_err = sample

    return {
        "concurrency": concurrency,
        "total": n_requests,
        "by_status": by_status,
        "ok_count": by_status.get(200, 0),
        "rate_limit_count": by_status.get(429, 0),
        "timeout_count": by_status.get(-1, 0),
        "other_err_count": sum(v for k, v in by_status.items() if k not in (200, 429, -1)),
        "wall_s": wall,
        "throughput_rps": by_status.get(200, 0) / wall if wall > 0 else 0,
        "mean_latency_s": sum(latencies) / len(latencies) if latencies else 0,
        "max_latency_s": max(latencies) if latencies else 0,
        "sample_429": sample_429,
        "sample_other_err": sample_other_err,
    }


async def main() -> int:
    api_key, base_url, model = load_env()
    print(f"Endpoint:   {base_url}/chat/completions")
    print(f"Model:      {model}")
    print(f"Levels:     {LEVELS}")
    print(f"Reqs/level: {REQS_PER_LEVEL}")
    print()
    print(f"{'concur':>7} {'OK':>5} {'429':>5} {'tmout':>5} {'err':>5} {'wall(s)':>8} {'rps':>7} {'mean(s)':>8} {'max(s)':>7}")
    print("-" * 78)

    safe_max = 1
    for level in LEVELS:
        # Cooldown between levels so rate-limit doesn't bleed
        if level != LEVELS[0]:
            await asyncio.sleep(15)

        stats = await run_at_concurrency(api_key, base_url, model, level, REQS_PER_LEVEL)
        print(f"{stats['concurrency']:>7} {stats['ok_count']:>5} "
              f"{stats['rate_limit_count']:>5} {stats['timeout_count']:>5} "
              f"{stats['other_err_count']:>5} {stats['wall_s']:>8.1f} "
              f"{stats['throughput_rps']:>7.2f} "
              f"{stats['mean_latency_s']:>8.2f} {stats['max_latency_s']:>7.2f}")
        if stats["sample_429"]:
            print(f"          sample 429 body: {stats['sample_429']!r}")
        if stats["sample_other_err"]:
            print(f"          sample other err: {stats['sample_other_err']!r}")

        # Heuristic for "safe concurrency": no 429 + no other errors
        if stats["rate_limit_count"] == 0 and stats["other_err_count"] == 0:
            safe_max = level

    print()
    print(f"=== SAFE CONCURRENCY: {safe_max} ===")
    print(f"Use this many parallel ingest jobs at most. Going beyond"
          f" started seeing 429s.")
    print()
    print("Note: this measures TOY requests (max_tokens=5). Real ingest "
          "requests are much heavier (~3500 tokens in, ~3000 tokens out).")
    print("DashScope may apply a separate token-rate limit that you won't "
          "see here. If real ingest at concurrency={} hits 429s, drop to {}.".format(
              safe_max, max(1, safe_max // 2)))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
