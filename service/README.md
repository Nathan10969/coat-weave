# CoatWeave API service

This directory provides a FastAPI question-answering service and a separate
read-only KG tools server. The API coordinates retrieval, document scope, model
calls and conversation memory. Clients provide their own user interface.

The independent [Pi Harness service](pi-harness/README.md) is also included under
`service/pi-harness`. It runs as a separate Node.js process and retains its own
FastAPI/Milvus patent backend by default. Its opt-in KG adapter calls this
service's existing KG HTTP tools without replacing their implementations. Pi is
not the runtime used by the Python API below. See its own setup, privacy
boundaries and tests before use; copying the source is not a production rollout.

## Local setup

From the repository root, with Python 3.10 or newer:

```bash
cd service
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install psycopg2-binary
cp config/.env.example .env
```

Edit `.env`: set `COATING_SERVICE_ROOT` to this directory and `COATING_DATA_ROOT`
to a writable runtime directory. Configure your model provider, database, Redis
and KG/embedding services as needed. Supply your own data and index.

Set a private `COATING_API_TOKEN` and keep `COATING_DISABLE_AUTH=false`. Requests
use `Authorization: Bearer <token>`. `COATING_ALLOWED_IPS` can restrict callers in
addition to token authentication. Configure `KG_EXPAND_TOKEN` when protecting the
KG tools service. Store credentials in your local `.env` or deployment secret store.

The API storage uses `psycopg`; KG tools additionally uses `psycopg2`, installed
above. Initialize storage, then export the configuration for KG tools in one
terminal. This loading method requires shell-compatible assignments in `.env`:

```bash
python scripts/init_db.py
set -a
source .env
set +a
KG_EXPAND_HOST=127.0.0.1 KG_EXPAND_PORT=8021 python kg_tools/kg_expand_http_service.py
```

In a second terminal, from `service/` with the same environment activated:

```bash
python -m uvicorn api.main:app --host 127.0.0.1 --port 8031
```

The API settings loader reads `service/.env`. `/health` reports process health;
`/ready` checks configured dependencies. Once your dependencies are available,
export `COATING_API_TOKEN` in the calling shell and exercise the API:

```bash
python scripts/smoke_test.py --base-url http://127.0.0.1:8031 --token "$COATING_API_TOKEN"
```

The smoke script sends model/retrieval requests; it is not an offline unit test.

## Requests

Set `COATING_API_TOKEN` in the calling shell to match the service, then send:

```bash
curl http://127.0.0.1:8031/api/v1/coating/chat \
  -H "Authorization: Bearer $COATING_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"research-1","user_id":"researcher-1","question":"船舶防腐蚀配方有多少？举几个例子","options":{"include_debug":false}}'
```

Debug fields are hidden by default. Restrict requests with `include_debug=true`
to trusted operators.

The native streaming endpoint is `POST /api/v1/coating/chat/stream`. An
OpenAI-compatible streaming endpoint is also available:

```http
POST /api/ask/stream/chat/completions
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "model": "deepseek-v4-pro",
  "stream": true,
  "conversation_id": "research-1",
  "user": "researcher-1",
  "messages": [{"role": "user", "content": "有多少种测试方法？"}]
}
```

The compatible response uses Server-Sent Events with `chat.completion.chunk`
payloads and ends with `data: [DONE]`. It shares the native endpoint's routing,
memory, KG tools and answer generation.

## Deployment

The [systemd units](systemd/) are templates. Adjust `WorkingDirectory`,
`EnvironmentFile`, `ExecStart` and the service user for your installation before
enabling them. Their checked-in paths are example installation locations.

For remote access, configure a TLS reverse proxy for your own hostname and keep
backend ports private. For example,
`https://example.org/api/ask/stream/chat/completions` illustrates the compatible
route; it is not a live project deployment. Preserve bearer authentication when
forwarding requests and use an IP allowlist where appropriate. Any extra proxy
path prefixes must be configured in your own proxy.
