"""Password hashing, JWT session cookies, CSRF guard and a simple per-user rate limiter."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, status

from . import db
from .config import get_settings

COOKIE = "aria_session"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


def issue_session(response: Response, user_id: str) -> None:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(hours=s.jwt_expire_hours)
    token = jwt.encode({"sub": user_id, "exp": exp}, s.jwt_secret, algorithm="HS256")
    response.set_cookie(COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="lax",
                        max_age=s.jwt_expire_hours * 3600, path="/")


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")


async def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    user = await db.get_user(payload["sub"])
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    from .hub import usage
    usage.set_scope(user["id"], feature_for_path(request.url.path))  # attribute model calls in this request
    return user


_FEATURE_PREFIXES = (("/api/documents", "documents"), ("/api/memor", "memory"), ("/api/research", "research"),
                     ("/api/team", "team"), ("/api/agent/", "agent"), ("/api/workspaces", "workspace"),
                     ("/api/conversations", "chat"), ("/api/skills", "skill"), ("/api/agents", "custom_agent"),
                     ("/api/automations", "automation"), ("/api/router", "router"), ("/api/projects", "chat"))


def feature_for_path(path: str) -> str:
    for prefix, feature in _FEATURE_PREFIXES:
        if path.startswith(prefix):
            return feature
    return "other"


async def csrf_guard(request: Request) -> None:
    """Cookie auth + SameSite=Lax, plus a custom header that cross-site forms cannot send."""
    if request.url.path.startswith("/api/hooks/"):
        return  # webhook triggers are called by other services and authenticated by their secret URL token
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-requested-with") != "aria":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")


class RateLimiter:
    def __init__(self) -> None:
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down a little.")
        q.append(now)


limiter = RateLimiter()


async def chat_rate_limit(user: dict = Depends(current_user)) -> dict:
    limiter.check(f"chat:{user['id']}", get_settings().rate_limit_per_minute)
    return user
