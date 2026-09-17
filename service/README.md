# A100 Coating API Service

This folder is the platform-facing migration of the local dialogue-memory KG demo.
It intentionally excludes the browser demo `static/` UI. Company frontend code
should call the HTTP API and render its own coating page.

## Services

- `coating-api.service`: FastAPI orchestrator on `0.0.0.0:8031`.
- `coating-kg-tools.service`: read-only KG tools on `127.0.0.1:8021`.

The API service owns routing, doc-scope memory, planner orchestration, model
calls, and compact API responses. The KG tools service owns hybrid search,
hyperedge multi-hop expansion, and template SQL aggregation.

## Setup On A100

```bash
cd /opt/coat-weave/service
/opt/coat-weave/.venv/bin/python -m pip install -r requirements.txt
cp config/.env.example .env
# edit .env with the real token, DeepSeek key, Postgres DSN, Redis URL, and allowed IPs
/opt/coat-weave/.venv/bin/python scripts/init_db.py
cp systemd/coating-api.service /etc/systemd/system/
cp systemd/coating-kg-tools.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now coating-kg-tools.service coating-api.service
```

## Smoke

```bash
cd /opt/coat-weave/service
/opt/coat-weave/.venv/bin/python scripts/smoke_test.py \
  --base-url http://127.0.0.1:8031 \
  --token "$COATING_API_TOKEN"
```

If the platform backend requires a bare URL with no auth header, set
`COATING_DISABLE_AUTH=true` and leave `COATING_ALLOWED_IPS` empty in `.env`,
then restart `coating-api.service`.

## Main API

```http
POST /api/v1/coating/chat
Content-Type: application/json
```

```json
{
  "conversation_id": "customer-session-1",
  "user_id": "customer-1",
  "question": "船舶防腐蚀配方有多少？举几个例子",
  "options": {
    "include_debug": false
  }
}
```

Debug fields are hidden by default. Set `include_debug=true` only for internal
operator calls.

## Platform-Compatible Streaming API

For the company platform's OpenAI-compatible streaming caller, use:

```http
POST /api/ask/stream/chat/completions
Content-Type: application/json
```

Public nginx URL:

```text
https://example.com/api/ask/stream/chat/completions
```

Request shape:

```json
{
  "model": "deepseek-v4-pro",
  "stream": true,
  "conversation_id": "customer-session-1",
  "user": "customer-1",
  "messages": [
    {"role": "user", "content": "有多少种测试方法？"}
  ]
}
```

The response is SSE using `chat.completion.chunk` payloads and ends with
`data: [DONE]`. Nginx also keeps the prefixed debug alias
`/coating/api/ask/stream/chat/completions`, but backend integration should use
the root platform path above. The endpoint reuses the same routing, memory, KG
tools, and DeepSeek answer path as `/api/v1/coating/chat`.
