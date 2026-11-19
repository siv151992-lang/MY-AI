"""Connect an AI model from the chat screen: paste a key, Aria checks it and starts using it right away."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import setup
from ..config import get_settings
from ..llm import registry
from ..security import current_user, limiter

router = APIRouter(prefix="/api/setup", tags=["setup"])


class KeyIn(BaseModel):
    key: str = Field(min_length=1, max_length=400)


async def _status(user: dict) -> dict:
    s = get_settings()
    saved = await setup.saved_keys()
    providers = []
    for name, (attr, _, _) in setup.PROVIDERS.items():
        value = getattr(s, attr)
        providers.append({"name": name, "connected": bool(value), "hint": setup.mask(value),
                          "source": "app" if attr in saved else ("env" if value else None)})
    try:
        default = registry.get_model(None).id
    except RuntimeError:
        default = None
    return {
        "configured": bool(registry.available_models()),
        "default_model": default,
        "providers": providers,
        "web_search": bool(s.tavily_api_key or s.brave_api_key),
        "can_edit": await setup.is_owner(user["id"]),
    }


async def _owner(user: dict = Depends(current_user)) -> dict:
    if not await setup.is_owner(user["id"]):
        raise HTTPException(403, "Only the person who set up Aria (the first account) can change its AI keys.")
    limiter.check(f"setup:{user['id']}", 10)
    return user


@router.get("")
async def status(user: dict = Depends(current_user)):
    return await _status(user)


@router.post("/key")
async def add_key(body: KeyIn, user: dict = Depends(_owner)):
    try:
        provider, value = setup.detect(body.key)
        value = await setup.verify(provider, value)
    except setup.SetupError as e:
        raise HTTPException(422, str(e))
    if provider == "tavily":
        await setup.save({"tavily_api_key": value}, user["id"])
        message = "Web search is on. Aria can now search the web and run Deep research."
    else:
        attr, default_model, utility_model = setup.PROVIDERS[provider]
        values = {attr: value}
        s = get_settings()
        # Switch the default models to the new provider unless the current defaults already work.
        current = {m.id for m in registry.available_models()}
        if s.default_model_id not in current:
            values["default_model_id"] = default_model
        if (s.utility_model_id or s.default_model_id) not in current:
            values["utility_model_id"] = utility_model
        await setup.save(values, user["id"])
        message = {"anthropic": "Connected to Anthropic. Claude models are ready to chat.",
                   "openai": "Connected to OpenAI. GPT models are ready to chat.",
                   "ollama": "Connected to Ollama. Local models are ready to chat."}[provider]
    return {"ok": True, "provider": provider, "message": message, **await _status(user)}


@router.delete("/key/{provider}")
async def remove_key(provider: str, user: dict = Depends(_owner)):
    attr = {"tavily": "tavily_api_key", **{k: v[0] for k, v in setup.PROVIDERS.items()}}.get(provider)
    if not attr:
        raise HTTPException(404, "Unknown provider")
    if attr not in await setup.saved_keys():
        raise HTTPException(409, "This key comes from the .env file, so it can only be removed there.")
    await setup.save({attr: None}, user["id"])
    # The environment value (if any) applies again after a restart; for now just drop it from the running app.
    return {"ok": True, **await _status(user)}
