from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

from .config import Settings
from .telegram_agent import TelegramAgent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

settings = Settings.from_env()
agent = TelegramAgent(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await agent.start()
    try:
        yield
    finally:
        await agent.stop()


app = FastAPI(title="Shadow", version="0.1.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def home() -> str:
    state = "ONLAYN" if agent.connected else "SOZLASH KERAK"
    color = "#6ce5d8" if agent.connected else "#f5c866"
    return f"""<!doctype html><html lang='uz'><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>Shadow</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#080b12;color:#f5f7fb;font:16px system-ui}}main{{width:min(520px,calc(100% - 40px));padding:30px;border:1px solid #252d3d;border-radius:18px;background:#101520}}h1{{font-size:2rem;margin:0 0 8px}}p{{color:#99a3b7}}b{{color:{color}}}code{{display:block;padding:12px;background:#080b12;border-radius:10px;color:#c6cedd}}</style></head><body><main><h1>Shadow</h1><p>Shaxsiy Telegram AI yordamchi</p><p>Holat: <b>{state}</b></p><code>/healthz</code></main></body></html>"""


@app.get("/healthz")
async def health() -> dict[str, object]:
    return {"ok": True, **agent.status()}


@app.get("/admin/status")
async def admin_status(authorization: str | None = Header(default=None)) -> dict[str, object]:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN is not configured")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not secrets.compare_digest(supplied, settings.admin_token):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return agent.status()
