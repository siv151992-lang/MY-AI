# Aria: a self-hosted AI assistant

Aria is a full-stack AI assistant in the style of Claude and ChatGPT. It plans, uses tools (web research, a Python sandbox, document search), checks its own answers, remembers you across chats and streams every step live. It runs on real model APIs (Anthropic, OpenAI, or local Ollama) through a pluggable provider layer, and nothing in the app is mocked.

```
┌──────────── Browser (Next.js 15 · React 19 · Tailwind · shadcn/ui) ────────────┐
│ Sidebar/history · Markdown + code highlight · uploads · model picker · themes │
└──────────────┬─────────────────────────────────────────────────────────────────┘
               │ same-origin /api/* (httpOnly session cookie, SSE streaming)
┌──────────────▼──────────────── FastAPI backend ────────────────────────────────┐
│ Auth (bcrypt + JWT cookie, CSRF header, rate limits)                           │
│ Chat service ── Orchestrator: Plan → Reason/Act (tool loop) → Verify → Revise  │
│   Reasoning engine   prompts.py      system prompt: how to think & answer      │
│   Planning engine    planner.py      decides when to decompose; JSON step plan │
│   Memory system      memory/         tiered: working window → state → rolling  │
│                                      summary → episodic segments → long-term   │
│                                      memories (pgvector, user-approved)        │
│   Tool engine        tools/base.py   typed registry, timeouts, errors → model  │
│   Research engine    tools/web.py    web_search (Tavily/Brave) + fetch_url     │
│   Coding engine      tools/code.py   run_python in the isolated sandbox        │
│   Verification eng.  verifier.py     independent reviewer pass, triggers revise│
│   Response engine    orchestrator →  SSE events: token, plan, tool_*, verify…  │
│ LLM layer: Anthropic + OpenAI-compatible (OpenAI, Ollama, Groq, OpenRouter…)   │
└───────┬─────────────────────────────────────────────┬──────────────────────────┘
        │                                             │ internal network only
┌───────▼────────── PostgreSQL 16 + pgvector ──┐  ┌───▼──── Python sandbox ────────┐
│ users · conversations · messages · files ·   │  │ no internet, read-only FS, non- │
│ document_chunks · memories · memory_revisions│
│ memory_settings · memory_audit ·             │
│ conversation_segments (vector, tsv)          │  │ root, rlimits, timeouts, token  │
└──────────────────────────────────────────────┘  └────────────────────────────────┘
```

## Reasoning engine

The reasoning engine lives in `backend/app/agent/reasoning/`. It is modular, and every component works with any provider:

| Component | File | What it does |
|---|---|---|
| Analyzer | `analyzer.py` | Before answering, it splits the request into **parts**, classifies the task (math, logic, analytical, technical, factual, creative), and spots **missing information** and **ambiguity**. It also decides how much effort and verification the task needs. Greetings and short factual questions skip this step and answer straight away. |
| Clarifier | `clarifier.py` | Asks a short question **only** when a wrong guess would be costly or required information is missing. Otherwise it goes ahead and states its interpretation and assumptions. It never asks twice in a row. |
| Planner | `planner.py` | Breaks the task into subtasks, each with a method (tool or reasoning) and a completion check. `ensure_coverage` adds a subtask for any part the plan skipped. |
| Strategies | `strategies.py` | Guidance for each domain: math is computed and checked with `run_python`, logic is tested against counterexamples, analysis is driven by criteria and trade-offs, technical work is reproduced and tested. You can add new domains with `register_strategy`. |
| Verifier | `verifier.py` | An independent check for coverage (every part answered), correctness, grounding and honesty about gaps, plus domain-specific checks. If a check fails, the answer is revised automatically. |
| Conversation state | `context.py` | Keeps a structured record of each chat (goals, constraints, decisions, key facts, artifacts, open questions), updates it after every turn and includes it in each prompt, so long conversations stay consistent. |
| Private reasoning | `privacy.py` | Models reason privately. Claude uses native extended thinking (thinking blocks stay on the server). OpenAI o-series models use `reasoning_effort`. Any other model uses a `<scratchpad>` that is stripped from the stream before anything reaches the browser or the database. Answers give **concise justifications**, not the model's internal deliberation. |
| Engine | `engine.py` | Combines the components above. The analyzer, planner and verifier are interfaces (Protocols), so you can swap in your own. |

Effort scales with complexity: no extra reasoning for simple tasks, low or medium for moderate ones, high for complex ones, and math and logic always go one level higher. Set each model's reasoning mode with the `"reasoning"` field in `models.json`.

In the chat, an **Understanding** panel shows the parts the request was split into and any interpretation, assumptions or missing information, followed by the plan, the tool calls and the self-check result.

## Autonomous agent

Open **Autonomous agent** in the sidebar (`/agent`), describe a goal and press **Start**. The code is in `backend/app/autonomy/`:

```
goal ─► plan (GoalPlanner: steps, tool for each step, success criteria — only tools that actually exist)
     ─► execute each step (StepExecutor: real tool calls, ends with complete_step)
           ├─ sensitive action? ─► PermissionGate: approval card in the UI, waits for you (approve / deny / timeout)
           ├─ tool error? ─► the model sees the error and adapts; up to N retries per step, with backoff
           └─ step still failing? ─► re-plan the remaining steps around the failure (bounded)
     ─► verify the goal against the real action log (claims without a tool action don't count)
           └─ partially done? ─► one remediation pass
     ─► report: steps, actions, approvals, errors and files are generated from recorded data;
                the model writes only a short summary of those facts
```

**Real actions only:**
- A step that needs a tool cannot be marked done unless a tool call actually succeeded. A "done" claim without one is rejected, and the model has to do the work.
- Tools are only available when they're configured. If no SMTP server is set, there is no `send_email`, so the agent can't pretend to send email. The planner also drops any tool that doesn't exist.

**Tools and permissions:**

| Tool | Effect | Needs approval |
|---|---|---|
| `web_search`, `fetch_url` | read the web | no |
| `run_python` | sandboxed computation | no |
| `search_documents`, `save_memory`, `search_memory`, `update_memory`, `forget_memory` | your Aria data | no |
| `create_file` | creates a downloadable file in your Aria library | no |
| `http_request` | external APIs | **yes**, for anything other than GET or HEAD |
| `send_email` | SMTP (`SMTP_*` in `.env`) | **always** |
| `post_slack_message` | Slack webhook (`SLACK_WEBHOOK_URL`) | **always** |

To add a tool, create a `Tool(...)` with `requires_approval=True` (or a predicate on its arguments) and register it in `autonomy/manager.py:build_agent_tools`.

**Activity panel:** a progress bar, approval cards (with the exact parameters), the current task with its live tool call, the plan with each step's status, attempts and result, errors and recovery, verification, and a live timeline. Events stream over SSE and are stored, so you can reload the page or come back later and see the full history.

**Limits:** you can set the limits in `.env` (`AUTONOMY_*`): retries per step, re-plans, model calls per step, approval timeout and concurrent runs. Runs execute inside the backend process. If the server restarts mid-run, that run is marked as interrupted rather than silently resumed.

## Memory architecture

The memory system lives in `backend/app/memory/`. It has five tiers, from most to least volatile:

| Tier | Where | What it holds |
|---|---|---|
| Working memory | `context.py` | The most recent turns, verbatim, sized to `CONTEXT_TOKEN_BUDGET`. |
| Conversation state | `agent/reasoning/context.py` | Structured goals, constraints, decisions and facts for each chat. |
| Rolling summary | `conversations.summary` | Prose summary of everything that has scrolled out of the working window. |
| Episodic segments | `conversation_segments` (pgvector + tsvector) | A summary of each compacted chunk of a chat. These are searched every turn, so an old detail comes back when it becomes relevant again. |
| Long-term memory | `memories` (pgvector + tsvector) | Durable facts about the user, each with a category, importance, confidence, sensitivity, version history and source. |

**How a turn uses memory**

1. **Automatic context management.** When the verbatim window overflows its budget, the oldest turns are compacted in one batch down to 60% of the budget. This hysteresis means summarisation doesn't run on every turn. Each chunk becomes an embedded segment, and the segments are folded into the rolling summary, which is re-compressed if it grows too long.
2. **Context retrieval.** Hybrid search over long-term memory: pgvector cosine similarity and Postgres full-text search, merged with reciprocal-rank fusion and then re-ranked by importance and recency. Pinned memories are always included. Results are packed into a token budget. Relevant segments from this chat are recalled the same way, and from your other chats too if you opt in.
3. **Transparency.** The answer shows which memories it used. The message stores only their ids, so deleting a memory leaves no copy behind.
4. **Learning.** After the answer, the utility model proposes facts (category, importance 1–5, confidence, and `supersedes` when the user corrected something already stored). They appear under the answer as *Remember this?* with Save, Correct and Dismiss buttons.

**Importance scoring** (`scoring.py`). The starting importance comes from the category (instructions > profile > preferences > projects > …), the source (added by you or asked for explicitly gets a boost), the extractor's estimate and its confidence. It is raised each time the same fact comes up again. When ranking, importance decays with a 120-day half-life down to a floor, gets a small boost from how often the memory is used, and never decays for pinned memories. You can set importance yourself with the dots next to each memory.

**Lifecycle.** `pending` (suggested) → `active` → `archived` (superseded or hidden) → deleted. Deletion is a hard delete, including revisions. Every create, edit, approval and correction is versioned in `memory_revisions`. The audit log (`memory_audit`) records *what happened* but never the content.

**Privacy rules** (`privacy.py`), all per user under *Memory → Privacy*:

| Setting | Default | Effect |
|---|---|---|
| Memory | on | Master switch for reading and writing long-term memory. |
| Use memories in chats | on | Inject relevant memories into prompts. |
| Learn from conversations | on | Propose facts automatically. |
| Ask before saving | **on** | Proposed facts wait for your approval. Sensitive or low-confidence ones always wait, whatever this is set to. |
| Allow sensitive topics | off | Health, beliefs, sexuality, politics, finances and legal status may be *proposed*. |
| Keep source quotes | on | Store the words a memory was learned from, with email addresses, phone numbers and secrets redacted. |
| Summarise long chats | on | When off, older turns are simply dropped and nothing derived from the chat is stored. |
| Recall across chats | off | Search summaries of your other chats. |
| Forget unused memories after | never | Retention: unpinned memories not used in N days are deleted. |
| Discard unreviewed suggestions after | 14 days | |

Secrets (passwords, API keys and tokens, Luhn-valid card numbers, SSNs, IBANs, private keys, ID numbers) are **never** stored, even if you ask. Each chat also has a **memory off** switch (the 🧠 button in the chat header) that stops that chat from reading or writing long-term memory.

**API** (all scoped to the signed-in user):

| Endpoint | Purpose |
|---|---|
| `GET /api/memories?status=&category=&q=&sort=` | List memories, or semantic search with `q` |
| `POST /api/memories` · `PATCH/DELETE /api/memories/{id}` · `GET /api/memories/{id}` | Add, correct/pin/archive, delete, or inspect (with revision history and source chat) |
| `POST /api/memories/{id}/approve` · `/reject` · `POST /api/memories/review` | Review suggestions, one at a time or in bulk |
| `POST /api/memories/delete` `{scope or ids, confirm: true, include_conversation_memory}` | Bulk delete |
| `GET /api/memories/export?format=json\|markdown` · `POST /api/memories/import` | Export everything (memories, revisions, settings, chat summaries, audit log) or import an export |
| `GET/PUT /api/memories/settings` · `GET /api/memories/audit` | Privacy settings and activity log |
| `GET/PATCH/DELETE /api/conversations/{id}/memory` | Inspect a chat's summary, segments and state; correct the summary; turn memory off for that chat; or reset it |

A maintenance task runs every 6 hours. It expires stale suggestions, applies retention, and backfills embeddings for memories saved while the embedding provider was down. Without `EMBEDDING_PROVIDER`, everything still works using full-text search.

## Quick start (Docker)

You need Docker Desktop (Windows or macOS) or Docker Engine (Linux).

```bash
cp .env.example .env
# edit .env: set JWT_SECRET and SANDBOX_TOKEN to long random strings,
#            and add at least one of ANTHROPIC_API_KEY / OPENAI_API_KEY / OLLAMA_BASE_URL
docker compose up --build
```

Then open **http://localhost:3000**, create an account and start chatting.

> **Windows:** run the commands in PowerShell from the project folder. To generate a secret, run `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

### Configuration

| What | Where |
|---|---|
| API keys, secrets, feature switches | `.env` (see `.env.example`) |
| Models shown in the selector | `backend/models.json`: add or edit entries (`provider` is `anthropic`, `openai` or `ollama`). Only models whose provider has a key appear. |
| Default and background models | `DEFAULT_MODEL_ID`, `UTILITY_MODEL_ID`. The utility model writes titles, plans, verification checks and memory extraction, so a cheap fast model works well here. |
| Semantic search | `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`. Without these, memory and document search use Postgres full-text search. **Set `EMBEDDING_DIM` before the first start**, because it is fixed into the schema. |
| Web research | `TAVILY_API_KEY` or `BRAVE_API_KEY` enables `web_search`. `fetch_url` always works. |

Model IDs change over time. If a provider rejects a model name, update `backend/models.json`.

## Features

- **Chat:** token streaming over SSE, a stop button (partial answers are kept), regenerate, copy, rename/delete chats, history grouped by date, a scroll-to-bottom button, drag-drop/paste uploads.
- **Agent mode** (the ✨ toggle): every answer gets a visible plan, tool calls and a self-check. Without the toggle, Aria still plans automatically for complex requests and verifies any answer that used tools.
- **Documents:** PDF, DOCX, text and code files are extracted and chunked, and embedded too when embeddings are configured. Small files go straight into context; large ones are read through `search_documents`.
- **Images:** sent to vision-capable models. Aria tells you if the selected model can't see images.
- **Memory:** Aria proposes durable facts it learns and you approve them inline or under *Account → Memory*, where you can also search, correct, pin, archive, export, import and delete. See [Memory architecture](#memory-architecture).
- **Long conversations:** older turns are condensed automatically into a rolling summary plus searchable sections, so details from far back can still be recalled.
- **Python execution:** in an isolated container, for calculations, data analysis and testing code before showing it.
- **Light, dark and system themes;** responsive layout with a slide-over sidebar on mobile.

## Security

- API keys live only in the backend environment, and the browser talks to the backend through a same-origin proxy.
- Passwords are hashed with bcrypt. Sessions are HS256 JWTs in **httpOnly, SameSite=Lax** cookies (`COOKIE_SECURE=true` behind HTTPS), and every state-changing request needs a CSRF header.
- Every database query is scoped by `user_id`. Uploads are stored under random IDs (never the client's filename), with file types detected from magic bytes and a size limit.
- `fetch_url` blocks private, loopback and metadata IPs on every redirect hop (SSRF protection).
- The sandbox has no network, a read-only filesystem and no capabilities. It runs as a non-root user with CPU, memory, file-size and process rlimits and a wall-clock timeout, and needs a bearer token.
- Login and signup are rate-limited, and so are chat messages (per user).
- Markdown is rendered without raw HTML, so model output can't inject scripts.

## Development without Docker

```bash
# database: any Postgres 16 with pgvector, then run db/init/01-schema.sh (needs POSTGRES_USER/POSTGRES_DB env)
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
JWT_SECRET=... ANTHROPIC_API_KEY=... SANDBOX_URL=http://localhost:8800 SANDBOX_TOKEN=dev \
  UPLOAD_DIR=./uploads uvicorn app.main:app --reload
SANDBOX_TOKEN=dev python sandbox/server.py
cd frontend && npm install && npm run dev        # http://localhost:3000, proxies /api to :8000
```

## Tests

```bash
cd backend && pytest            # 86 tests (incl. 10 autonomous-run scenarios: approval granted/denied,
                                # grounding guard, retry, re-plan, remediation, cancel, report): stream parsers (incl. thinking blocks), agent loop, reasoning
                                # engine (analyzer, clarifier, planner, verifier, state, scratchpad privacy),
                                # memory (privacy rules, scoring, rank fusion, extraction, compaction),
                                # tools, SSRF guard, chunking, PDF extraction, sandbox
# Memory integration tests against real PostgreSQL + pgvector (load db/init/01-schema.sh into an
# empty database first; skipped unless the URL is set):
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/aria_test pytest tests/test_memory_pg.py
cd frontend && npm test         # SSE parser
```

The orchestrator tests use a scripted provider *in the test suite only*, so plan → tool → verify → revise can be checked deterministically. The application itself always calls real APIs.

## Going to production

- Put it behind HTTPS (Caddy, nginx or Traefik), set `COOKIE_SECURE=true`, and set `CORS_ORIGINS` to your domain.
- Scale the backend horizontally: it is stateless apart from the in-memory rate limiter, which you can move to Redis for multiple replicas. Store uploads on shared storage (S3 or a shared volume).
- Small schema changes are applied automatically at startup (`db.MIGRATIONS`, idempotent). Use managed Postgres with pgvector, and add a schema migration tool (for example Alembic or sqitch) before changing the schema.
- For stronger sandbox isolation, run it under gVisor (`runtime: runsc`) or Firecracker.
