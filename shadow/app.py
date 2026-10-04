from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import Cookie, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Settings
from .persist import save_model_selection, save_reply_enabled
from .config import SUPPORTED_OPENAI_MODELS
from .chat_memory import normalize_chat_profile
from .keepalive import keepalive_interval, keepalive_url, run_keepalive
from .telegram_agent import TelegramAgent, TelegramSetupTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = Settings.from_env()
agent = TelegramAgent(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await agent.start()
    url = keepalive_url()
    ping_task = asyncio.create_task(run_keepalive(url, keepalive_interval())) if url else None
    try:
        yield
    finally:
        if ping_task:
            ping_task.cancel()
        await agent.stop()


app = FastAPI(title="Shadow", version="0.1.0", lifespan=lifespan)

_DASHBOARD_FILE = Path(__file__).with_name("dashboard.html")
_SETUP_FILE = Path(__file__).with_name("setup.html")


def _secure_cookie(request: Request) -> bool:
    return not (
        request.url.scheme == "http"
        and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
    )


def _dashboard_allowed(cookie: str | None, authorization: str | None = None) -> bool:
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


def _setup_allowed(cookie: str | None) -> bool:
    return bool(settings.setup_token and cookie and secrets.compare_digest(cookie, settings.setup_token))


def _setup_allowed(cookie: str | None) -> bool:
    return bool(settings.setup_token and cookie and secrets.compare_digest(cookie, settings.setup_token))


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    return _DASHBOARD_FILE.read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    return _DASHBOARD_FILE.read_text(encoding="utf-8")


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


@app.post("/dashboard/logout")
async def dashboard_logout() -> JSONResponse:
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
    return agent.status()


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



@app.get("/dashboard/api/chats/{chat_id}/profile")
async def dashboard_chat_profile(
    chat_id: int,
    shadow_setup: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not _dashboard_allowed(shadow_setup, authorization):
        raise HTTPException(status_code=401, detail="Kirish kerak")
    try:
        return {"profile": agent.chat_profile_for(chat_id)}
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
    return agent.status()


@app.get("/setup/telegram", response_class=HTMLResponse)
async def telegram_setup() -> str:
    return _SETUP_FILE.read_text(encoding="utf-8")


@app.post("/setup/telegram/auth")
async def telegram_setup_auth(request: Request) -> JSONResponse:
    token = str((await request.json()).get("token", "")).strip()
    if not settings.setup_token or not secrets.compare_digest(token, settings.setup_token):
        raise HTTPException(status_code=401, detail="Setup token noto‘g‘ri")
    response = JSONResponse({"ok": True})
    response.set_cookie("shadow_setup", token, httponly=True, secure=_secure_cookie(request), samesite="lax", max_age=43200)
    return response


def _require_setup(cookie: str | None) -> None:
    if not _setup_allowed(cookie):
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
        status = await agent.complete_login(code)
    except TelegramSetupTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    return {"status": status, **(agent.setup_result() if status == "connected" else {})}


@app.post("/setup/telegram/password")
async def telegram_setup_password(request: Request, shadow_setup: str | None = Cookie(default=None)) -> dict[str, object]:
    _require_setup(shadow_setup)
    password = str((await request.json()).get("password", ""))
    try:
        await agent.complete_password(password)
    except TelegramSetupTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    return {"status": "connected", **agent.setup_result()}
