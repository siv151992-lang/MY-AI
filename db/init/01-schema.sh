#!/bin/bash
# Runs once on first Postgres container start (docker-entrypoint-initdb.d).
# EMBEDDING_DIM must match the embedding model you configure (1536 = OpenAI text-embedding-3-small,
# 768 = Ollama nomic-embed-text).
set -euo pipefail
DIM="${EMBEDDING_DIM:-1536}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       TEXT NOT NULL DEFAULT 'New chat',
  model_id    TEXT,
  summary     TEXT,                       -- rolling summary of older turns (long-context support)
  summarized_upto INTEGER NOT NULL DEFAULT 0,
  state       JSONB NOT NULL DEFAULT '{}'::jsonb, -- structured working memory (reasoning engine)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX conversations_user_updated ON conversations(user_id, updated_at DESC);

CREATE TABLE messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content         TEXT NOT NULL DEFAULT '',
  file_ids        UUID[] NOT NULL DEFAULT '{}',
  meta            JSONB NOT NULL DEFAULT '{}'::jsonb,  -- plan, tool calls, verification, usage, model
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, seq)
);

CREATE TABLE files (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  filename     TEXT NOT NULL,
  mime_type    TEXT NOT NULL,
  size_bytes   BIGINT NOT NULL,
  storage_path TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('document','image')),
  text_content TEXT,
  status       TEXT NOT NULL DEFAULT 'ready',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX files_user ON files(user_id);

CREATE TABLE document_chunks (
  id        BIGSERIAL PRIMARY KEY,
  file_id   UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  idx       INTEGER NOT NULL,
  content   TEXT NOT NULL,
  embedding vector(${DIM}),
  tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE INDEX document_chunks_file ON document_chunks(file_id);
CREATE INDEX document_chunks_tsv ON document_chunks USING gin(tsv);
CREATE INDEX document_chunks_embedding ON document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE memories (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  embedding  vector(${DIM}),
  tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX memories_user ON memories(user_id);
CREATE INDEX memories_tsv ON memories USING gin(tsv);
CREATE INDEX memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);

-- Autonomous agent runs
CREATE TABLE IF NOT EXISTS agent_runs (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  goal         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'planning',
  model_id     TEXT,
  report       TEXT NOT NULL DEFAULT '',
  verification JSONB,
  error        TEXT NOT NULL DEFAULT '',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agent_runs_user ON agent_runs(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS agent_steps (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id           UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  idx              INTEGER NOT NULL,
  title            TEXT NOT NULL,
  description      TEXT NOT NULL DEFAULT '',
  tool             TEXT NOT NULL DEFAULT 'none',
  success_criteria TEXT NOT NULL DEFAULT '',
  status           TEXT NOT NULL DEFAULT 'pending',
  attempts         INTEGER NOT NULL DEFAULT 0,
  result           TEXT NOT NULL DEFAULT '',
  error            TEXT NOT NULL DEFAULT '',
  origin           TEXT NOT NULL DEFAULT 'plan',
  started_at       TIMESTAMPTZ,
  finished_at      TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS agent_steps_run ON agent_steps(run_id, idx);
CREATE TABLE IF NOT EXISTS agent_approvals (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id     UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  step_idx   INTEGER NOT NULL,
  tool       TEXT NOT NULL,
  args       JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary    TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS agent_actions (
  id           BIGSERIAL PRIMARY KEY,
  run_id       UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  step_idx     INTEGER NOT NULL,
  tool         TEXT NOT NULL,
  args_summary TEXT NOT NULL DEFAULT '',
  ok           BOOLEAN NOT NULL,
  output       TEXT NOT NULL DEFAULT '',
  approval     TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_actions_run ON agent_actions(run_id, id);
CREATE TABLE IF NOT EXISTS agent_events (
  run_id     UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  type       TEXT NOT NULL,
  data       JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, seq)
);
SQL
