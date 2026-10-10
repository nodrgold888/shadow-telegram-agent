# Shadow Telegram agent - project notes for Claude

Shadow is a private Telegram assistant for independently configured personal accounts (Telethon user session + FastAPI dashboard),
with concurrent workers in `shadow/runtime.py`, deployed on Render. The owner speaks Uzbek: user-facing strings are Uzbek, code and docs are English.

## Layout
- `shadow/app.py` FastAPI routes (dashboard API, `/ping`, `/healthz`, setup), `shadow/dashboard.html` single-file UI.
- `shadow/telegram_agent.py` Telegram client, message handling, status payload, chat approvals/profiles.
- `shadow/accounts.py` saved Telegram accounts (`TELEGRAM_ACCOUNTS`: id -> label + session). Sessions are full logins:
  only `public_view()` goes to the panel; `TELEGRAM_SESSION` selects the default panel account after restart; all saved accounts run concurrently and the list fills itself on connect.
  Per-account state (allowlist, friends, chat memory, reply/stranger switches, model choice) lives in
  `TELEGRAM_ACCOUNT_SETTINGS` (`persist.SCOPED_DEFAULTS`): `load_local_settings()` layers the active account's
  bundle over the globals and `_save_local`/`_put_env_var` redirect those keys into it. `_activate_client` enters the
  scope (first account keeps its old settings, later ones start blank). New per-account setting = add it to SCOPED_DEFAULTS.
- `shadow/assistant.py` AI calls: free-only by default (known free cloud routes + configured local host); paid modes opt into OpenAI Responses API and the backup provider chain
  (OpenAI-compatible Chat Completions, slots `AI_*`, `AI_*_2`..`_12`), tools (calculator, Word, Excel).
- `shadow/skills/*.md` prompt skills (human chat, banking, video download); `shadow/agents.py` per-chat agents.
- `shadow/config.py` env settings and model catalog; `shadow/persist.py` Render env / local state persistence.
- `integrations/freellmapi/` + `.claude/skills/freellmapi/SKILL.md`: FreeLLMAPI as a backup AI (not vendored).

## Working here
- Tests: `PYTHONPATH=<stub dir containing an empty pyaes.py> python -m pytest -q tests` in the sandbox where
  telethon's `pyaes` dependency cannot be built (CI installs requirements normally). CI runs `python -m pytest -q tests`,
  so new `tests/test_*.py` files are picked up automatically; the job keeps the name `office`.
- Dashboard JS is one inline script: syntax-check it with node and drive it with Playwright (Chromium at
  `/opt/pw-browsers/chromium`) using stubbed `/dashboard/api/*` routes; fastapi cannot be run in the sandbox.
- Every change goes through a PR from a `claude/...` branch; the owner says "merge qil" to merge (squash).
  Stacked PRs: after the base PR is squash-merged, retarget the next PR to `main` and merge `main` into it
  (`git checkout --ours` for files that only differ by the squashed history), then wait for CI again.
- Commit messages: write them to a file and use `git commit -F` (a `$(...)`-looking string in `-m` is executed).
- Check the full pytest result before pushing: a failing run piped through `tail` still exits 0.

## Hard rules and lessons
- Shadow never claims to be human or the account owner; it says it is Shadow AI when sincerely asked. Style can be as
  natural as possible (`shadow/skills/human_chat.md`) but this rule is not negotiable.
- Replies need: Telegram connected, reply switch on, an AI configured, and the chat approved. Own messages never
  trigger replies. Unapproved chats only get the opt-in bank answers (`PUBLIC_BANK_REPLY`) and the opt-in short greeting
  (dashboard switch `GREET_UNKNOWN`, `shadow/greeting.py`), opt-in video links (`VIDEO_UNKNOWN`), opt-in short voice replies
  (`VOICE_UNKNOWN`) and opt-in notes to Saved Messages (`NOTIFY_UNKNOWN`), all off by default; chats on the friend list (`FRIEND_CHAT_IDS`) get nothing at all.
- Free mode must never invoke excluded paid models or multimedia APIs, even on failure or during model checks. The one-time `AI_COST_POLICY_VERSION` migration enables it privately for existing accounts; later saved choices win.
- OpenAI API credits are separate from ChatGPT plan limits (`insufficient_quota` = no API credits).
  `gpt-reserve` is not an API model (404); `gpt-5.6-luna` needs credits; `gpt-6-luna` worked on the owner's key.
- A saved but no-longer-offered model in `SHADOW_MODEL_SELECTION` is ignored at boot, never fatal.
- A stale-base merge once reverted `app.py` and broke the Render deploy: when the owner or another tool also edits
  the repo, fetch `origin/main` before every branch and diff `app.py` imports against `keepalive.py`/`config.py`.
- Secrets (tokens, API keys, session strings) must never be logged or echoed; `safe_error_detail` masks keys.
