from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field

from .. import db
from ..config import get_settings
from ..security import clear_session, current_user, hash_password, issue_session, limiter, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=80)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


@router.post("/signup")
async def signup(body: SignupIn, request: Request, response: Response):
    if not get_settings().allow_signup:
        raise HTTPException(403, "Sign-up is disabled on this server.")
    limiter.check(f"signup:{request.client.host if request.client else '?'}", 5)
    try:
        user = await db.create_user(body.email.lower(), body.name.strip(), hash_password(body.password))
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "An account with this email already exists.")
    issue_session(response, user["id"])
    return user


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response):
    limiter.check(f"login:{request.client.host if request.client else '?'}", 10)
    user = await db.get_user_by_email(body.email.lower())
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Incorrect email or password.")
    issue_session(response, user["id"])
    return {k: user[k] for k in ("id", "email", "name", "created_at")}


@router.post("/logout")
async def logout(response: Response):
    clear_session(response)
    return {"ok": True}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return user
