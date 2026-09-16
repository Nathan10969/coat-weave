from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request


def request_json(url: str, *, token: str | None = None, payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if payload is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"error": body}


def request_text(url: str, *, token: str | None = None, payload: dict | None = None) -> tuple[int, str]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=True).encode("ascii")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="GET" if payload is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("COATING_API_BASE_URL", "http://127.0.0.1:8031"))
    parser.add_argument("--token", default=os.environ.get("COATING_API_TOKEN", ""))
    parser.add_argument("--question", default="船舶防腐蚀配方有多少？举几个例子")
    parser.add_argument("--mock-model", action="store_true")
    parser.add_argument("--expect-open", action="store_true")
    args = parser.parse_args()

    health_code, health = request_json(f"{args.base_url}/health")
    ready_code, ready = request_json(f"{args.base_url}/ready")
    unauth_code, _ = request_json(
        f"{args.base_url}/api/v1/coating/chat",
        payload={"question": "auth check"},
    )
    chat_code, chat = request_json(
        f"{args.base_url}/api/v1/coating/chat",
        token=args.token,
        payload={
            "conversation_id": "smoke",
            "user_id": "smoke-user",
            "question": args.question,
            "options": {"include_debug": False, "mock_model": args.mock_model},
        },
    )
    platform_code, platform_stream = request_text(
        f"{args.base_url}/api/ask/stream/chat/completions",
        token=args.token,
        payload={
            "model": "deepseek-v4-pro",
            "stream": True,
            "conversation_id": "smoke-platform",
            "user": "smoke-user",
            "messages": [{"role": "user", "content": "How many test methods are in the coating KG?"}],
            "options": {"mock_model": args.mock_model},
        },
    )
    print(json.dumps({
        "health": {"code": health_code, "body": health},
        "ready": {"code": ready_code, "body": ready},
        "unauth_code": unauth_code,
        "chat": {"code": chat_code, "body": chat},
        "platform_stream": {
            "code": platform_code,
            "has_chunk": "\"chat.completion.chunk\"" in platform_stream,
            "has_done": "data: [DONE]" in platform_stream,
        },
    }, ensure_ascii=False, indent=2))
    expected_unauth = 200 if args.expect_open else 401
    if (
        health_code != 200
        or ready_code != 200
        or unauth_code != expected_unauth
        or chat_code != 200
        or platform_code != 200
        or "\"chat.completion.chunk\"" not in platform_stream
        or "data: [DONE]" not in platform_stream
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
