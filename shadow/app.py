from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import sessions
from .config import Settings
from .csrf import CSRF_COOKIE_NAME, new_csrf_token, require_csrf
from .persist import save_model_selection, save_reply_enabled
from .config import SUPPORTED_OPENAI_MODELS
from .chat_memory import normalize_chat_profile
from .keepalive import keepalive_interval, keepalive_url, run_keepalive
from .ratelimit import admin_status_limiter, auth_limiter
from .telegram_agent import TelegramAgent, TelegramSetupTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

_DASHBOARD_FILE = Path(__file__).with_name("dashboard.html")
_SESSION_COOKIE = "shadow_session"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    agent = TelegramAgent(settings)

    def _secure_cookie(request: Request) -> bool:
        """Whether the session/CSRF cookies should carry the Secure attribute.

        This used to infer "production" by string-matching the request hostname
        against localhost/127.0.0.1/::1 -- fragile, because Render terminates TLS
        at its edge and forwards plain HTTP to the container, so without trusting
        forwarded-proto headers `request.url.scheme` (and any hostname-based
        heuristic standing in for it) doesn't reliably reflect what the browser
        actually saw. A `Secure` cookie set on a connection the browser doesn't
        consider HTTPS is silently dropped -- no error, no console warning -- a
        plausible root cause of login succeeding per curl/server logs while
        failing in a real browser.

        Fix: trust `request.url.scheme` directly (the Dockerfile now runs uvicorn
        with --proxy-headers so this reflects X-Forwarded-Proto from Render's
        edge, which is the only thing that can reach the container), with an
        explicit SHADOW_COOKIE_SECURE override for self-hosting behind a proxy
        that doesn't forward that header.
        """
        if settings.cookie_secure_override is not None:
            return settings.cookie_secure_override
        return request.url.scheme == "https"

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
    app.state.settings = settings
    app.state.agent = agent

    def _dashboard_allowed(session_id: str | None) -> bool:
        return sessions.role_for(session_id) in {"admin", "setup"}

    def _setup_allowed(session_id: str | None) -> bool:
        return sessions.role_for(session_id) == "setup"

    def _require_setup(session_id: str | None) -> None:
        if not _setup_allowed(session_id):
            raise HTTPException(status_code=401, detail="Setup ruxsati kerak")

    def _issue_session(response: JSONResponse, request: Request, role: sessions.Role) -> None:
        session_id = sessions.create_session(role)
        secure = _secure_cookie(request)
        response.set_cookie(
            _SESSION_COOKIE, session_id, httponly=True, secure=secure, samesite="strict", max_age=1800,
        )
        response.set_cookie(
            CSRF_COOKIE_NAME, new_csrf_token(), httponly=False, secure=secure, samesite="strict", max_age=1800,
        )

    @app.get("/", response_class=HTMLResponse)
    async def home() -> str:
        return _DASHBOARD_FILE.read_text(encoding="utf-8")

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard() -> str:
        return _DASHBOARD_FILE.read_text(encoding="utf-8")

    @app.post("/dashboard/auth")
    async def dashboard_auth(request: Request) -> JSONResponse:
        auth_limiter.check(_client_ip(request))
        token = str((await request.json()).get("token", "")).strip()
        role: sessions.Role | None = None
        if settings.setup_token and secrets.compare_digest(token, settings.setup_token):
            role = "setup"
        elif settings.admin_token and secrets.compare_digest(token, settings.admin_token):
            role = "admin"
        if role is None:
            raise HTTPException(status_code=401, detail="Kirish kodi noto‘g‘ri")
        response = JSONResponse({"ok": True})
        _issue_session(response, request, role)
        return response

    @app.post("/dashboard/logout", dependencies=[Depends(require_csrf)])
    async def dashboard_logout(shadow_session: str | None = Cookie(default=None)) -> JSONResponse:
        sessions.revoke(shadow_session)
        response = JSONResponse({"ok": True})
        response.delete_cookie(_SESSION_COOKIE)
        response.delete_cookie(CSRF_COOKIE_NAME)
        return response

    @app.get("/dashboard/api/status")
    async def dashboard_status(shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
            raise HTTPException(status_code=401, detail="Kirish kerak")
        return agent.status()

    @app.post("/dashboard/api/models", dependencies=[Depends(require_csrf)])
    async def dashboard_models(request: Request, shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
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
    async def dashboard_chats(shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
            raise HTTPException(status_code=401, detail="Kirish kerak")
        return {"chats": await agent.dialogs()}

    @app.get("/dashboard/api/chats/{chat_id}/profile")
    async def dashboard_chat_profile(chat_id: int, shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
            raise HTTPException(status_code=401, detail="Kirish kerak")
        try:
            return {"profile": agent.chat_profile_for(chat_id)}
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.put("/dashboard/api/chats/{chat_id}/profile", dependencies=[Depends(require_csrf)])
    async def dashboard_update_chat_profile(
        chat_id: int, request: Request, shadow_session: str | None = Cookie(default=None),
    ) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
            raise HTTPException(status_code=401, detail="Kirish kerak")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Chat xotirasi noto‘g‘ri formatda")
        try:
            profile = normalize_chat_profile(body)
            return await agent.update_chat_profile(chat_id, profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/dashboard/api/chats/{chat_id}/profile", dependencies=[Depends(require_csrf)])
    async def dashboard_clear_chat_profile(
        chat_id: int, shadow_session: str | None = Cookie(default=None),
    ) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
            raise HTTPException(status_code=401, detail="Kirish kerak")
        try:
            return await agent.clear_chat_profile(chat_id)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post("/dashboard/api/chats/approval", dependencies=[Depends(require_csrf)])
    async def dashboard_chat_approval(request: Request, shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
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

    @app.post("/dashboard/api/chats/clear-approvals", dependencies=[Depends(require_csrf)])
    async def dashboard_clear_chat_approvals(shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
            raise HTTPException(status_code=401, detail="Kirish kerak")
        result = await agent.clear_chat_approvals()
        return result

    @app.post("/dashboard/api/replies", dependencies=[Depends(require_csrf)])
    async def dashboard_replies(request: Request, shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        if not _dashboard_allowed(shadow_session):
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
        return {"ok": True, **agent.status()}

    @app.get("/admin/status")
    async def admin_status(request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
        admin_status_limiter.check(_client_ip(request))
        if not settings.admin_token:
            raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
        supplied = (authorization or "").removeprefix("Bearer ").strip()
        if not secrets.compare_digest(supplied, settings.admin_token):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return agent.status()

    @app.get("/setup/telegram", response_class=HTMLResponse)
    async def telegram_setup() -> str:
        return """<!doctype html><html lang='uz'><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>Shadow Telegram Setup</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#080b12;color:#f5f7fb;font:16px system-ui}main{width:min(520px,calc(100% - 40px));padding:28px;border:1px solid #252d3d;border-radius:18px;background:#101520}input,button{box-sizing:border-box;width:100%;padding:13px;margin:7px 0;border-radius:10px;border:1px solid #344056;background:#090d15;color:#fff}button{background:#58c8bb;color:#07110f;font-weight:700}p{color:#aab3c4}.ok{color:#6ce5d8}.err{color:#ff8f8f}</style></head><body><main><h1>Shadow</h1><p>Telegram ulanishini xavfsiz sozlash</p><section id='auth'><input id='token' type='password' autocomplete='off' placeholder='Setup token'><button onclick='auth()'>Davom etish</button></section><section id='phone' hidden><input id='phoneValue' type='tel' autocomplete='tel' placeholder='+998...'><button onclick='sendCode()'>Kod yuborish</button></section><section id='code' hidden><input id='codeValue' type='text' autocomplete='one-time-code' placeholder='Telegram kodi'><button onclick='verify()'>Tasdiqlash</button></section><section id='password' hidden><input id='passwordValue' type='password' autocomplete='current-password' placeholder='Telegram 2FA paroli'><button onclick='verifyPassword()'>Kirish</button></section><p id='status'></p><script>const q=s=>document.querySelector(s), show=id=>{['auth','phone','code','password'].forEach(x=>q('#'+x).hidden=x!==id)}; function csrfToken(){return (document.cookie.match(/(?:^|; )shadow_csrf=([^;]+)/)||[])[1]||''} async function call(url,body){const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json','x-shadow-csrf':csrfToken()},body:JSON.stringify(body)});const j=await r.json();if(!r.ok)throw Error(j.detail||'Xato');return j}async function auth(){try{await call('/setup/telegram/auth',{token:q('#token').value});show('phone');q('#status').textContent='Xavfsiz kirish tasdiqlandi'}catch(e){q('#status').textContent=e.message}}async function sendCode(){try{await call('/setup/telegram/code',{phone:q('#phoneValue').value});show('code');q('#status').textContent='Kod Telegram ilovasiga yuborildi'}catch(e){q('#status').textContent=e.message}}async function verify(){try{const j=await call('/setup/telegram/verify',{code:q('#codeValue').value});if(j.status==='password_required'){show('password');q('#status').textContent='2FA parolini kiriting'}else done()}catch(e){q('#status').textContent=e.message}}async function verifyPassword(){try{await call('/setup/telegram/password',{password:q('#passwordValue').value});done()}catch(e){q('#status').textContent=e.message}}function done(){['auth','phone','code','password'].forEach(x=>q('#'+x).hidden=true);q('#status').className='ok';q('#status').textContent='Shadow Telegramga ulandi. Xabar yuborish o‘chirilgan.'}</script></main></body></html>"""

    @app.post("/setup/telegram/auth")
    async def telegram_setup_auth(request: Request) -> JSONResponse:
        auth_limiter.check(_client_ip(request))
        token = str((await request.json()).get("token", ""))
        if not settings.setup_token or not secrets.compare_digest(token, settings.setup_token):
            raise HTTPException(status_code=401, detail="Setup token noto‘g‘ri")
        response = JSONResponse({"ok": True})
        _issue_session(response, request, "setup")
        return response

    @app.post("/setup/telegram/code", dependencies=[Depends(require_csrf)])
    async def telegram_setup_code(request: Request, shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        _require_setup(shadow_session)
        phone = str((await request.json()).get("phone", "")).strip()
        if not phone.startswith("+") or len(phone) < 8:
            raise HTTPException(status_code=400, detail="Telefon raqamini xalqaro formatda kiriting")
        try:
            await agent.request_login_code(phone)
        except TelegramSetupTimeout as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/setup/telegram/verify", dependencies=[Depends(require_csrf)])
    async def telegram_setup_verify(request: Request, shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        _require_setup(shadow_session)
        code = str((await request.json()).get("code", "")).strip()
        try:
            return {"status": await agent.complete_login(code)}
        except TelegramSetupTimeout as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc

    @app.post("/setup/telegram/password", dependencies=[Depends(require_csrf)])
    async def telegram_setup_password(request: Request, shadow_session: str | None = Cookie(default=None)) -> dict[str, object]:
        _require_setup(shadow_session)
        password = str((await request.json()).get("password", ""))
        try:
            await agent.complete_password(password)
        except TelegramSetupTimeout as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        return {"status": "connected"}

    return app


settings = Settings.from_env()
app = create_app(settings)
agent = app.state.agent
