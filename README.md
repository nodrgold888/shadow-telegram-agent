# Shadow

Shadow is a private, always-on Telegram assistant for one personal account. It replies in Uzbek inside approved chats and groups, using the OpenAI Responses API.

New deployments start in read-only connection mode with `REPLY_ENABLED=false`. In this mode Shadow connects to Telegram but does not register a message handler, mark messages read, show typing activity, or send replies.

## Run locally from GitHub

Requires Python 3.12 or newer. No Render account is needed.

```bash
git clone https://github.com/nodrgold888/shadow-telegram-agent.git
cd shadow-telegram-agent
python -m venv .venv
```

Activate the environment:

- Windows PowerShell: `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Then run:

```bash
python -m pip install -r requirements.txt
python scripts/run_local.py
```

The first launch creates a private `.env` file and generates `SETUP_TOKEN`.
Edit `.env` and fill `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and
`OPENAI_API_KEY`, then restart the command. Existing Render secrets are not
downloaded automatically. Obtain Telegram credentials from
https://my.telegram.org and an OpenAI key from https://platform.openai.com/api-keys.

Open http://127.0.0.1:10000/setup/telegram and use your `SETUP_TOKEN` to sign in.
Complete Telegram phone/code verification (and your two-step password if enabled).
Open http://127.0.0.1:10000/ for the dashboard, select chats, then enable replies
when ready. Replies start disabled; no chats are automatically approved.

Telegram login, chat permissions, and the reply switch are saved privately in
`.shadow-state/state.json`; saved choices override their corresponding `.env`
values on later launches. Never upload this directory or `.env`.
Keep the terminal and computer running with internet access for continuous operation.
Stop with Ctrl+C; start again with `python scripts/run_local.py`.
Public Instagram/TikTok video downloading also runs locally.

## Safety model

- No chat is handled until its numeric Telegram chat ID is listed in `APPROVED_CHAT_IDS`.
- Group replies default to mentions and direct replies to Shadow. Set `GROUP_REPLY_MODE=all` only for groups where every message should receive an answer.
- Passwords, Telegram codes, API keys, and the session string stay in Render secrets.
- Shadow refuses irreversible or high-risk actions and escalates them to the account owner.
- Do not use Shadow for spam, unsolicited outreach, artificial engagement, or other Telegram Terms violations.

## Create Telegram credentials

1. Sign in at [my.telegram.org](https://my.telegram.org/).
2. Open **API development tools** and create an application.
3. Save the `api_id` and `api_hash` privately.

## Create the private Telegram session

Run this on a trusted computer, not on a shared server:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/create_session.py
```

Enter the Telegram code and two-step password only in that local terminal. Copy the resulting `TELEGRAM_SESSION` directly into Render's secret environment variables.

## Find chat IDs

Forward a message to a trusted ID helper or temporarily inspect Telethon logs locally. Add only the intended numeric IDs to `APPROVED_CHAT_IDS`, separated by commas. Telegram supergroup IDs usually begin with `-100`.

## Render deployment

`render.yaml` defines a paid 512 MB web service in Frankfurt so Shadow remains available continuously. Create a Blueprint from this repository, add the required secret values, and deploy.

Required secrets:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `SETUP_TOKEN`

For the free preview, open `/setup/telegram` and authenticate with the private setup token. The resulting Telegram session lives only in memory and is lost whenever the free service sleeps or restarts.

When you are ready to allow replies, add `OPENAI_API_KEY` and `APPROVED_CHAT_IDS`, then explicitly set `REPLY_ENABLED=true`.

The public `/healthz` route reports connection state without exposing secrets. `/admin/status` requires the generated `ADMIN_TOKEN` bearer token.

The private `/dashboard` page is the Uzbek control panel. Sign in with `SETUP_TOKEN` or `ADMIN_TOKEN` to see Telegram connectivity, the connected account, and a read-only list of recent dialogs. Its reply switch starts off. Turning replies on requires an explicit confirmation and configured `OPENAI_API_KEY` plus `APPROVED_CHAT_IDS`; reply changes apply only to the running process and reset to the configured value after a restart.

## Local development

```bash
cp .env.example .env
set -a && source .env && set +a
uvicorn shadow.app:app --reload --port 10000
```

Open <http://localhost:10000/healthz>.

## Keep-alive ping

On Render's free plan the service sleeps after ~15 minutes without inbound traffic, which drops the in-memory Telegram session. Shadow therefore pings its own public `/healthz` every 10 minutes. It is enabled automatically when `RENDER_EXTERNAL_URL` is set (Render sets it for you); set `KEEPALIVE_URL` to override the target and `KEEPALIVE_INTERVAL_SECONDS` (minimum 60) to change the cadence. Leave both unset locally to disable it. The ping only calls `/healthz`; it never sends Telegram messages.

### Second ping source (external, survives a crashed process)

The self-ping above only runs while Shadow's own process is alive — it cannot
wake a service that has fully crashed or failed to boot. `.github/workflows/keepalive.yml`
adds an independent ping from GitHub Actions on a 10-minute schedule, calling
the same `/healthz` endpoint from outside the service entirely. Set it up:

1. Repo Settings → Secrets and variables → Actions → **Variables** tab → New repository variable.
2. Name: `HEALTHZ_URL`. Value: `https://<your-service>.onrender.com/healthz`.

Like the internal ping, this only calls `/healthz` and never touches Telegram.
GitHub's own scheduler can run a few minutes late under load, so treat this as
a redundant second source, not a replacement for the internal ping.

## Surviving restarts (no re-login)

The Telegram session created at `/setup/telegram` would normally live only in memory. To keep it across restarts and redeploys:

1. Create a Render API key (Account Settings → API Keys) and add it as the `RENDER_API_KEY` secret. `RENDER_SERVICE_ID` is provided by Render automatically.
2. Log in once at `/setup/telegram`. After a successful login Shadow writes the session into the service's `TELEGRAM_SESSION` secret (Render redeploys once, then boots straight into the saved session).

`/admin/status` reports `session_persisted` and `can_persist_session`. If you prefer, run `scripts/create_session.py` locally and paste `TELEGRAM_SESSION` into Render yourself. The session string is never logged or exposed over HTTP.

While running, a watchdog checks the connection every 60 seconds and reconnects if it dropped or failed at boot. Only a session revoked from Telegram (Settings → Devices → Terminate) needs a new login; this is reported as `session_revoked`.

## Selective model use

Everyday replies use `OPENAI_MODEL` (default `gpt-5-mini`). Shadow sends explicit
multi-step analysis, proofs and advanced equations, long prompts, and Office-file
work to `OPENAI_COMPLEX_MODEL` (default `gpt-6-luna`) with medium reasoning effort.
The routing decision happens in the app; the advanced model is not used for every
message, and Shadow does not silently fall back to it for ordinary replies.
Choose either model for everyday or complex work in Dashboard → Sozlamalar → AI modeli. The selection applies immediately and is saved for future restarts when local state or Render API persistence is configured. Environment variables remain available for initial setup.
This still uses the OpenAI API for ordinary as well as advanced replies; GitHub hosts
Shadow's code and does not provide its own language model. API usage is billed
separately from ChatGPT subscriptions. Deterministic arithmetic checks and Office
file building/reading run in Shadow's code.

## Telegram voice notes

In approved chats, voice notes up to 3 minutes and 10 MB are transcribed and answered
with an AI-generated Telegram voice note; its caption also includes the written answer.
Transcription asks for Uzbek Latin script while preserving informal speech and mixed
language words. OpenAI's current transcription guidance supports language hints, but
accuracy still depends on audio quality and documented language coverage.
If speech generation is unavailable, Shadow falls back to its normal text reply.
OpenAI's TTS voices are optimized for English, so natural Uzbek pronunciation cannot
be guaranteed. Transcription and speech generation use the configured OpenAI API key
and may incur additional API usage.

## Mathematics, Excel and Word skills

Shadow loads task guidance from `shadow/skills/math.md`, `excel.md` and `word.md`.
Arithmetic tools validate expressions without executing Python. The assistant explains
equations and word problems and can check arithmetic, percentages, roots and trigonometry
(trigonometric calculator arguments are radians).

Send a DOCX or XLSX attachment to an approved chat with your instructions, or request a new file:
- "Masalani bosqichma-bosqich yech: ..."
- "Oylik budjetimni Excel fayl qilib ber: ..."
- Send XLSX with "Jadvalni tahlil qil, xatolarini top."
- Send DOCX with "Matnni tahrir qilib, yangi Word nusxa tayyorla."

Attachments without captions receive a summary. In groups the existing mention/reply
policy still applies. File contents are sent to the configured OpenAI API for analysis.
Only selected chats with replies enabled are processed. Permissions are checked again
before returning text or files.

Limits: uploads up to 10 MB, decompressed Office contents up to 40 MB; XLSX previews
include at most 5 sheets, 200 rows and 30 columns per sheet within a 24,000-character
budget. Truncation is disclosed. DOCX previews extract paragraphs and tables, without
images or original layout. Legacy DOC/XLS and macro formats are not supported.
Edits produce a NEW document, not a lossless edit of the original. Formula generation
uses a limited set of local Excel formulas; results recalculate in Excel, not on the server.
Generated files are capped at 3 per request. XLSX creation supports up to 5 sheets,
500 rows and 30 columns per sheet. Temporary input/output files are deleted after processing.

Local update: `git pull`, `python -m pip install -r requirements.txt`, then restart
`python scripts/run_local.py`. Python tests: `python -m unittest discover -s tests`.


## Per-chat memory, notes, and routines

The dashboard's **Suhbatlar** page lets you add a separate response style, owner-written memory, notes, and routine context for each approved chat. These are explicit profile fields; Shadow does not save full chat transcripts as long-term memory. Profiles are used only in that chat, can be edited or erased from the chat's **Maxsus xotira** control, and are erased when its reply approval is removed. They are stored in the configured private local state file or Render environment settings. Without persistence configured, edits apply only until the current process restarts. Routine notes are contextual only; they do not trigger scheduled reminders. Shadow can help with math, writing, explanations, planning, coding guidance, and its connected Word/Excel features, but does not claim unavailable browsing or code execution.
