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
