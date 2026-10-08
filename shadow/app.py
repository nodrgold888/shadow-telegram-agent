from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

from .config import Settings
from .development import DevelopmentError, DevelopmentStudio
from .ai_slots import apply_provider, free_slot, parse_provider, provider_env, remove_slot, set_first, slot_env_names
from .accounts import mask_label
from .persist import delete_env_vars, save_env_vars, save_model_selection, save_reply_enabled, save_group_reply_enabled, save_group_reply_mode
from .policy import parse_group_reply_update
from .config import MAX_BACKUP_PROVIDERS, SUPPORTED_OPENAI_MODELS
from .agents import agent_catalog, skill_catalog
from .chat_memory import normalize_chat_profile
from .keepalive import build_keepalive
from .panel_login import PanelLogin, LoginError
from .telegram_agent import TelegramAgent, TelegramSetupTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = Settings.from_env()
agent = TelegramAgent(settings)
keepalive = build_keepalive()
panel_login = PanelLogin()
development = DevelopmentStudio()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await agent.start()
    if keepalive:
        keepalive.start()
    try:
        yield
    finally:
        await development.close()
        if keepalive:
            keepalive.stop()
        await agent.stop()


log = logging.getLogger("shadow.app")
app = FastAPI(title="Shadow", version="0.1.0", lifespan=lifespan)

_DASHBOARD_FILE = Path(__file__).with_name("dashboard.html")
_DASHBOARD_THEME_FILE = Path(__file__).with_name("dashboard-theme.css")
_SETUP_FILE = Path(__file__).with_name("setup.html")


def _secure_cookie(request: Request) -> bool:
    return not (
        request.url.scheme == "http"
        and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
    )


def _dashboard_allowed(cookie: str | None, authorization: str | None = None) -> bool:
    if panel_login.session_valid(cookie):
        return True
    bearer = authorization or ""
    if bearer[:7].lower() == "bearer ":
        bearer = bearer[7:].strip()
    else:
        bearer = ""
    return any(
        supplied and expected and secrets.compare_digest(supplied, expected)
        for supplied in (cookie, bearer)
        for expected in (settings.setup_token, settings.admin_token)
    )


def _dashboard_page() -> HTMLResponse:
    # no-store: a redeploy must show up on the next page load, never a stale cached dashboard
    return HTMLResponse(_DASHBOARD_FILE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return _dashboard_page()


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return _dashboard_page()


@app.get("/dashboard/theme.css")
async def dashboard_theme() -> Response:
    return Response(
        _DASHBOARD_THEME_FILE.read_text(encoding="utf-8"),
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/dashboard/development.js")
async def development_script() -> Response:
    return Response(Path(__file__).with_name("development.js").read_text(encoding="utf-8"),
                    media_type="text/javascript", headers={"Cache-Control": "no-store"})


def development_auth(shadow_setup: str | None = Cookie(default=None),
                     authorization: str | None = Header(default=None)):
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")


class DevelopmentTask(BaseModel):
    objective: str = Field(min_length=10, max_length=3000)
    mode: Literal["audit", "build"] = "audit"


class DevelopmentFeedback(BaseModel):
    decision: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=1000)


@app.exception_handler(DevelopmentError)
async def development_error(_request: Request, exc: DevelopmentError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/dashboard/api/development", dependencies=[Depends(development_auth)])
async def development_status():
    return development.status(agent.settings)


@app.post("/dashboard/api/development", status_code=202, dependencies=[Depends(development_auth)])
async def development_start(body: DevelopmentTask):
    state = agent.status()
    runtime = {key: state.get(key) for key in ("connected", "reply_enabled", "reply_ready", "reply_count")}
    runtime["has_reply_error"] = bool(state.get("last_reply_error"))
    return await development.start(agent.settings, body.objective, body.mode, runtime)


@app.get("/dashboard/api/development/{job_id}", dependencies=[Depends(development_auth)])
async def development_detail(job_id: str):
    return development.public(development.get(job_id), detail=True)


@app.get("/dashboard/api/development/{job_id}/patch", dependencies=[Depends(development_auth)])
async def development_patch(job_id: str):
    job = development.get(job_id)
    if not job.get("patch"):
        raise DevelopmentError("This task has no patch")
    return Response(job["patch"], media_type="text/plain",
                    headers={"Content-Disposition": f'attachment; filename="shadow-{job["id"]}.patch"',
                             "Cache-Control": "no-store"})


@app.post("/dashboard/api/development/{job_id}/cancel", dependencies=[Depends(development_auth)])
async def development_cancel(job_id: str):
    await development.cancel(job_id)
    return {"ok": True}


@app.post("/dashboard/api/development/{job_id}/feedback", dependencies=[Depends(development_auth)])
async def development_feedback(job_id: str, body: DevelopmentFeedback):
    development.feedback(job_id, body.decision, body.note)
    return {"ok": True}


@app.post("/dashboard/api/development/{job_id}/publish", dependencies=[Depends(development_auth)])
async def development_publish(job_id: str):
    return await development.publish(job_id)


@app.delete("/dashboard/api/development/{job_id}", dependencies=[Depends(development_auth)])
async def development_delete(job_id: str):
    development.delete(job_id)
    return {"ok": True}


@app.post("/dashboard/auth")
async def dashboard_auth(request: Request) -> JSONResponse:
    token = str((await request.json()).get("token", "")).strip()
    accepted = any(
        expected and secrets.compare_digest(token, expected)
        for expected in (settings.setup_token, settings.admin_token)
    )
    if not accepted:
        raise HTTPException(status_code=401, detail="Kirish kodi noto‘g‘ri")
    response = JSONResponse({"ok": True})
    response.set_cookie("shadow_setup", token, httponly=True, secure=_secure_cookie(request), samesite="lax", max_age=43200)
    return response


@app.get("/dashboard/auth/info")
async def dashboard_auth_info() -> JSONResponse:
    """List saved Telegram nicknames so the owner can choose where to receive a login code."""
    accounts = agent.account_list()
    return JSONResponse({
        "connected": bool(agent.connected),
        "accounts": [{
            "key": str(index),
            "label": mask_label(str(account["label"])) if str(account["label"]).isdigit() else account["label"],
            "active": bool(agent.connected and account["active"]),
        } for index, account in enumerate(accounts)],
    }, headers={"Cache-Control": "no-store"})


@app.post("/dashboard/auth/telegram/request")
async def dashboard_telegram_request(request: Request) -> dict[str, object]:
    # The browser must choose one of the owner's saved sessions before we send a code.
    body = await request.json()
    account_key = str(body.get("account_key", "")).strip() if isinstance(body, dict) else ""
    accounts = agent.account_list()
    if not account_key.isdigit() or int(account_key) >= len(accounts):
        raise HTTPException(status_code=400, detail="Avval ulangan Telegram akkauntlaridan birini tanlang.")
    account_id = str(accounts[int(account_key)]["id"])
    try:
        code = panel_login.issue_code(account_id=account_id)
    except LoginError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    try:
        await agent.send_login_code(
            account_id,
            f"Shadow panelga kirish kodi: {code}\n5 daqiqa amal qiladi. Buni siz so‘ramagan bo‘lsangiz, hech kimga bermang."
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except TelegramSetupTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from None
    except Exception as exc:
        log.warning("Telegram login code delivery failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Tanlangan akkauntga kod yuborilmadi. Ulanishni tekshiring va qayta urinib ko‘ring.") from None
    return {"ok": True, "expires_in": 300}


@app.post("/dashboard/auth/telegram/verify")
async def dashboard_telegram_verify(request: Request) -> JSONResponse:
    code = str((await request.json()).get("code", ""))
    account_id = panel_login.code_account_id
    try:
        token = panel_login.verify_code(code)
    except LoginError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    if account_id and (not agent.connected or str(agent.account_id) != account_id):
        try:
            await agent.switch_account(account_id)
        except ValueError as exc:
            panel_login.end_session(token)
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except TelegramSetupTimeout as exc:
            panel_login.end_session(token)
            raise HTTPException(status_code=504, detail=str(exc)) from None
        except Exception as exc:
            panel_login.end_session(token)
            log.warning("Switching to sign-in Telegram account failed: %s", type(exc).__name__)
            raise HTTPException(status_code=502, detail="Tanlangan akkauntga ulana olmadik. Qaytadan urinib ko‘ring.") from None
    response = JSONResponse({"ok": True})
    response.set_cookie("shadow_setup", token, httponly=True, secure=_secure_cookie(request), samesite="lax", max_age=43200)
    return response


@app.post("/dashboard/logout")
async def dashboard_logout(shadow_setup: str | None = Cookie(default=None)) -> JSONResponse:
    panel_login.end_session(shadow_setup)
    response = JSONResponse({"ok": True})
    response.delete_cookie("shadow_setup")
    return response


@app.get("/dashboard/api/status")
async def dashboard_status(
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    return {**agent.status(), "keepalive": keepalive.status() if keepalive else {"enabled": False}}


@app.post("/dashboard/api/repair-agent")
async def dashboard_repair_agent(
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    return await agent.run_repair_agent()


@app.post("/dashboard/api/models")
async def dashboard_models(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Model sozlamasi noto‘g‘ri")
    openai_model = body.get("openai_model")
    complex_model = body.get("complex_openai_model")
    if openai_model not in SUPPORTED_OPENAI_MODELS or complex_model not in SUPPORTED_OPENAI_MODELS:
        raise HTTPException(status_code=400, detail="Model tanlovini tekshiring")
    updated_settings = replace(
        agent.settings,
        openai_model=openai_model,
        complex_openai_model=complex_model,
    )
    agent.settings = updated_settings
    if agent.assistant:
        agent.assistant.settings = updated_settings
    persisted = await save_model_selection(openai_model, complex_model)
    return {
        "openai_model": openai_model,
        "complex_openai_model": complex_model,
        "persisted": persisted,
    }


@app.get("/dashboard/api/chats")
async def dashboard_chats(
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    return {"chats": await agent.dialogs()}



@app.get("/dashboard/api/chats/{chat_id}/photo")
async def dashboard_chat_photo(
    chat_id: int,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> Response:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    if not agent.connected or not agent.client:
        raise HTTPException(status_code=404, detail="Rasm topilmadi")
    try:
        entity = await agent.client.get_entity(chat_id)
        photo = await agent.client.download_profile_photo(entity, file=bytes)
    except Exception:
        raise HTTPException(status_code=404, detail="Rasm topilmadi") from None
    if not photo:
        raise HTTPException(status_code=404, detail="Rasm topilmadi")
    return Response(
        content=photo,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@app.get("/dashboard/api/agents")
async def dashboard_agents(
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    return {"agents": agent_catalog(), "skills": skill_catalog()}


@app.get("/dashboard/api/chats/{chat_id}/profile")
async def dashboard_chat_profile(
    chat_id: int,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    try:
        return {"profile": agent.chat_profile_for(chat_id), "agents": agent_catalog()}
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.put("/dashboard/api/chats/{chat_id}/profile")
async def dashboard_update_chat_profile(
    chat_id: int, request: Request, shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Chat xotirasi noto‘g‘ri formatda")
    try:
        profile = normalize_chat_profile(body)
        return await agent.update_chat_profile(chat_id, profile)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/dashboard/api/chats/{chat_id}/profile")
async def dashboard_clear_chat_profile(
    chat_id: int, shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    try:
        return await agent.clear_chat_profile(chat_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/dashboard/api/chats/approval")
async def dashboard_chat_approval(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="So‘rov noto‘g‘ri")
    chat_id, approved = body.get("chat_id"), body.get("approved")
    if type(chat_id) is not int or type(approved) is not bool:
        raise HTTPException(status_code=400, detail="Chat ID va ruxsat holati noto‘g‘ri")
    try:
        return await agent.update_chat_approval(chat_id, approved)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/dashboard/api/chats/friend")
async def dashboard_chat_friend(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="So‘rov noto‘g‘ri")
    chat_id, friend = body.get("chat_id"), body.get("friend")
    category = body.get("category", "dostlar")
    if type(chat_id) is not int or type(friend) is not bool or type(category) is not str:
        raise HTTPException(status_code=400, detail="Chat ID va holat noto‘g‘ri")
    try:
        return await agent.update_chat_friend(chat_id, friend, category)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/dashboard/api/chats/clear-approvals")
async def dashboard_clear_chat_approvals(
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    result = await agent.clear_chat_approvals()
    return result


@app.post("/dashboard/api/replies")
async def dashboard_replies(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    enabled = (await request.json()).get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled qiymati true yoki false bo‘lishi kerak")
    try:
        agent.set_reply_enabled(enabled)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    persisted = await save_reply_enabled(agent.reply_enabled)
    return {"reply_enabled": agent.reply_enabled, "persisted": persisted}


@app.post("/dashboard/api/group-replies")
async def dashboard_group_replies(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    try:
        enabled, mode = parse_group_reply_update(await request.json())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    changes = {}
    if enabled is not None:
        changes["group_reply_enabled"] = enabled
    if mode is not None:
        changes["group_reply_mode"] = mode
    agent.settings = replace(agent.settings, **changes)
    if agent.assistant:
        agent.assistant.settings = agent.settings
    persisted = True
    if enabled is not None:
        persisted = await save_group_reply_enabled(enabled) and persisted
    if mode is not None:
        persisted = await save_group_reply_mode(mode) and persisted
    return {
        "group_reply_enabled": agent.settings.group_reply_enabled,
        "group_reply_mode": agent.settings.group_reply_mode,
        "persisted": persisted,
    }


@app.post("/dashboard/api/greet-unknown")
async def dashboard_greet_unknown(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    enabled = (await request.json()).get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled qiymati true yoki false bo‘lishi kerak")
    return await agent.set_greet_unknown(enabled)


@app.post("/dashboard/api/video-unknown")
async def dashboard_video_unknown(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    enabled = (await request.json()).get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled qiymati true yoki false bo‘lishi kerak")
    return await agent.set_video_unknown(enabled)


@app.post("/dashboard/api/stranger-flag")
async def dashboard_stranger_flag(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="So‘rov noto‘g‘ri")
    name, enabled = body.get("name"), body.get("enabled")
    if type(name) is not str or type(enabled) is not bool:
        raise HTTPException(status_code=400, detail="name va enabled noto‘g‘ri")
    try:
        return await agent.set_stranger_flag(name, enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/dashboard/api/ai-providers")
async def dashboard_add_ai_provider(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    try:
        provider = parse_provider(await request.json())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    slot = free_slot(settings=agent.settings)
    if slot is None:
        raise HTTPException(status_code=409, detail="Barcha 8 ta AI joyi band")
    values = provider_env(slot, provider)
    persisted = await save_env_vars(values)
    os.environ.update(values)
    updated = apply_provider(agent.settings, slot, provider)
    agent.settings = updated
    if agent.assistant:
        agent.assistant.update_settings(updated)
    return {"ok": True, "slot": slot, "name": provider.name, "model": provider.model, "persisted": persisted}


@app.post("/dashboard/api/ai-first")
async def dashboard_ai_first(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    body = await request.json()
    slot = body.get("slot") if isinstance(body, dict) else None
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot <= MAX_BACKUP_PROVIDERS:
        raise HTTPException(status_code=400, detail="AI joyi noto‘g‘ri")
    try:
        updated, values = set_first(agent.settings, slot)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    persisted = await save_env_vars(values)
    os.environ.update(values)
    agent.settings = updated
    if agent.assistant:
        agent.assistant.update_settings(updated)
    return {"ok": True, "slot": slot, "persisted": persisted}


@app.delete("/dashboard/api/ai-providers/{slot}")
async def dashboard_remove_ai_provider(
    slot: int,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    if not 1 <= slot <= MAX_BACKUP_PROVIDERS:
        raise HTTPException(status_code=404, detail="Bunday AI joyi yo‘q")
    keys = list(slot_env_names(slot).values())
    if agent.settings.ai_first_slot == slot:
        keys.append("AI_FIRST_SLOT")
    persisted = await delete_env_vars(keys)
    updated = remove_slot(agent.settings, slot, env={})
    agent.settings = updated
    if agent.assistant:
        agent.assistant.update_settings(updated)
    return {"ok": True, "slot": slot, "persisted": persisted}


@app.post("/dashboard/api/ai-check")
async def dashboard_ai_check(
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    return await agent.ai_check()


@app.api_route("/ping", methods=["GET", "HEAD"])
async def ping() -> PlainTextResponse:
    # Minimal public liveness probe for keep-alive pingers: no agent or Telegram access.
    return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})


@app.get("/healthz")
async def health() -> dict[str, object]:
    # Render and keep-alive probes are public. Do not expose the connected
    # Telegram identity or account/session configuration here.
    return {"ok": True, "connected": agent.connected}


@app.get("/admin/status")
async def admin_status(authorization: str | None = Header(default=None)) -> dict[str, object]:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, settings.admin_token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {**agent.status(), "keepalive": keepalive.status() if keepalive else {"enabled": False}}


@app.get("/dashboard/api/accounts")
async def dashboard_accounts(
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    return {"accounts": agent.account_list(), "active": agent.account_label, "connected": agent.connected}


@app.post("/dashboard/api/accounts/switch")
async def dashboard_accounts_switch(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    body = await request.json()
    account_id = str(body.get("id", "")).strip() if isinstance(body, dict) else ""
    try:
        result = await agent.switch_account(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except TelegramSetupTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from None
    except Exception as exc:
        log.warning("Switching Telegram account failed: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="Akkauntga ulanib bo‘lmadi. Hozirgi akkaunt o‘z holicha qoldi.") from None
    return {**result, "accounts": agent.account_list(), "session_persisted": agent.session_persisted}


@app.post("/dashboard/api/accounts/remove")
async def dashboard_accounts_remove(
    request: Request,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    body = await request.json()
    account_id = str(body.get("id", "")).strip() if isinstance(body, dict) else ""
    try:
        await agent.forget_account(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"accounts": agent.account_list()}


@app.get("/setup/telegram", response_class=HTMLResponse)
async def telegram_setup() -> str:
    return _SETUP_FILE.read_text(encoding="utf-8")


@app.get("/setup/telegram/status")
async def telegram_setup_status(
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Akkaunt ulash uchun avval boshqaruv paneliga kiring.")
    return agent.setup_result() | {"connected": agent.connected}


@app.post("/setup/telegram/auth")
async def telegram_setup_auth(request: Request,
                              shadow_setup: str | None = Cookie(default=None)) -> JSONResponse:
    token = str((await request.json()).get("token", "")).strip()
    accepted = _dashboard_allowed(shadow_setup) or any(
        expected and secrets.compare_digest(token, expected)
        for expected in (settings.setup_token, settings.admin_token)
    )
    if not accepted:
        raise HTTPException(status_code=401, detail="Setup token noto‘g‘ri")
    response = JSONResponse({"ok": True})
    if token:
        response.set_cookie("shadow_setup", token, httponly=True, secure=_secure_cookie(request), samesite="lax", max_age=43200)
    return response


def _require_setup(cookie: str | None) -> None:
    if not _dashboard_allowed(cookie):
        raise HTTPException(status_code=401, detail="Setup ruxsati kerak")


@app.post("/setup/telegram/code")
async def telegram_setup_code(request: Request, shadow_setup: str | None = Cookie(default=None)) -> dict[str, object]:
    _require_setup(shadow_setup)
    phone = str((await request.json()).get("phone", "")).strip()
    if not phone.startswith("+") or len(phone) < 8:
        raise HTTPException(status_code=400, detail="Telefon raqamini xalqaro formatda kiriting")
    try:
        await agent.request_login_code(phone)
    except TelegramSetupTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/setup/telegram/verify")
async def telegram_setup_verify(request: Request, shadow_setup: str | None = Cookie(default=None)) -> dict[str, object]:
    _require_setup(shadow_setup)
    code = str((await request.json()).get("code", "")).strip()
    try:
        result = await agent.complete_login(code)
    except TelegramSetupTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    if isinstance(result, str):
        return {"status": result}
    return result


@app.post("/setup/telegram/password")
async def telegram_setup_password(request: Request, shadow_setup: str | None = Cookie(default=None)) -> dict[str, object]:
    _require_setup(shadow_setup)
    password = str((await request.json()).get("password", ""))
    try:
        result = await agent.complete_password(password)
    except TelegramSetupTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    return result
