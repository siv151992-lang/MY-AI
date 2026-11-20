# Aria: a self-hosted AI assistant

Aria is a full-stack AI assistant in the style of Claude and ChatGPT. It plans, uses tools (web research, a Python sandbox, document search), checks its own answers, remembers you across chats and streams every step live. It runs on real model APIs (Anthropic, OpenAI, or local Ollama) through a pluggable provider layer, and nothing in the app is mocked.

```
┌──────────── Browser (Next.js 15 · React 19 · Tailwind · shadcn/ui) ────────────┐
│ Chat: history · Markdown · uploads · model picker · memory panel · themes      │
│ Code workspace: file explorer · CodeMirror editor · terminal · diffs · git ·   │
│                 AI panel with approval cards and per-turn undo                 │
│ Deep research: live progress · cited report · sources · claims · MD/PDF export │
│ Documents: library · ask (RAG) · compare · extract · tables · generate · share │
│ Agent team: live agent roster · task graph · conflicts · verification          │
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
│   Coding workspace   workspace/      git-backed projects, code tools, approval │
│                                      gate, checkpoints, sandboxed commands     │
│   Deep research      research/       plan → search → read → quote-verified     │
│                                      claims → cross-check → verify → report    │
│   Documents          documents/      extract + OCR → chunks/tables → hybrid    │
│                                      RAG; access checks on every read          │
│   Agent team         team/           plan → parallel specialists → reconcile   │
│                                      conflicts → synthesize → verify           │
│   Tool engine        tools/base.py   typed registry, timeouts, errors → model  │
│   Research engine    tools/web.py    web_search (Tavily/Brave) + fetch_url     │
│   Coding engine      tools/code.py   run_python in the isolated sandbox        │
│   Verification eng.  verifier.py     independent reviewer pass, triggers revise│
│   Response engine    orchestrator →  SSE events: token, plan, tool_*, verify…  │
│ LLM layer: Anthropic + OpenAI-compatible (OpenAI, Ollama, Groq, OpenRouter…)   │
└───────┬─────────────────────────────────────────────┬──────────────────────────┘
        │                                             │ internal network only
┌───────▼────────── PostgreSQL 16 + pgvector ──┐  ┌───▼──── Sandbox ───────────────┐
│ users · conversations · messages · files ·   │  │ /run  Python snippets          │
│ document_chunks · memories (+revisions,      │  │ /exec shell commands on a copy │
│ settings, audit) · conversation_segments ·   │  │ of a project (python, pytest,  │
│ workspaces · workspace_activity · agent_* ·  │
│ research_runs/sources/claims/events ·        │
│ document_tables · document_access ·          │
│ team_runs/tasks/events                       │  │ node, npm, git, make)          │
└──────────────────────────────────────────────┘  │ no internet, read-only FS, non-│
  workspaces volume: one git repo per project      │ root, rlimits, timeouts, token │
                                                   └────────────────────────────────┘
```

## Free local AI and basic mode (no API key)

- **Free local AI.** Aria finds an [Ollama](https://ollama.com) server running on the same computer by itself. From Docker it reaches the host through `host.docker.internal`. Every chat model installed in Ollama shows up in the model list as `ollama:<name>`.
  - **Downloading a model:** if Ollama is running but has no model yet, the chat screen offers a one-click download with a progress bar: `llama3.2:3b` (2 GB, about 8 GB RAM), `qwen2.5:7b` (4.7 GB, about 16 GB RAM) or `llama3.2:1b`.
  - **The launcher:** on first run, `aria.bat` offers to install Ollama (with winget, or the official installer) and download `llama3.2:3b`. It only asks once, and skips the question when an API key is already set.
  - **Models without tool support:** if a model can't use tools, Aria answers without them instead of failing.
  - **Privacy:** chats with local models never leave the computer.
- **Basic mode.** With no model connected at all, chat still answers simple things without any AI. It handles greetings and small talk, help with Aria, the date and time, safe arithmetic (`15% of 80`, `sqrt(144)`), unit conversions (length, mass, volume, speed, temperature), capitals, and a small built-in fact base (`backend/app/basic/knowledge.py`; add your own entries there). It also does keyword search of your uploaded documents, quoting the file and page. When basic mode doesn't know something, it says so instead of guessing. Answers are marked `basic`.

## Platform hub: one workspace for everything

Aria opens on **Home** (`/home`): a single box for any request. As you type, the adaptive router shows how it would handle it. You see the task type, its complexity, the model it would use, the tools it needs and the reasons. You can then **Ask in chat**, or launch the workspace it suggests: Deep research, the agent team, a code workspace, or an automation. Home also shows live tasks, your projects and the last 7 days of usage. Every page shares one navigation bar.

| Module | What it does | Where |
|---|---|---|
| **Adaptive model routing** | Choose **Auto** as the chat model. Each message is classified (chat, coding, reasoning, research, writing, analysis, vision; low/medium/high complexity) and the connected models are scored by tier, strengths, vision/tool support, measured error rate and latency over the last 24 h, and your preferences (balanced / quality / speed / cost, local-only, per-category pins). If a model fails before it produces anything, the next-best model takes over automatically. Every answer carries a "why this model" badge. | `hub/routing.py`, Analytics → Routing preferences |
| **Usage & performance analytics** | Every model call is recorded: user, feature, model, tokens, latency, time to first token, and errors. Analytics shows totals, p50/p95 latency, error rates per model and feature, task outcomes, and routing decisions. The owner can switch to everyone's usage. Cost appears only for models where you set `price_in`/`price_out` (USD per million tokens) in `models.json`; it is never guessed. | `hub/usage.py`, `/analytics` |
| **Project workspaces** | A project groups chats, documents, code workspaces, research, team runs, agent runs and automations, with instructions that apply to every chat in it. | `hub/projects.py`, `/projects` |
| **Context-aware memory** | Memories learned in a project chat are tagged with that project. Recall inside a project sees global memories plus that project's; outside a project, other projects' memories are never used. The Memory panel still shows everything. | `memory/store.py` (`PROJECT_SCOPE`) |
| **Reusable skills** | Named instructions with `{{input}}` fields, allowed tools and an optional model. Run a skill on the Skills page, apply it to a chat message (✦ button in the chat box), or use it as an automation step. Five built-in skills are included. | `hub/skills.py`, `/skills` |
| **Custom agents** | Your own specialists, each with instructions, tools and a model. You can chat with one, give it a one-off task, or use it as an automation step. If enabled, the **agent team** planner can assign it work next to the built-in agents. | `hub/custom_agents.py`, `/agents` |
| **API integrations** | Connect any HTTP/JSON API once (base URL plus bearer, header, query or basic auth). It becomes a tool `api_<name>` for chats, skills, agents and automations. Credentials are encrypted with AES-256-GCM (key derived from `JWT_SECRET`) and never returned to the browser. Only public hosts are allowed, checked on save and on every call, and redirects aren't followed. GET requests are always allowed. Write requests work **only in automations**, and only when you switch on "allow writes" for that integration. | `hub/integrations.py`, `/integrations` |
| **Automations & autonomous workflows** | Workflows of up to 10 steps: AI task, skill, custom agent, Deep research, agent team, API call, notification. Steps pass results along with `{{prev}}`, `{{steps.N}}`, `{{date}}` and `{{payload.field}}`. Triggers are manual, a schedule (cron or `@daily`/`@every 30m`, time-zone aware, minimum 5 minutes; safe with several backend processes), or a secret webhook URL (token stored hashed, shown once). Each run records the status and output of every step. | `hub/automations.py`, `hub/cron.py`, `/automations` |
| **Real-time task monitor** | Autonomous-agent, research, team and automation runs in one live list (SSE), with progress, duration, errors and cancel. | `hub/tasks.py`, `/tasks` |
| **Voice** | Microphone input in the chat box and on Home. With an OpenAI key, audio is transcribed on your server (`whisper-1`). Otherwise the browser's built-in speech recognition is used (Chrome, Edge, Safari), and where neither exists the button is hidden. **Read aloud** on answers uses the browser's speech synthesis. | `hub/voice.py`, `components/hub/VoiceButton.tsx` |

**What needs outside setup.** Integrations → Built-in services shows the live status of each:

- **Web search** needs a Tavily or Brave key. It powers Deep research, the research agent and the "Web brief" skill.
- **Code execution** needs the sandbox, i.e. the Docker version.
- **Email notifications** need SMTP settings.
- **Slack notifications** need `SLACK_WEBHOOK_URL`.
- **Server-side voice transcription** needs an OpenAI key.
- **Semantic search** needs an embedding provider and pgvector.

If an automation step relies on something that isn't configured, it fails with a message naming the missing setting. Nothing pretends to work.

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

## Coding workspace

Open **Code workspace** in the sidebar (`/code`). Each workspace is a real project, stored as a git repository on the `workspaces` volume. You can start one blank, clone it from a public HTTPS git URL, or upload a `.zip`.

The screen has four parts:

- **File explorer:** create, rename, move and delete files and folders. Files with uncommitted changes are coloured by their git status.
- **Editor:** CodeMirror with syntax highlighting for most languages. Save with Ctrl/⌘+S. Before saving, it checks the file hasn't been changed underneath you (for example by the AI), so it never silently overwrites.
- **Terminal:** runs commands in the sandbox (details below).
- **AI panel:** a chat that works on the project. The open file and any selected lines are sent along as context.

The side bar also has **Search** (text, regex, or a natural-language query that ranks files) and **Source control** (changed files with diffs, discard, commit, branches, history, remote, push).

**What the AI can do** (`backend/app/workspace/tools.py`):

| Capability | Tools |
|---|---|
| Read and understand the codebase | `repo_map` (every file with its top-level functions and classes, also put into the prompt), `list_files`, `read_file`, `search_code` (literal, regex, or a natural-language query ranked with BM25 over code, paths and symbol names) |
| Generate and edit across many files | `write_files` (scaffold a whole application in one call), `write_file`, `edit_file` (exact search-and-replace that must match exactly once, so edits stay small and reviewable), `move_path` |
| Debug and run tests | `run_command`, `run_tests` (detects pytest, `npm test`, `node --test`, `go test`, `cargo test` or `make test`) |
| Git | `git_status`, `git_diff`, `git_log`, `git_commit` |
| Delete | `delete_path` |

The coding prompt has the AI work like an engineer: read before changing, make focused edits, run the tests, fix the failures it sees, then summarise what changed and how it was verified. Explaining code, refactoring and writing documentation are ordinary requests to the same agent. Quick-start buttons in the AI panel cover the common ones.

**Approvals and undo.**

| Action | What happens |
|---|---|
| The AI creates or edits files | Applied straight away and shown live: the explorer refreshes and open tabs reload. |
| The AI deletes files, commits, or runs a command that deletes files | Pauses at an approval card showing the exact files, or the commit message and diffstat. Nothing happens until you click Approve. If you deny it, or don't answer within `WORKSPACE_APPROVAL_TIMEOUT_MIN` minutes, the AI is told to carry on without it. |
| Push (publishing) | The AI has no push tool. You push from Source control after a confirmation dialog. The access token is used for that one push via an environment-only HTTP header and is never written to disk. |
| You delete, discard changes, restore a checkpoint or delete a workspace in the UI | Asks you to confirm first. The API enforces this too: these endpoints return `428` unless `confirm: true` is sent. |
| Any AI turn | A **checkpoint** of the whole working tree (untracked files included) is taken before it starts, as a private git ref, without touching your index or branch. Afterwards the turn shows which files changed, with **Review** (diff) and **Undo** (restore the checkpoint; your current state is checkpointed first, so undo can itself be undone). |

**Sandboxed execution** (`sandbox/server.py`, `POST /exec`). Each command, whether typed in the terminal or run by the AI, works like this:

1. The project is sent to the sandbox as a tarball (without `.git`, `node_modules` and build folders).
2. The command runs on a throwaway copy under `bash`, with no network, a read-only root filesystem, a non-root user, CPU, file-size and process limits, a timeout, and a scrubbed environment.
3. The sandbox returns the files the command created, changed or deleted, and the backend applies them.

There is no persistent shell: the terminal remembers `cd` itself, but nothing else carries over between commands except files. The image includes Python 3.12 with pytest, numpy and pandas, plus Node.js/npm (offline), git and make. Dependencies can't be downloaded at run time.

**Git hardening.** The backend runs git with hooks, fsmonitor, external diff and credential helpers disabled. Paths inside `.git` can never be written through the API or the AI. Clone and push are allowed only to public HTTPS hosts that pass the SSRF check.

**Limits.** Files over 2 MB aren't opened in the editor. Projects over 40 MB can't be sent to the sandbox. Imports are capped at `WORKSPACE_IMPORT_MAX_MB`. Approvals are held in the backend process's memory, so run a single backend replica, or move the approval gate to Redis or Postgres before scaling out.

## Deep research

Open **Deep research** in the sidebar (`/research`). Ask a question and choose a depth:

| Depth | Sub-questions | Sources read (at most) | Single-source claims re-verified |
|---|---|---|---|
| Quick | 3 | 6 | 0 |
| Standard | 5 | 12 | 3 |
| Deep | 7 | 20 | 6 |

Each run is a background task. Its progress streams live: a phase bar, every search with its result count, every page read or failed, the claims extracted from each page, conflicts found and verification results. You can leave and come back, because the event history is stored and replayed. The pipeline lives in `backend/app/research/engine.py`:

1. **Plan.** The question is split into sub-questions, each with search queries.
2. **Search.** Every query is sent to the configured search provider (Tavily or Brave). Failed searches are recorded and shown.
3. **Select.** Candidate pages are ranked by source reliability, search rank and how many queries found them. Every sub-question gets at least one page, and pages per site are capped so the sources stay diverse. Reliability (`reliability.py`) is a transparent domain heuristic (official/academic/reference > reputable secondary > unknown > user-generated), and its reason is shown next to each source.
4. **Read.** Pages (HTML, text or PDF) are fetched through the SSRF guard. Pages that fail, or that have too little readable text (paywalls, logins), are listed as unread and never cited.
5. **Extract.** For each page, the model lists claims, each with a **verbatim quote**. A claim is kept only if its quote actually appears in the downloaded text. Whitespace, case and typographic quotes are normalised, and an elided quote only counts if every fragment appears in order. Claims that fail this check are discarded and counted.
6. **Cross-check.** For each sub-question, claims are grouped by agreement and **conflicts** are identified, with an assessment of which side is better supported. A claim counts as *corroborated* only when at least two **different sites** support it.
7. **Verify.** Important claims that rest on a single source get their own search. Pages from other sites are read and checked for support or contradiction, again requiring a verified quote.
8. **Write.** The report model receives only the verified evidence and must cite `[S#]`.
   - Citations to sources that weren't read are removed, and the statement is marked *[unsupported]*.
   - A sources list written by the model is discarded.
   - The **claim verification table**, **source comparison table**, **sources list** (URLs and access dates) and **"How this research was done"** section are generated from the database. That section gives real counts of searches, failures, rejected quotes and removed citations.

**Never claiming research that didn't happen:**

| Situation | What happens |
|---|---|
| No search provider is configured (`TAVILY_API_KEY` / `BRAVE_API_KEY`) | Deep research refuses to start and says why. |
| Every search fails, no page can be read, no verifiable evidence is found, or the draft cites none of the sources | The run ends as **failed with an explanation and no report**. |
| A run completes | Each source in the report links to the page that was actually downloaded. Each claim shows its quote and the source it was verified in. |

**Output.** The report contains an executive summary, findings for each sub-question, comparison tables, a section on conflicting information, and a confidence and limitations section. Every `[S#]` links to its source. The **Sources** tab shows reliability, how each page was found, and claims per source. The **Claims** tab shows every claim with its quote, status (corroborated, single source or conflicting) and the conflicts with their assessments. You can export to **Markdown** or **PDF** (fpdf2, with DejaVu fonts for Unicode).

**Limits.**
- Runs execute inside the backend process; at most 2 run at once per user. Unfinished runs are marked failed after a restart.
- Reliability ratings and "corroborated" are signals, not proof, and the report says so.
- Search quality depends on the provider. Pages that need JavaScript to render can't be read.

## Document intelligence

Open **Documents** in the sidebar (`/documents`). Files attached in chat go through the same pipeline. The code is in `backend/app/documents/`.

**Formats** (`extract.py`). The type is detected from the file's content, not its name or MIME type.

| Format | What is extracted |
|---|---|
| PDF | Text per page. Pages with no text layer are **OCR'd** (poppler renders the page, Tesseract reads it; up to 40 pages). Encrypted or damaged files get a clear error. |
| DOCX | Paragraphs in document order, with heading sections and lists, plus tables (as real tables and as text). |
| XLSX | Every sheet as a table with a detected header row. Formula cells are counted, and the file is flagged if it was saved without calculated values. |
| CSV / TSV | Delimiter and encoding are detected; numbers are typed. |
| PPTX | One block per slide: title, bullets with indentation, tables and speaker notes. |
| TXT / Markdown / code | Markdown is split by heading. |
| Images (PNG, JPEG, GIF, WebP, TIFF, BMP) | **OCR** with Tesseract. If a vision model is configured, an AI description of the image (charts, diagrams, layout) is also indexed. |

Legacy `.doc`, `.xls` and `.ppt` files and ZIP archives are rejected with a message saying what to do instead.

**Indexing and RAG** (`chunking.py`, `retrieval.py`):
- Text is chunked within its location, so every passage remembers its page, slide, sheet rows or heading.
- Chunks are embedded into pgvector when an embedding provider is configured. Chunks indexed while it was unavailable are backfilled by the maintenance task.
- Retrieval is hybrid: vector similarity plus Postgres full-text search, merged by reciprocal-rank fusion.
- Answers must cite passages as `[P#]`. A citation to a passage that wasn't retrieved is replaced with **[unsupported]**. Each citation shows the file, location and snippet.
- If nothing relevant is found, the answer says so instead of guessing.

**Operations.** Each is available in the UI, through the API (`/api/documents/...`), and as a chat tool.

| Feature | How |
|---|---|
| Summarisation | Brief, bullet or detailed. Long documents are summarised in parts, then the parts are merged. A brief summary is made automatically after upload. |
| Question answering | Over selected documents, or everything you can access, with cited passages. |
| Multi-document comparison | 2–6 documents. Passages are retrieved from each one, and the result is a cited comparison table plus differences, similarities and gaps. |
| Data extraction | Presets (invoice, contract, résumé, research paper) or custom fields. Each value comes with its source text, which is checked against the document. Values whose source text isn't found are marked **unverified**, and missing values are **not found** (never invented). Export as JSON or CSV. |
| Table and spreadsheet analysis | Each table gets a column profile (type, empty count, distinct values, min/max/mean/sum). Questions are answered by model-written **pandas code run over all rows in the sandbox**, with the code and raw output shown. Tables can be downloaded as CSV. |
| Document generation | DOCX, XLSX (typed numbers, frozen header row), PPTX (bullet and table slides, speaker notes), PDF or Markdown, optionally grounded in selected documents. The sources used are recorded on the new file, which is indexed like any upload. |
| Chat tools | `search_documents`, `list_documents`, `read_document`, `summarize_document`, `compare_documents`, `extract_data`, `analyze_table`, `generate_document`. |

**Privacy and access control** (`access.py`):
- **Private by default.** Only the uploader (the owner) can see a document. Owners can share read-only with specific Aria users by email.
- **What viewers can do.** Viewers can read, download, search, ask about, summarise, compare, extract from and analyse a shared document. They can't rename, reindex, reshare, see who else has access, or delete it; deleting only removes it from their own library.
- **One access check for every path.** API routes, RAG retrieval (in SQL), chat attachments (filtered when the message is sent and again when the prompt is built), the conversation view and all chat tools use the same check. A document you can't read returns **404**, so ids can't be probed.
- **Storage.** Files are stored under the owner's directory with random names. Downloads are sent as attachments with `nosniff` and a sandbox CSP; only images and PDFs display inline, so uploaded HTML or SVG can never run on Aria's origin.
- **What leaves the server.** OCR runs locally. Document text goes to your configured model or embedding provider only when you use a feature that needs it (summaries, questions, comparison, extraction, analysis, generation, embeddings).

## Multi-agent orchestration

Open **Agent team** in the sidebar (`/team`), describe a goal, and a team of specialist agents works on it. The code is in `backend/app/team/`.

| Agent | Tools | Role |
|---|---|---|
| Planning | (model only) | Breaks the goal into 1–8 tasks, assigns each to an agent and declares dependencies. |
| Research | `web_search`, `fetch_url`, `search_documents`, `list_documents` | Finds and reads sources and returns findings attributed to them. |
| Coding | `run_python` | Writes code, runs it in the sandbox, debugs it. |
| Data Analyst | `analyze_table`, `read_document`, `search_documents`, `list_documents`, `run_python` | Computes figures with pandas over the user's spreadsheets or the data it is given. |
| Writing | `generate_document`, `search_documents`, `read_document` | Writes the prose deliverable and can create DOCX/PDF/XLSX/PPTX/MD files. |
| Browser | `open_page`, `read_page`, `find_in_page`, `follow_link` | Navigates specific sites like a person: reads pages, searches in them, follows links. Every hop is SSRF-guarded. |
| Verification | `web_search`, `fetch_url`, `run_python`, `search_documents`, `read_document` | Settles conflicts between agents and checks the final answer independently. |

**How a run works** (`orchestrator.py`):

1. **Plan.** The Planning Agent sees the roster, including which tools aren't configured on this server, and returns a task graph. It is validated: unknown agents and dependencies are dropped, cycles are broken, and every adjustment is shown to you.
2. **Delegate.** Each task starts as soon as its dependencies finish, and receives their results as input.
   - **Parallel when safe:** tasks for agents without side effects run concurrently, up to 3 at a time. An agent that creates files (Writing) runs alone.
   - **Retries:** a failed task is retried once with the error. If it still fails, tasks that depend on it are told what is missing.
3. **Each specialist** runs its own tool loop and must finish by calling `submit_result` (status, result, sources, confidence, notes), with code-level checks:
   - **No evidence, no "done":** Research, Data Analyst and Browser can't report done without at least one successful tool call. They are told once; after that the result is downgraded to *partial*.
   - **Sources must be real:** a listed source that doesn't appear in the agent's own tool results is removed, and the removal is shown.
4. **Reconcile.** The results are compared for contradictions. Factual or numeric conflicts go to the **Verification Agent**, which checks them with its tools; up to 3 are checked in parallel. A conflict counts as *resolved* only if the agent actually used a tool; otherwise both positions are reported as *unresolved*.
5. **Synthesize.** The orchestrator combines the results using only what the agents produced. It applies the conflict resolutions and discloses failed or partial tasks.
6. **Verify.** The Verification Agent checks the final answer against the agents' results and re-checks key facts and numbers with tools, then calls `submit_verdict` (`ok`, `issues`, `checked`). If it finds issues, the answer is revised once and checked again. The run ends **completed** or **completed with issues**, and the remaining issues are shown.

**What you see.** A roster card for each agent shows whether it's idle or working, and on which task. Tasks are laid out in dependency columns (tasks in a column can run in parallel); each card shows live tool activity, the result, sources, confidence, notes and attempts. There's also a conflicts panel with the resolutions, the verification verdict with what was checked, and the final answer, which you can export as Markdown. Progress events are stored, so you can reload or come back later.

**Limits.**
- **No external actions.** Agents act only through the tools above. Actions with outside effects (email, Slack, HTTP writes) belong to the **Autonomous agent**, which asks for your approval.
- **Single process.** Runs execute inside the backend process (2 at a time per user), and unfinished runs are marked failed after a restart.
- **Conflict checks are capped.** At most 3 conflicts are checked with tools per run; any others are reported as unresolved.

## Run without Docker (Windows)

1. Download the project as a ZIP: https://github.com/siv151992-lang/MY-AI/archive/refs/heads/main.zip
2. Extract it.
3. Double-click **`aria-native.bat`**.

No admin rights are needed. On the first run (15–30 minutes) it downloads private, portable copies of Python (via `uv`), Node.js 22, PostgreSQL 16 and MinGit into a `runtime` folder next to Aria. It then:
- creates a local database on port 5433 (listening on 127.0.0.1 only) in `data\pgdata`, with a random password stored in `.env`;
- installs and builds Aria;
- starts three minimized windows (**Aria database**, **Aria backend**, **Aria web**);
- opens http://127.0.0.1:3000 and adds an **Aria (no Docker)** Desktop shortcut.

It installs nothing system-wide, except the Microsoft Visual C++ runtime if your PC doesn't have it yet (Windows asks for permission once).

| | Docker | Without Docker |
|---|---|---|
| Chat, memory, documents, Q&A, comparison, extraction, generation, deep research, agent team | ✓ | ✓ |
| Semantic (pgvector) search | ✓ | keyword (full-text) search instead |
| Code execution: `run_python`, workspace terminal and tests, table analysis | ✓ isolated sandbox | **off**, because it can't be isolated safely on Windows without Docker |
| OCR | ✓ | only if Tesseract is installed (`winget install UB-Mannheim.TesseractOCR`) |

Commands, run from the project folder:
- `aria-native.bat stop` stops Aria and its database.
- `aria-native.bat logs` shows the logs.
- `aria-native.bat update` downloads the latest version and keeps `.env` and `data`.

Don't run it as administrator: PostgreSQL refuses to start with admin rights.

The backend runs in this mode on any OS. It falls back automatically when pgvector is missing (`DISABLE_PGVECTOR=true` forces the fallback) and disables code execution with `SANDBOX_URL=off`. It also creates the whole schema itself (`backend/app/schema.py`), so it only needs an empty PostgreSQL database.

## Quick start (Docker)

You need Docker Desktop (Windows or macOS) or Docker Engine (Linux).

**Windows, one click:** download `aria.bat` and double-click it from anywhere. It is the only file you need. If it isn't inside the project folder it downloads Aria into `%USERPROFILE%\Aria`. With Docker Desktop installed it uses Docker, starting Docker Desktop if needed; without Docker it hands over to `aria-native.bat` and runs Aria directly on Windows (see *Run without Docker*), and remembers that choice. `aria.bat native` and `aria.bat docker` switch between the two. It creates `.env` with random secrets, starts everything, opens the browser and adds an **Aria** shortcut to your Desktop. `aria.bat stop`, `logs`, `status` and `update` (downloads the latest code, keeping your `.env`) cover the rest.

```bash
cp .env.example .env
# edit .env: set JWT_SECRET and SANDBOX_TOKEN to long random strings,
#            (model keys are optional here: you can paste one in the app instead, see below)
docker compose up --build
```

**Connecting a model from the chat screen.** When no model is connected, the chat screen shows a *Connect an AI model* box. Paste an Anthropic key (`sk-ant-…`), an OpenAI key (`sk-…`) or type `ollama`. Aria checks the key with the provider using a free, read-only request, saves it in its own database (table `app_settings`) and starts using it right away; the default models switch to match. A second step offers an optional Tavily key (`tvly-…`) for web search and Deep research. Only the first account created on the installation can change keys, keys are never shown again in full, and settings saved in the app take precedence over `.env`.

Then open **http://localhost:3000**, create an account and start chatting.

> **Port 3000 already in use?** Set `ARIA_PORT=3100` (or any free port) in `.env` and open `http://localhost:3100` instead.

> **Windows:** run the commands in PowerShell from the project folder. To generate a secret, run `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

### Configuration

| What | Where |
|---|---|
| API keys, secrets, feature switches | `.env` (see `.env.example`) |
| Models shown in the selector | `backend/models.json`: add or edit entries (`provider` is `anthropic`, `openai` or `ollama`). Only models whose provider has a key appear. |
| Default and background models | `DEFAULT_MODEL_ID`, `UTILITY_MODEL_ID`. The utility model writes titles, plans, verification checks and memory extraction, so a cheap fast model works well here. |
| Semantic search | `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`. Without these, memory and document search use Postgres full-text search. **Set `EMBEDDING_DIM` before the first start**, because it is fixed into the schema. |
| Web research | `TAVILY_API_KEY` or `BRAVE_API_KEY` enables `web_search` and Deep research (which refuses to run without one). `fetch_url` always works. |

Model IDs change over time. If a provider rejects a model name, update `backend/models.json`.

## Features

- **Chat:** token streaming over SSE, a stop button (partial answers are kept), regenerate, copy, rename/delete chats, history grouped by date, a scroll-to-bottom button, drag-drop/paste uploads.
- **Agent mode** (the ✨ toggle): every answer gets a visible plan, tool calls and a self-check. Without the toggle, Aria still plans automatically for complex requests and verifies any answer that used tools.
- **Documents:** PDF, DOCX, XLSX, CSV, PPTX, text/code and images (with OCR) are extracted, chunked with page/slide/sheet locations and indexed for RAG. Small files go straight into context; large ones are read through the document tools. See [Document intelligence](#document-intelligence).
- **Images:** sent to vision-capable models. Aria tells you if the selected model can't see images.
- **Memory:** Aria proposes durable facts it learns and you approve them inline or under *Account → Memory*, where you can also search, correct, pin, archive, export, import and delete. See [Memory architecture](#memory-architecture).
- **Long conversations:** older turns are condensed automatically into a rolling summary plus searchable sections, so details from far back can still be recalled.
- **Python execution:** in an isolated container, for calculations, data analysis and testing code before showing it.
- **Light, dark and system themes;** responsive layout with a slide-over sidebar on mobile.

## Security

- API keys live only on the server (`.env`, or the `app_settings` table when pasted in the app; only the first account may set them), and the browser talks to the backend through a same-origin proxy. Once saved, a key is never sent back to the browser in full.
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
cd backend && pytest            # 159 tests (incl. 10 autonomous-run scenarios: approval granted/denied,
                                # grounding guard, retry, re-plan, remediation, cancel, report): stream parsers (incl. thinking blocks), agent loop, reasoning
                                # engine (analyzer, clarifier, planner, verifier, state, scratchpad privacy),
                                # memory (privacy rules, scoring, rank fusion, extraction, compaction),
                                # coding workspace (path safety, edits, git, checkpoints, sandbox commands,
                                # approvals, code search), deep research (quote verification, citation
                                # validation, conflicts, source selection, report, PDF), documents (every
                                # format, OCR, chunk locations, profiling, generation, citations, access
                                # control between users), agent team (plan validation, parallel scheduling,
                                # side-effect isolation, grounding, conflicts, verification), tools, SSRF guard,
                                # chunking, PDF extraction, sandbox
# Integration tests against real PostgreSQL + pgvector (load db/init/01-schema.sh into an
# empty database first; skipped unless the URL is set):
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/aria_test pytest tests/test_memory_pg.py tests/test_workspace_pg.py tests/test_research_pg.py tests/test_documents_pg.py tests/test_team_pg.py tests/test_native_mode_pg.py
cd frontend && npm test         # SSE and diff parsers, citation linking, team waves/agent state
```

The orchestrator tests use a scripted provider *in the test suite only*, so plan → tool → verify → revise can be checked deterministically. The application itself always calls real APIs.

## Going to production

- Put it behind HTTPS (Caddy, nginx or Traefik), set `COOKIE_SECURE=true`, and set `CORS_ORIGINS` to your domain.
- Scale the backend horizontally: it is stateless apart from the in-memory rate limiter, which you can move to Redis for multiple replicas. Store uploads on shared storage (S3 or a shared volume).
- Small schema changes are applied automatically at startup (`db.MIGRATIONS`, idempotent). Use managed Postgres with pgvector, and add a schema migration tool (for example Alembic or sqitch) before changing the schema.
- For stronger sandbox isolation, run it under gVisor (`runtime: runsc`) or Firecracker.
