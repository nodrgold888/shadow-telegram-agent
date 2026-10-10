"""Independent Telegram workers; selecting a dashboard account never stops another."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from .accounts import MAX_ACCOUNTS, dump_accounts, forget, parse_accounts, public_view, remember
from .config import Settings
from .persist import account_scope, load_accounts_raw, save_accounts, select_scope, set_task_scope
from .telegram_agent import TelegramAgent, TelegramSetupTimeout, CONNECTION_TIMEOUT_SECONDS


class AccountWorker(TelegramAgent):
    def __init__(self, settings, runtime, expected_id=None):
        super().__init__(settings)
        self.runtime = runtime
        self.expected_id = expected_id

    async def _activate_client(self, client):
        me = await asyncio.wait_for(client.get_me(), timeout=CONNECTION_TIMEOUT_SECONDS)
        if me is None or (self.expected_id and str(me.id) != self.expected_id):
            self.session_revoked = True
            raise ValueError("Saqlangan akkaunt va sessiya mos kelmadi. Akkauntni qayta ulang.")
        await self.runtime.register_worker(str(me.id), self)
        await super()._activate_client(client)

    async def _remember_account(self, session):
        if self.account_id is not None and session:
            await self.runtime.remember_worker(self, session)

    async def run_repair_agent(self):
        with account_scope(self.expected_id):
            return await super().run_repair_agent()

    async def _on_message(self, event):
        # Telethon's dispatcher may predate account activation during setup.
        # Always bind callbacks explicitly rather than trusting inherited tasks.
        with account_scope(self.expected_id):
            return await super()._on_message(event)

    async def _on_owner_command(self, event):
        with account_scope(self.expected_id):
            return await super()._on_owner_command(event)


class AccountRuntime:
    def __init__(self, settings: Settings):
        self._registry = parse_accounts(load_accounts_raw())
        self._workers: dict[str, AccountWorker] = {}
        self._request_worker = ContextVar("shadow_dashboard_worker", default=None)
        self._registry_lock = asyncio.Lock()
        self._selection_lock = asyncio.Lock()
        self._setup_worker: AccountWorker | None = None
        self._started = False
        selected_id = next((key for key, value in self._registry.items()
                            if value["session"] == settings.telegram_session), None)
        with account_scope(selected_id):
            self._primary = AccountWorker(settings, self, selected_id)
        if selected_id:
            self._workers[selected_id] = self._primary
        select_scope(selected_id)

    def _current(self):
        return self._request_worker.get() or self._primary

    def __getattr__(self, name):
        return getattr(self._current(), name)

    def __setattr__(self, name, value):
        if name.startswith("_") or callable(getattr(type(self), name, None)):
            object.__setattr__(self, name, value)
        else:
            setattr(self._current(), name, value)

    def __delattr__(self, name):
        if name in self.__dict__:
            object.__delattr__(self, name)
        else:
            delattr(self._current(), name)

    @property
    def connected(self):
        return self._current().connection_alive

    @property
    def account_id(self):
        worker = self._current()
        return worker.account_id or worker.expected_id

    @contextmanager
    def request_context(self, account_id=None):
        worker = self._workers.get(str(account_id)) if account_id is not None else self._primary
        if worker is None:
            raise ValueError("Bu akkaunt panel sessiyasida mavjud emas")
        token = self._request_worker.set(worker)
        try:
            with account_scope(str(worker.account_id or worker.expected_id) if
                               (worker.account_id or worker.expected_id) is not None else None):
                yield
        finally:
            self._request_worker.reset(token)

    def _new_worker(self, account_id):
        saved = self._registry[account_id]
        with account_scope(account_id):
            settings = replace(Settings.from_env(), telegram_session=saved["session"])
            worker = AccountWorker(settings, self, account_id)
        worker.accounts = dict(self._registry)
        self._workers[account_id] = worker
        return worker

    async def start(self):
        if self._started:
            return
        self._started = True
        for key in self._registry:
            if key not in self._workers:
                self._new_worker(key)
        workers = list(dict.fromkeys([self._primary, *self._workers.values()]))
        for worker in workers:
            with account_scope(worker.expected_id):
                await worker.start()

    async def stop(self):
        workers = list(dict.fromkeys([self._primary, *self._workers.values(), self._setup_worker]))
        await asyncio.gather(*(worker.stop() for worker in workers if worker), return_exceptions=True)
        self._started = False

    async def register_worker(self, account_id, worker):
        async with self._registry_lock:
            if account_id not in self._registry and len(self._registry) >= MAX_ACCOUNTS:
                raise ValueError("Akkauntlar royxati tolgan. Yangi akkaunt uchun avval birini olib tashlang.")
            previous = self._workers.get(account_id)
            if previous is not None and previous is not worker:
                await previous.stop()
                if self._primary is previous:
                    self._primary = worker
            worker.expected_id = account_id
            self._workers[account_id] = worker

    async def remember_worker(self, worker, session):
        async with self._registry_lock:
            updated = remember(self._registry, worker.account_id, worker.account_label, session)
            if updated == self._registry:
                return
            removed = set(self._registry) - set(updated)
            self._registry = updated
            for item in self._workers.values():
                item.accounts = dict(updated)
            await save_accounts(dump_accounts(updated))
            for key in removed:
                old = self._workers.pop(key, None)
                if old and old is not self._primary:
                    await old.stop()

    def account_list(self):
        accounts = public_view(self._registry, self.account_id)
        for item in accounts:
            worker = self._workers.get(item["id"])
            connected = bool(worker and worker.connection_alive)
            item.update(connected=connected,
                        runtime_state="connected" if connected else "login_required" if
                        worker and worker.session_revoked else "reconnecting" if
                        worker and worker._watchdog_task and not worker._watchdog_task.done() else "stopped",
                        reply_enabled=bool(worker and worker.reply_enabled))
        return accounts

    def status(self):
        return {**self._current().status(), "account_workers": self.account_list(),
                "concurrent_accounts": len(self._workers), "multi_account": True}

    def _select(self, worker):
        self._primary = worker
        self._request_worker.set(worker)
        key = str(worker.account_id or worker.expected_id)
        select_scope(key)
        set_task_scope(key)

    async def switch_account(self, account_id):
        key = str(account_id)
        if key not in self._registry:
            raise ValueError("Bunday akkaunt saqlanmagan")
        async with self._selection_lock:
            worker = self._workers.get(key) or self._new_worker(key)
            with account_scope(key):
                if not worker.connection_alive:
                    if worker.session_revoked:
                        raise ValueError("Akkaunt sessiyasi tugagan. Akkauntni qayta ulang.")
                    try:
                        async with asyncio.timeout(CONNECTION_TIMEOUT_SECONDS + 5):
                            async with worker._setup_lock:
                                async with worker._recovery_lock:
                                    if not worker.connection_alive:
                                        await worker._connect_saved_session()
                    except TimeoutError:
                        raise TelegramSetupTimeout("Telegramga ulanish vaqti tugadi. Qayta urinib koring.") from None
                    if not worker.connection_alive:
                        raise TelegramSetupTimeout("Telegramga ulanib bolmadi. Qayta urinib koring.")
                changed = self._current() is not worker
                await worker.start()
                # Session persistence picks the default after restart; other
                # running workers and their private settings remain untouched.
                if changed:
                    await worker._persist_login(worker.client)
            self._select(worker)
            return {"account": worker.account_label, "account_id": worker.account_id, "changed": changed}

    async def forget_account(self, account_id):
        key = str(account_id)
        async with self._registry_lock:
            updated = forget(self._registry, key, self.account_id)
            worker = self._workers.pop(key, None)
            if worker:
                await worker.stop()
            self._registry = updated
            for item in self._workers.values():
                item.accounts = dict(updated)
            await save_accounts(dump_accounts(updated))

    async def send_login_code(self, account_id, text):
        worker = self._workers.get(str(account_id))
        if worker is None:
            raise ValueError("Bunday Telegram akkaunti saqlanmagan")
        if worker.connection_alive:
            await asyncio.wait_for(worker.send_to_self(text), timeout=CONNECTION_TIMEOUT_SECONDS)
        else:
            await worker.send_login_code(str(account_id), text)

    async def request_login_code(self, phone):
        if self._setup_worker is None:
            with account_scope(None):
                self._setup_worker = AccountWorker(self._current().settings, self)
            self._setup_worker.accounts = dict(self._registry)
        with account_scope(None):
            await self._setup_worker.request_login_code(phone)

    async def _finish_setup(self):
        worker = self._setup_worker
        if worker and worker.connection_alive:
            with account_scope(str(worker.account_id)):
                await worker.start()
            self._select(worker)
            self._setup_worker = None

    async def complete_login(self, code):
        if self._setup_worker is None:
            raise ValueError("Avval telefon raqamingiz uchun kod sorang")
        with account_scope(None):
            result = await self._setup_worker.complete_login(code)
        await self._finish_setup()
        return result

    async def complete_password(self, password):
        if self._setup_worker is None:
            raise ValueError("Avval kirish kodini tasdiqlang")
        with account_scope(None):
            result = await self._setup_worker.complete_password(password)
        await self._finish_setup()
        return result
