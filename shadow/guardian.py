"""Server-owned supervision; independent of whether a dashboard browser is open."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path

import httpx

from .development import DevelopmentError

log = logging.getLogger("shadow.guardian")
AI_INTERVAL = 6 * 60 * 60
CHECK_INTERVALS = {60, 300, 900}


class Guardian:
    def __init__(self, agent, studio, state_file: Path | None = None, clock=time.time):
        self.agent, self.studio, self.clock = agent, studio, clock
        default = studio.state_dir / "guardian-state.json"
        self.state_file = state_file or Path(os.getenv("SHADOW_GUARDIAN_STATE_FILE", str(default)))
        self.scopes: dict[str, dict] = {}
        self.loaded = False
        self.storage_ok = True
        self.task: asyncio.Task | None = None
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.heartbeat: float | None = None
        self.started_at: float | None = None
        self.running_scope: str | None = None
        self.source_checked_at = 0.0
        self.source_result: dict | None = None

    def scope_key(self) -> str:
        account = str(self.agent.account_id or "unconnected")
        return hashlib.sha256(account.encode()).hexdigest()[:24]

    def _load(self):
        if self.loaded:
            return
        try:
            if self.state_file.exists() and self.state_file.stat().st_size < 2_000_000:
                data = json.loads(self.state_file.read_text())
                if isinstance(data, dict):
                    self.scopes = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}
        except (OSError, ValueError):
            log.warning("Guardian state could not be restored")
        self.loaded = True

    def _scope(self) -> tuple[str, dict]:
        self._load()
        key = self.scope_key()
        state = self.scopes.setdefault(key, {})
        defaults = {"enabled": os.getenv("SHADOW_GUARDIAN_ENABLED", "true").lower() != "false",
                    "auto_repair": True, "ai_review": True, "interval_seconds": 60,
                    "checks": [], "events": [], "cycles": 0, "repairs": 0,
                    "last_check_at": None, "next_check_at": None, "last_ai_at": None,
                    "last_ai_job": None, "ai_note": "Birinchi tekshiruv kutilmoqda"}
        for name, value in defaults.items():
            state.setdefault(name, value)
        if state["interval_seconds"] not in CHECK_INTERVALS:
            state["interval_seconds"] = 60
        return key, state

    def _save(self):
        temporary = None
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(prefix=".guardian-", dir=self.state_file.parent)
            with os.fdopen(fd, "w") as stream:
                json.dump(self.scopes, stream, ensure_ascii=False)
            os.replace(temporary, self.state_file)
            self.storage_ok = True
        except OSError:
            self.storage_ok = False
            log.warning("Guardian state could not be saved")
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _event(self, state: dict, message: str, kind="info"):
        state["events"].insert(0, {"time": self.clock(), "message": message, "kind": kind})
        del state["events"][40:]

    def snapshot(self) -> dict:
        key, state = self._scope()
        alive = bool(self.task and not self.task.done())
        result = {name: state[name] for name in ("enabled", "auto_repair", "ai_review", "interval_seconds",
                  "checks", "events", "cycles", "repairs", "last_check_at", "last_ai_at", "last_ai_job", "ai_note")}
        job = self.studio.jobs.get(state["last_ai_job"])
        result.update(running=alive, checking=self.running_scope == key,
                      heartbeat_at=self.heartbeat, started_at=self.started_at,
                      next_check_at=state["next_check_at"] if alive and state["enabled"] else None,
                      next_ai_at=(state["last_ai_at"] + AI_INTERVAL) if state["last_ai_at"] else None,
                      ai_interval_seconds=AI_INTERVAL, storage_ok=self.storage_ok,
                      ai_job_state=job.get("state") if job else None)
        return result

    def configure(self, **changes) -> dict:
        for name, value in changes.items():
            if name in {"enabled", "auto_repair", "ai_review"} and type(value) is bool:
                continue
            if name == "interval_seconds" and type(value) is int and value in CHECK_INTERVALS:
                continue
            raise DevelopmentError("Invalid background agent setting")
        _, state = self._scope()
        state.update(changes)
        state["next_check_at"] = self.clock() if state["enabled"] else None
        self._event(state, "Doimiy agent sozlamalari yangilandi")
        self._save()
        self.wake.set()
        return self.snapshot()

    def request_check(self) -> dict:
        _, state = self._scope()
        if not state["enabled"]:
            raise DevelopmentError("Avval doimiy agentni yoqing")
        if not self.task or self.task.done():
            raise DevelopmentError("Fon agenti ishlamayapti. Serverni qayta ishga tushiring")
        if self.running_scope != self.scope_key():
            state["next_check_at"] = self.clock()
            self.wake.set()
        return self.snapshot()

    def start(self):
        if not self.task or self.task.done():
            self._scope()
            # Check immediately after restart, preserving enabled flags and the AI budget.
            for state in self.scopes.values():
                state["next_check_at"] = None
            self.started_at = self.clock()
            self.task = asyncio.create_task(self._run(), name="shadow-guardian")

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.running_scope = None

    async def _run(self):
        while True:
            self.heartbeat = self.clock()
            key, state = self._scope()
            try:
                if state["enabled"] and (not state["next_check_at"] or self.clock() >= state["next_check_at"]):
                    await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never let one bad check kill supervision or store an exception with secrets.
                self._event(state, "Tekshiruv bajarilmadi; keyingi davrada qayta uriniladi", "error")
                state["next_check_at"] = self.clock() + state["interval_seconds"]
                self._save()
                log.warning("Guardian cycle failed")
            self.heartbeat = self.clock()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except TimeoutError:
                pass
            self.wake.clear()

    def _source_check(self) -> dict:
        failures = []
        for name in ("app.py", "development.py", "guardian.py", "assistant.py", "development.js",
                     "dashboard.html", "dashboard-theme.css", "dashboard-redesign.css"):
            path = self.studio.root / "shadow" / name
            try:
                content = path.read_text(encoding="utf-8")
                if not content.strip():
                    raise ValueError("empty source")
                if path.suffix == ".py":
                    ast.parse(content, filename=name)
            except (OSError, ValueError, SyntaxError):
                failures.append(name)
        return {"id": "source", "title": "Agent va panel fayllari", "state": "error" if failures else "ok",
                "detail": ("Tekshirish kerak: " + ", ".join(failures)) if failures else
                          "Asosiy fayllar mavjud; Python sintaksisi tekshirildi. Funksional testlar bajarilmadi."}

    async def _local_ai_check(self) -> dict | None:
        settings = self.agent.settings
        if not all((getattr(settings, "local_ai_base_url", ""), getattr(settings, "local_ai_api_key", ""),
                    getattr(settings, "local_ai_model", ""))):
            return None
        healthy = False
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.get(settings.local_ai_base_url + "/models",
                                            headers={"Authorization": "Bearer " + settings.local_ai_api_key})
                response.raise_for_status()
                payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            healthy = any(isinstance(model, dict) and model.get("id") == settings.local_ai_model
                          for model in models)
        except (httpx.HTTPError, ValueError, TypeError):
            pass
        return {"id": "local_ai", "title": "Lokal AI modeli", "state": "ok" if healthy else "warning",
                "detail": "Model yuklangan va javob berishga tayyor" if healthy else
                          "Ollama ulanmagan yoki model yuklanmagan; lokal server va modelni tekshiring"}

    async def check_once(self):
        async with self.lock:
            key, state = self._scope()
            if not state["enabled"]:
                return
            self.running_scope = key
            try:
                fixed, recovery_failed = [], False
                if state["auto_repair"]:
                    try:
                        async with asyncio.timeout(30):
                            async with self.agent._setup_lock:
                                result = await self.agent.run_repair_agent()
                        fixed = result.get("fixed", [])
                    except Exception:
                        recovery_failed = True
                # Switching Telegram accounts while a check awaits must not mix snapshots.
                if key != self.scope_key():
                    return
                status = self.agent.status()
                checks = [
                    {"id": "telegram", "title": "Telegram ulanishi", "state": "ok" if status.get("connected") else "warning",
                     "detail": "Akkaunt ulangan" if status.get("connected") else "Akkauntlar bo‘limida ulanishni tekshiring"},
                    {"id": "ai", "title": "AI sozlamalari", "state": "ok" if self.agent.settings.ai_ready else "warning",
                     "detail": "Provayder sozlangan; haqiqiy javob AI auditida tekshiriladi" if self.agent.settings.ai_ready else "AI provayderlar bo‘limida kalit va modelni sozlang"},
                    {"id": "listener", "title": "Avtojavob tinglovchisi", "state": ("ok" if status.get("reply_listener_registered") else "error") if status.get("reply_enabled") else "idle",
                     "detail": "Tinglovchi holati tekshirildi" if status.get("reply_enabled") else "Avtojavob egasi tomonidan o‘chirilgan"},
                    {"id": "replies", "title": "Javob xatolari", "state": "warning" if status.get("last_reply_error") else "ok",
                     "detail": "Oxirgi javobda xato qayd etilgan; Monitoring bo‘limini ko‘ring" if status.get("last_reply_error") else "Javob xatosi qayd etilmagan"},
                ]
                local_ai = await self._local_ai_check()
                if key != self.scope_key():
                    return
                if local_ai:
                    checks.append(local_ai)
                if recovery_failed:
                    checks.append({"id": "recovery", "title": "Avtomatik tiklash", "state": "warning", "detail": "Tiklash tugamadi; keyingi davrada qayta uriniladi"})
                if self.source_result is None or self.clock() - self.source_checked_at >= 900:
                    self.source_result = await asyncio.to_thread(self._source_check)
                    self.source_checked_at = self.clock()
                if key != self.scope_key():
                    return
                checks.append(self.source_result.copy())
                state["checks"] = checks
                state["cycles"] += 1
                state["repairs"] += len(fixed)
                state["last_check_at"] = self.clock()
                state["next_check_at"] = self.clock() + state["interval_seconds"] if state["enabled"] else None
                warnings = sum(c["state"] in {"warning", "error"} for c in checks)
                self._event(state, f"Tekshiruv tugadi: {warnings} ta e’tibor talab qiladigan holat", "warning" if warnings else "ok")
                if fixed:
                    self._event(state, f"{len(fixed)} ta runtime muammosi avtomatik tiklandi", "ok")
                await self._schedule_review(key, state, status)
                self._save()
            finally:
                self.running_scope = None
                self.heartbeat = self.clock()

    async def _schedule_review(self, key: str, state: dict, status: dict):
        if not state["enabled"] or not state["ai_review"] or key != self.scope_key():
            state["ai_note"] = "Avtomatik AI auditi o‘chirilgan"
            return
        if not self.agent.settings.ai_ready:
            state["ai_note"] = "AI auditi uchun provayderni sozlang"
            return
        if self.studio.task and not self.studio.task.done():
            state["ai_note"] = "Development Studio vazifasi tugashi kutilmoqda"
            return
        if state["last_ai_at"] is not None and self.clock() - state["last_ai_at"] < AI_INTERVAL:
            state["ai_note"] = "Keyingi AI auditi jadval bo‘yicha bajariladi"
            return
        self.studio._load()
        # Bounded retention only for this account's automatic, patch-free audits.
        audits = sorted((j for j in self.studio.jobs.values() if j.get("origin") == "guardian"
                         and j.get("account_scope") == key and not j.get("patch")
                         and j["state"] in {"ready", "failed", "cancelled", "interrupted"}),
                        key=lambda j: j["created_at"], reverse=True)
        for job in audits[4:]:
            self.studio.delete(job["id"])
        objective = ("Shadow doimiy agenti uchun kodga asoslangan o‘zini tekshirish auditini bajaring. "
                     "Development Studio, guardian, server ishga tushishi, vaqt chegaralari, xato tiklash, "
                     "akkauntlar izolyatsiyasi va panel holatini tekshiring. Aniq fayl va dalil bilan haqiqiy "
                     "kamchiliklarni va tuzatish yo‘lini yozing. Soxta muammo yaratmang. Kodni o‘zgartirmang; "
                     "test yoki tashqi API tekshiruvini bajardim demang. Shaxsiy xabarlar va sirlarni o‘qimang.")
        runtime = {k: status[k] for k in ("connected", "reply_enabled", "reply_ready", "reply_count") if k in status}
        runtime["has_reply_error"] = bool(status.get("last_reply_error"))
        try:
            job = await self.studio.start(self.agent.settings, objective, "audit", runtime, "code_review",
                                          origin="guardian", account_scope=key)
        except DevelopmentError:
            state["ai_note"] = "AI auditi boshlanmadi; provayder va vazifalar tarixini tekshiring"
            return
        state["last_ai_at"] = self.clock()
        state["last_ai_job"] = job["id"]
        state["ai_note"] = "AI o‘zini tekshirish auditi boshlandi"
        self._event(state, "Rejali AI auditi Development Studio’da boshlandi")
