"""User-level access control for documents. Every document read goes through these functions.

Roles:
  owner   uploaded or generated the document: everything (read, analyse, share, rename, delete)
  viewer  the owner shared it with them: read, search, ask, summarise, compare, extract, analyse, download.
          Viewers can't delete, rename, reshare or see who else it is shared with.
Anyone else: the document does not exist for them (404, never 403, so ids can't be probed).
"""
from __future__ import annotations

from ..db import pool, rows

READ = "read"
MANAGE = "manage"


class NotFound(LookupError):
    pass


class Forbidden(PermissionError):
    pass


async def role(user_id: str, file_id: str) -> str | None:
    try:
        r = await pool().fetchrow(
            "SELECT CASE WHEN f.user_id=$1 THEN 'owner' ELSE a.role END AS role FROM files f "
            "LEFT JOIN document_access a ON a.file_id=f.id AND a.user_id=$1 WHERE f.id=$2", user_id, file_id)
    except Exception:  # malformed UUID
        return None
    return r["role"] if r else None


async def require(user_id: str, file_id: str, action: str = READ) -> str:
    r = await role(user_id, file_id)
    if r is None:
        raise NotFound(file_id)
    if action == MANAGE and r != "owner":
        raise Forbidden("Only the owner of this document can do that.")
    return r


async def accessible_ids(user_id: str, include_shared: bool = True) -> list[str]:
    sql = "SELECT id FROM files WHERE user_id=$1"
    if include_shared:
        sql += " UNION SELECT file_id FROM document_access WHERE user_id=$1"
    return [str(r["id"]) for r in await pool().fetch(sql, user_id)]


async def filter_readable(user_id: str, file_ids: list[str]) -> list[str]:
    """Keep only ids the user may read (silently drops others: no existence oracle)."""
    if not file_ids:
        return []
    try:
        rs = await pool().fetch(
            "SELECT f.id FROM files f LEFT JOIN document_access a ON a.file_id=f.id AND a.user_id=$1 "
            "WHERE f.id = ANY($2::uuid[]) AND (f.user_id=$1 OR a.user_id IS NOT NULL)", user_id, file_ids)
    except Exception:
        return []
    keep = {str(r["id"]) for r in rs}
    return [i for i in file_ids if i in keep]


async def share(owner_id: str, file_id: str, email: str) -> dict:
    await require(owner_id, file_id, MANAGE)
    target = await pool().fetchrow("SELECT id, email, name FROM users WHERE lower(email)=lower($1)", email.strip())
    if not target:
        raise ValueError("No Aria user with that email address.")
    if str(target["id"]) == owner_id:
        raise ValueError("You already own this document.")
    await pool().execute(
        "INSERT INTO document_access(file_id, user_id, role, granted_by) VALUES ($1,$2,'viewer',$3) "
        "ON CONFLICT (file_id, user_id) DO NOTHING", file_id, target["id"], owner_id)
    return {"user_id": str(target["id"]), "email": target["email"], "name": target["name"], "role": "viewer"}


async def unshare(owner_id: str, file_id: str, user_id: str) -> bool:
    await require(owner_id, file_id, MANAGE)
    res = await pool().execute("DELETE FROM document_access WHERE file_id=$1 AND user_id=$2", file_id, user_id)
    return res.endswith(" 1")


async def grants(owner_id: str, file_id: str) -> list[dict]:
    await require(owner_id, file_id, MANAGE)
    return rows(await pool().fetch(
        "SELECT u.id AS user_id, u.email, u.name, a.role, a.created_at FROM document_access a JOIN users u ON u.id=a.user_id "
        "WHERE a.file_id=$1 ORDER BY a.created_at", file_id))


async def leave(user_id: str, file_id: str) -> bool:
    """A viewer removes a document someone shared with them from their library."""
    res = await pool().execute("DELETE FROM document_access WHERE file_id=$1 AND user_id=$2", file_id, user_id)
    return res.endswith(" 1")


