"""API integrations: connect any HTTP/JSON API once (base URL + credential) and let chats, skills, custom agents and
automations call it as a tool.

Security:
- Credentials are encrypted at rest (AES-256-GCM, key derived from JWT_SECRET) and never returned to the browser.
- Only public http(s) hosts are allowed (checked on save and again on every call, so DNS changes can't point a
  connector at the internal network); redirects are not followed; paths can't escape the base URL.
- Read methods (GET/HEAD) are always allowed. Write methods (POST/PUT/PATCH/DELETE) are only available in
  automations, and only for integrations where the user switched "allow writes" on."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from urllib.parse import urlencode, urlparse

from .. import db
from ..agent.tools.base import Tool, ToolContext

AUTH_TYPES = ("none", "bearer", "header", "basic", "query")
READ_METHODS = ("GET", "HEAD")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


class IntegrationError(ValueError):
    pass


# ------------------------------------------------------------------ encryption
def _key() -> bytes:
    from ..config import get_settings
    return hashlib.sha256(b"aria-integrations-v1:" + get_settings().jwt_secret.encode()).digest()


def encrypt(plain: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, plain.encode(), b"aria")
    return "v1:" + base64.b64encode(nonce + ct).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    raw = base64.b64decode(token.split(":", 1)[1])
    return AESGCM(_key()).decrypt(raw[:12], raw[12:], b"aria").decode()


# ------------------------------------------------------------------ network safety (patched in tests)
def _is_public(url: str) -> bool:
    from ..agent.tools.web import is_public_url
    return is_public_url(url)


def _client():
    import httpx
    return httpx.AsyncClient(timeout=30, follow_redirects=False)


# ------------------------------------------------------------------ store
def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30]
    return s or "api"


def _public(r) -> dict:
    d = db.row(r)
    d["has_secret"] = bool(d.pop("secret_enc", None))
    if isinstance(d.get("last_test"), str):
        d["last_test"] = json.loads(d["last_test"])
    d["tool_name"] = f"api_{d['slug']}"
    return d


async def validate(data: dict, *, existing: dict | None = None) -> dict:
    name = str(data.get("name", "")).strip()
    if not name or len(name) > 60:
        raise IntegrationError("Name must be 1-60 characters")
    base = str(data.get("base_url", "")).strip().rstrip("/")
    u = urlparse(base)
    if u.scheme not in ("https", "http") or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise IntegrationError("Base URL must look like https://api.example.com/v1 (no credentials, query or fragment)")
    if not await asyncio.to_thread(_is_public, base):
        raise IntegrationError("Base URL must be a public internet address (private, local and internal hosts are blocked)")
    auth_type = data.get("auth_type") or "none"
    if auth_type not in AUTH_TYPES:
        raise IntegrationError(f"auth_type must be one of {', '.join(AUTH_TYPES)}")
    auth_name = str(data.get("auth_name") or "").strip()
    if auth_type in ("header", "query") and not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", auth_name):
        raise IntegrationError("Give the header or query parameter name for the credential (letters, digits, - and _)")
    secret = data.get("secret")
    if auth_type != "none" and not secret and not (existing and existing.get("has_secret")):
        raise IntegrationError("This authentication type needs a credential")
    if auth_type == "basic" and secret and ":" not in secret:
        raise IntegrationError("For basic auth, enter the credential as username:password")
    test_path = str(data.get("test_path") or "").strip()
    if test_path and not test_path.startswith("/"):
        test_path = "/" + test_path
    return {"name": name, "description": str(data.get("description", "")).strip()[:500], "base_url": base,
            "auth_type": auth_type, "auth_name": auth_name, "secret": secret, "allow_writes": bool(data.get("allow_writes")),
            "test_path": test_path[:300]}


async def list_integrations(user_id: str) -> list[dict]:
    rows = await db.pool().fetch("SELECT * FROM integrations WHERE user_id=$1 ORDER BY name", user_id)
    return [_public(r) for r in rows]


async def _get_raw(user_id: str, integration_id: str):
    try:
        return await db.pool().fetchrow("SELECT * FROM integrations WHERE id=$1 AND user_id=$2", integration_id, user_id)
    except Exception:
        return None


async def get_integration(user_id: str, integration_id: str) -> dict | None:
    r = await _get_raw(user_id, integration_id)
    return _public(r) if r else None


async def create_integration(user_id: str, data: dict) -> dict:
    d = await validate(data)
    base_slug = slugify(data.get("slug") or d["name"])
    slug, n = base_slug, 2
    while await db.pool().fetchval("SELECT 1 FROM integrations WHERE user_id=$1 AND slug=$2", user_id, slug):
        slug, n = f"{base_slug}_{n}", n + 1
    r = await db.pool().fetchrow(
        "INSERT INTO integrations (user_id, name, slug, description, base_url, auth_type, auth_name, secret_enc, "
        "allow_writes, test_path) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *", user_id, d["name"], slug,
        d["description"], d["base_url"], d["auth_type"], d["auth_name"],
        encrypt(d["secret"]) if d["secret"] and d["auth_type"] != "none" else None, d["allow_writes"], d["test_path"])
    return _public(r)


async def update_integration(user_id: str, integration_id: str, data: dict) -> dict | None:
    cur = await get_integration(user_id, integration_id)
    if not cur:
        return None
    d = await validate(data, existing=cur)
    secret_sql = "secret_enc"
    args = [integration_id, user_id, d["name"], d["description"], d["base_url"], d["auth_type"], d["auth_name"],
            d["allow_writes"], d["test_path"]]
    if d["auth_type"] == "none":
        secret_sql = "NULL"
    elif d["secret"]:
        secret_sql = "$10"
        args.append(encrypt(d["secret"]))
    r = await db.pool().fetchrow(
        f"UPDATE integrations SET name=$3, description=$4, base_url=$5, auth_type=$6, auth_name=$7, allow_writes=$8, "
        f"test_path=$9, secret_enc={secret_sql}, updated_at=now() WHERE id=$1 AND user_id=$2 RETURNING *", *args)
    return _public(r)


async def delete_integration(user_id: str, integration_id: str) -> bool:
    try:
        return (await db.pool().execute("DELETE FROM integrations WHERE id=$1 AND user_id=$2",
                                        integration_id, user_id)).endswith("1")
    except Exception:
        return False


# ------------------------------------------------------------------ calling
def _build(row, method: str, path: str, query: dict | None) -> tuple[str, dict]:
    path = "/" + str(path or "").lstrip("/")
    if ".." in path.split("?")[0].split("/") or "://" in path or "\\" in path or path.startswith("//"):
        raise IntegrationError("Invalid path: it must be a path under the integration's base URL")
    url = row["base_url"].rstrip("/") + path
    headers = {"accept": "application/json, text/plain;q=0.9, */*;q=0.5", "user-agent": "Aria-Integrations/1.0"}
    q = {str(k): str(v) for k, v in (query or {}).items()}
    secret = decrypt(row["secret_enc"]) if row["secret_enc"] else ""
    if row["auth_type"] == "bearer":
        headers["authorization"] = f"Bearer {secret}"
    elif row["auth_type"] == "header":
        headers[row["auth_name"]] = secret
    elif row["auth_type"] == "basic":
        headers["authorization"] = "Basic " + base64.b64encode(secret.encode()).decode()
    elif row["auth_type"] == "query":
        q[row["auth_name"]] = secret
    if q:
        url += ("&" if "?" in url else "?") + urlencode(q)
    if urlparse(url).hostname != urlparse(row["base_url"]).hostname:
        raise IntegrationError("Invalid path: host changed")
    return url, headers


def _redact(text: str, row) -> str:
    secret = decrypt(row["secret_enc"]) if row["secret_enc"] else ""
    return text.replace(secret, "[credential]") if secret and len(secret) >= 4 else text


async def call(row, method: str, path: str, *, query: dict | None = None, body=None, allow_writes: bool = False) -> dict:
    method = method.upper()
    if method not in READ_METHODS + WRITE_METHODS:
        raise IntegrationError(f"Method {method} is not allowed")
    if method in WRITE_METHODS and not (allow_writes and row["allow_writes"]):
        raise IntegrationError(f"{method} is a write request. Write requests are only allowed in automations, for "
                               f"integrations where 'allow writes' is switched on.")
    url, headers = _build(row, method, path, query)
    if not await asyncio.to_thread(_is_public, url):
        raise IntegrationError("Refused: the host no longer resolves to a public address")
    async with _client() as c:
        r = await c.request(method, url, headers=headers, json=body if body is not None and method in WRITE_METHODS else None)
    text = r.text[:8000]
    return {"status": r.status_code, "ok": r.is_success, "content_type": r.headers.get("content-type", ""),
            "body": _redact(text, row), "truncated": len(r.text) > 8000}


async def test(user_id: str, integration_id: str) -> dict:
    from datetime import datetime, timezone
    row = await _get_raw(user_id, integration_id)
    if not row:
        raise IntegrationError("Integration not found")
    try:
        res = await call(row, "GET", row["test_path"] or "/")
        result = {"ok": res["ok"], "status": res["status"], "preview": res["body"][:300]}
    except IntegrationError as e:
        result = {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - network errors
        result = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    result["at"] = datetime.now(timezone.utc).isoformat()
    await db.pool().execute("UPDATE integrations SET last_test=$2::jsonb WHERE id=$1", integration_id, json.dumps(result))
    return result


def make_tool(row, *, allow_writes: bool) -> Tool:
    writes = allow_writes and row["allow_writes"]
    methods = list(READ_METHODS + (WRITE_METHODS if writes else ()))

    async def run(args: dict, ctx: ToolContext) -> str:
        try:
            res = await call(row, str(args.get("method", "GET")), str(args.get("path", "/")),
                             query=args.get("query") if isinstance(args.get("query"), dict) else None,
                             body=args.get("json"), allow_writes=writes)
        except IntegrationError as e:
            return f"Refused: {e}"
        return f"HTTP {res['status']} ({res['content_type']})\n\n{res['body']}" + ("\n…(truncated)" if res["truncated"] else "")

    desc = (f"Call the user's '{row['name']}' API ({row['base_url']}). "
            + (row["description"] + " " if row["description"] else "")
            + f"Give a path relative to the base URL. Allowed methods: {', '.join(methods)}.")
    return Tool(name=f"api_{row['slug']}", description=desc[:900], timeout=40, run=run,
                parameters={"type": "object", "properties": {
                    "method": {"type": "string", "enum": methods},
                    "path": {"type": "string", "description": "Path under the base URL, e.g. /users/42"},
                    "query": {"type": "object", "additionalProperties": {"type": "string"}},
                    "json": {"description": "JSON body (write methods only)"}}, "required": ["path"]},
                describe=lambda a: f"{str(a.get('method', 'GET')).upper()} {row['base_url']}{a.get('path', '/')}")


async def tools_for_user(user_id: str, *, allow_writes: bool = False) -> list[Tool]:
    try:
        rows = await db.pool().fetch("SELECT * FROM integrations WHERE user_id=$1", user_id)
    except Exception:  # noqa: BLE001 - no database (unit tests)
        return []
    return [make_tool(r, allow_writes=allow_writes) for r in rows]


def builtin_status() -> list[dict]:
    """Built-in integrations configured by the server owner: what is on, and what each needs."""
    from ..config import get_settings
    s = get_settings()
    return [
        {"name": "Web search", "configured": bool(s.tavily_api_key or s.brave_api_key),
         "needs": "TAVILY_API_KEY or BRAVE_API_KEY (paste a Tavily key on the chat screen)",
         "enables": "web search tool, Deep research, research agents"},
        {"name": "Code execution sandbox", "configured": bool(s.sandbox_url),
         "needs": "the Docker version of Aria (SANDBOX_URL)", "enables": "run_python, workspace commands, data analysis"},
        {"name": "Email (SMTP)", "configured": bool(s.smtp_host and s.smtp_from),
         "needs": "SMTP_HOST, SMTP_FROM (+ SMTP_USER/SMTP_PASSWORD) in .env",
         "enables": "send_email for the autonomous agent and automation notifications"},
        {"name": "Slack", "configured": bool(s.slack_webhook_url), "needs": "SLACK_WEBHOOK_URL in .env",
         "enables": "Slack messages from the autonomous agent and automation notifications"},
        {"name": "Voice transcription", "configured": bool(s.openai_api_key),
         "needs": "an OpenAI key (server-side speech-to-text); without it the browser's own speech recognition is used where supported",
         "enables": "microphone input"},
        {"name": "Semantic search (embeddings)", "configured": bool(s.embedding_provider),
         "needs": "EMBEDDING_PROVIDER (openai or ollama) and pgvector", "enables": "meaning-based memory and document search"},
    ]
