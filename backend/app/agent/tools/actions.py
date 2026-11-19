"""Action tools: real side effects in the outside world (or in the user's account).

Every tool that changes something outside Aria is marked `requires_approval`, so the autonomous agent must get
the user's explicit permission first. Tools are only registered when configured — the agent cannot "pretend"
to send an email if no SMTP server exists; the tool simply isn't available and the planner knows it.
"""
from __future__ import annotations

import asyncio
import json
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from pathlib import Path

from .base import Tool, ToolContext
from .web import is_public_url

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def make_http_tool() -> Tool:
    import httpx

    async def http_request(args: dict, ctx: ToolContext) -> str:
        method = str(args.get("method", "GET")).upper()
        url = str(args["url"])
        if not await asyncio.to_thread(is_public_url, url):
            return "Refused: only public http(s) URLs are allowed."
        headers = {str(k): str(v) for k, v in (args.get("headers") or {}).items()}
        body = args.get("json")
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as c:
            r = await c.request(method, url, headers=headers, json=body if body is not None else None,
                                content=None if body is not None else args.get("body"))
        text = r.text[:8000]
        return f"HTTP {r.status_code} {r.reason_phrase}\ncontent-type: {r.headers.get('content-type', '')}\n\n{text}"

    return Tool(
        name="http_request",
        description="Call an external HTTP API (GET/POST/PUT/PATCH/DELETE) on a public URL and return the status "
                    "and body. Non-GET requests change external systems and require the user's approval.",
        parameters={"type": "object", "properties": {
            "method": {"type": "string", "enum": ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]},
            "url": {"type": "string"},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
            "json": {"description": "JSON body"},
            "body": {"type": "string", "description": "Raw body (if not JSON)"}}, "required": ["url"]},
        run=http_request, timeout=40,
        requires_approval=lambda a: str(a.get("method", "GET")).upper() not in SAFE_METHODS,
        describe=lambda a: f"{str(a.get('method', 'GET')).upper()} {a.get('url')}"
                           + (f" with body {json.dumps(a.get('json'))[:200]}" if a.get("json") is not None else ""))


def make_email_tool(host: str, port: int, user: str | None, password: str | None, sender: str) -> Tool:
    def _send(to: list[str], subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), subject
        msg.set_content(body)
        ctx_ssl = ssl.create_default_context()
        if port == 465:
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx_ssl)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
        with server as s:
            if port != 465:
                s.ehlo()
                if s.has_extn("starttls"):
                    s.starttls(context=ctx_ssl)
            if user:
                s.login(user, password or "")
            s.send_message(msg)

    async def send_email(args: dict, ctx: ToolContext) -> str:
        to = args["to"] if isinstance(args["to"], list) else [args["to"]]
        to = [str(t).strip() for t in to if "@" in str(t)][:20]
        if not to:
            return "No valid recipient address."
        await asyncio.to_thread(_send, to, str(args["subject"])[:300], str(args["body"]))
        return f"Email sent to {', '.join(to)} with subject {args['subject']!r}."

    return Tool(
        name="send_email",
        description="Send a plain-text email via the configured SMTP server. Always requires the user's approval.",
        parameters={"type": "object", "properties": {
            "to": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"},
            "body": {"type": "string"}}, "required": ["to", "subject", "body"]},
        run=send_email, timeout=45, requires_approval=True,
        describe=lambda a: f"Email to {', '.join(a['to'] if isinstance(a['to'], list) else [a['to']])}: "
                           f"“{a.get('subject', '')}”\n\n{str(a.get('body', ''))[:600]}")


def make_slack_tool(webhook_url: str) -> Tool:
    import httpx

    async def post_slack(args: dict, ctx: ToolContext) -> str:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(webhook_url, json={"text": str(args["text"])[:3500]})
        return f"Slack responded {r.status_code}: {r.text[:200]}"

    return Tool(
        name="post_slack_message",
        description="Post a message to the team's configured Slack channel (incoming webhook). Requires approval.",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        run=post_slack, timeout=25, requires_approval=True,
        describe=lambda a: f"Post to Slack:\n\n{str(a.get('text', ''))[:600]}")


def make_create_file_tool(upload_dir: Path, save_record) -> Tool:
    """Creates a real, downloadable file in the user's Aria file library (internal, no approval needed).

    `save_record(user_id, file_id, filename, mime, size, path, text)` persists the DB row.
    """
    MIME = {".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv", ".json": "application/json",
            ".html": "text/html", ".py": "text/x-python", ".sql": "text/plain", ".yaml": "text/plain"}

    async def create_file(args: dict, ctx: ToolContext) -> str:
        name = Path(str(args["filename"])).name[:120] or "output.txt"
        ext = name[name.rfind("."):].lower() if "." in name else ".txt"
        if ext not in MIME:
            return f"Unsupported extension {ext}. Use one of: {', '.join(MIME)}"
        content = str(args["content"])
        if len(content) > 2_000_000:
            return "Content too large (max 2 MB)."
        file_id = str(uuid.uuid4())
        d = upload_dir / ctx.user_id
        d.mkdir(parents=True, exist_ok=True)
        path = d / file_id
        data = content.encode()
        await asyncio.to_thread(path.write_bytes, data)
        await save_record(ctx.user_id, file_id, name, MIME[ext], len(data), str(path), content)
        return f"Created file {name} ({len(data)} bytes). file_id={file_id} download=/api/files/{file_id}/content"

    return Tool(
        name="create_file",
        description="Create a text file (report, CSV, JSON, code…) in the user's Aria file library; returns a "
                    "download link. Use for deliverables.",
        parameters={"type": "object", "properties": {
            "filename": {"type": "string"}, "content": {"type": "string"}}, "required": ["filename", "content"]},
        run=create_file, timeout=30)
