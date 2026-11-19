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
│   Memory engine      memory_engine   long-term facts (pgvector) + rolling      │
│                                      conversation summaries for long chats     │
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
│ document_chunks(vector, tsv) · memories      │  │ root, rlimits, timeouts, token  │
└──────────────────────────────────────────────┘  └────────────────────────────────┘
```

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
- **Memory:** facts are extracted automatically after each turn and deduplicated, and the model can also save them on request. You can view, add or delete memories under *Account → Memory*.
- **Long conversations:** older turns are folded into a rolling summary when the context budget is reached.
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
cd backend && pytest            # 33 tests: stream parsers, format converters, agent loop, planner,
                                # verifier, memory, tools, SSRF guard, chunking, PDF extraction, sandbox
cd frontend && npm test         # SSE parser
```

The orchestrator tests use a scripted provider *in the test suite only*, so plan → tool → verify → revise can be checked deterministically. The application itself always calls real APIs.

## Going to production

- Put it behind HTTPS (Caddy, nginx or Traefik), set `COOKIE_SECURE=true`, and set `CORS_ORIGINS` to your domain.
- Scale the backend horizontally: it is stateless apart from the in-memory rate limiter, which you can move to Redis for multiple replicas. Store uploads on shared storage (S3 or a shared volume).
- Use managed Postgres with pgvector, and add a schema migration tool (for example Alembic or sqitch) before changing the schema.
- For stronger sandbox isolation, run it under gVisor (`runtime: runsc`) or Firecracker.
