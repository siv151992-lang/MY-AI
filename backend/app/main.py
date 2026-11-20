from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db
from .config import get_settings
from .llm import registry
from .routers import agent, auth, conversations, documents, files, hub, memory, research, setup, team, workspaces
from .security import csrf_guard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("aria")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.upload_dir.mkdir(parents=True, exist_ok=True)
    s.workspace_root.mkdir(parents=True, exist_ok=True)
    await db.connect(s.database_url)
    await db.migrate(s.embedding_dim, s.disable_pgvector)
    from .setup import load as load_app_settings
    await load_app_settings()
    from .local_ai import discover
    await discover(force=True)  # free local AI (Ollama) is connected automatically when it is running
    if not db.VECTOR_ENABLED:
        log.warning("pgvector is not installed: semantic search is off, using full-text search only.")
    if not s.sandbox_url:
        log.warning("No sandbox configured: code execution (run_python, workspace commands, table analysis) is off.")
    from .autonomy.manager import get_manager
    await get_manager().recover_after_restart()
    from .research.manager import get_manager as research_manager
    await research_manager().recover_after_restart()
    from .team.manager import get_manager as team_manager
    await team_manager().recover_after_restart()
    models = registry.available_models()
    from .memory.service import maintenance_loop
    maintenance = asyncio.create_task(maintenance_loop())
    from .hub.automations import get_runner
    scheduler = asyncio.create_task(get_runner().loop())  # scheduled automations
    log.info("Providers: %s | models: %s", list(registry.providers()), [m.id for m in models])
    if not models:
        log.warning("No LLM provider configured yet: open Aria and paste an API key on the chat screen.")
    yield
    maintenance.cancel()
    scheduler.cancel()
    await db.close()


app = FastAPI(title="Aria API", version="1.0.0", lifespan=lifespan, dependencies=[Depends(csrf_guard)])
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origin_list, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

for r in (auth.router, conversations.router, files.router, memory.router, memory.conversation_router, agent.router,
          workspaces.router, research.router, documents.router, team.router, setup.router, hub.router):
    app.include_router(r)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:])
    return JSONResponse(status_code=422, content={"detail": f"{field}: {first.get('msg', 'invalid input')}".strip(": ")})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/api/health")
async def health():
    await db.pool().fetchval("SELECT 1")
    return {"ok": True, "models": [m.id for m in registry.available_models()]}
