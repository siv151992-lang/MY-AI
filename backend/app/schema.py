"""Base schema (users, conversations, messages, files, document chunks, memories).

Created by the backend at startup, so Docker and the native (no-Docker) setup share one definition.
`{DIM}` is the embedding dimension. Without pgvector, `vector({DIM})` columns become a stand-in `vector`
domain (see db.ensure_extensions) and indexes using `vector_cosine_ops` are skipped.
"""

BASE = [
    """CREATE TABLE IF NOT EXISTS users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT NOT NULL UNIQUE,
  name          TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    """CREATE TABLE IF NOT EXISTS conversations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       TEXT NOT NULL DEFAULT 'New chat',
  model_id    TEXT,
  summary     TEXT,                       -- rolling summary of older turns (long-context support)
  summarized_upto INTEGER NOT NULL DEFAULT 0,
  state       JSONB NOT NULL DEFAULT '{}'::jsonb, -- structured working memory (reasoning engine)
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    """CREATE INDEX IF NOT EXISTS conversations_user_updated ON conversations(user_id, updated_at DESC)""",
    """CREATE TABLE IF NOT EXISTS messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  seq             INTEGER NOT NULL,
  role            TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content         TEXT NOT NULL DEFAULT '',
  file_ids        UUID[] NOT NULL DEFAULT '{}',
  meta            JSONB NOT NULL DEFAULT '{}'::jsonb,  -- plan, tool calls, verification, usage, model
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (conversation_id, seq)
)""",
    """CREATE TABLE IF NOT EXISTS files (
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
)""",
    """CREATE INDEX IF NOT EXISTS files_user ON files(user_id)""",
    """CREATE TABLE IF NOT EXISTS document_chunks (
  id        BIGSERIAL PRIMARY KEY,
  file_id   UUID NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  idx       INTEGER NOT NULL,
  content   TEXT NOT NULL,
  embedding vector({DIM}),
  tsv       tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
)""",
    """CREATE INDEX IF NOT EXISTS document_chunks_file ON document_chunks(file_id)""",
    """CREATE INDEX IF NOT EXISTS document_chunks_tsv ON document_chunks USING gin(tsv)""",
    """CREATE INDEX IF NOT EXISTS document_chunks_embedding ON document_chunks USING hnsw (embedding vector_cosine_ops)""",
    """CREATE TABLE IF NOT EXISTS memories (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  source_conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  embedding  vector({DIM}),
  tsv        tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    """CREATE INDEX IF NOT EXISTS memories_user ON memories(user_id)""",
    """CREATE INDEX IF NOT EXISTS memories_tsv ON memories USING gin(tsv)""",
    """CREATE INDEX IF NOT EXISTS memories_embedding ON memories USING hnsw (embedding vector_cosine_ops)""",
]
