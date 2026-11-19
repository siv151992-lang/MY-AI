"""010 — platform tables. All statements are idempotent."""

MIGRATIONS = [
    # usage analytics: one row per model call
    """CREATE TABLE IF NOT EXISTS llm_calls (
  id            BIGSERIAL PRIMARY KEY,
  user_id       UUID REFERENCES users(id) ON DELETE CASCADE,
  feature       TEXT NOT NULL DEFAULT 'other',
  provider      TEXT NOT NULL,
  model_id      TEXT NOT NULL,
  input_tokens  INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  latency_ms    INTEGER NOT NULL DEFAULT 0,
  ttft_ms       INTEGER,
  ok            BOOLEAN NOT NULL,
  error         TEXT NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS llm_calls_user_time ON llm_calls(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS llm_calls_model_time ON llm_calls(model_id, created_at DESC)",
    # per-user preferences (routing etc.)
    """CREATE TABLE IF NOT EXISTS user_prefs (
  user_id    UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  prefs      JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    # projects
    """CREATE TABLE IF NOT EXISTS projects (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL DEFAULT '',
  archived     BOOLEAN NOT NULL DEFAULT false,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS projects_user ON projects(user_id, updated_at DESC)",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE SET NULL",
    "ALTER TABLE files ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE SET NULL",
    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE SET NULL",
    "ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE SET NULL",
    "ALTER TABLE team_runs ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE SET NULL",
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE SET NULL",
    "ALTER TABLE memories ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE SET NULL",
    # reusable skills
    """CREATE TABLE IF NOT EXISTS skills (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL,
  inputs       JSONB NOT NULL DEFAULT '[]'::jsonb,
  tools        JSONB NOT NULL DEFAULT '[]'::jsonb,
  model_id     TEXT,
  project_id   UUID REFERENCES projects(id) ON DELETE SET NULL,
  uses         INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS skills_user ON skills(user_id, updated_at DESC)",
    # custom agents
    """CREATE TABLE IF NOT EXISTS custom_agents (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  instructions TEXT NOT NULL,
  tools        JSONB NOT NULL DEFAULT '[]'::jsonb,
  model_id     TEXT,
  use_memory   BOOLEAN NOT NULL DEFAULT true,
  in_team      BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS custom_agents_user ON custom_agents(user_id, updated_at DESC)",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS agent_id UUID REFERENCES custom_agents(id) ON DELETE SET NULL",
    # API integrations (secrets encrypted at rest)
    """CREATE TABLE IF NOT EXISTS integrations (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  slug         TEXT NOT NULL,
  description  TEXT NOT NULL DEFAULT '',
  base_url     TEXT NOT NULL,
  auth_type    TEXT NOT NULL DEFAULT 'none',
  auth_name    TEXT NOT NULL DEFAULT '',
  secret_enc   TEXT,
  allow_writes BOOLEAN NOT NULL DEFAULT false,
  test_path    TEXT NOT NULL DEFAULT '',
  last_test    JSONB,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, slug)
)""",
    # automations / workflows
    """CREATE TABLE IF NOT EXISTS automations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id    UUID REFERENCES projects(id) ON DELETE SET NULL,
  name          TEXT NOT NULL,
  enabled       BOOLEAN NOT NULL DEFAULT true,
  trigger_type  TEXT NOT NULL DEFAULT 'manual',
  schedule      TEXT NOT NULL DEFAULT '',
  timezone      TEXT NOT NULL DEFAULT 'UTC',
  webhook_hash  TEXT,
  steps         JSONB NOT NULL DEFAULT '[]'::jsonb,
  notify        JSONB NOT NULL DEFAULT '{}'::jsonb,
  next_run_at   TIMESTAMPTZ,
  last_run_at   TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)""",
    "CREATE INDEX IF NOT EXISTS automations_due ON automations(next_run_at) WHERE enabled",
    """CREATE TABLE IF NOT EXISTS automation_runs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  automation_id UUID NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  trigger       TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'running',
  steps         JSONB NOT NULL DEFAULT '[]'::jsonb,
  output        TEXT NOT NULL DEFAULT '',
  error         TEXT NOT NULL DEFAULT '',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ
)""",
    "CREATE INDEX IF NOT EXISTS automation_runs_auto ON automation_runs(automation_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS automation_runs_user ON automation_runs(user_id, created_at DESC)",
]
