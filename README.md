# Shadow

Shadow is a private Telegram assistant for personal accounts. It can stay connected while its host is running and replies in Uzbek inside approved chats and groups, using the OpenAI Responses API.

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
- Group replies have an independent dashboard switch and default to enabled. In approved groups, replies default to mentions and direct replies to Shadow; set `GROUP_REPLY_MODE=all` only for groups where every message should receive an answer.
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

`render.yaml` defines a free web service in Frankfurt. Free services can sleep, so the keep-alive checks below are best-effort and do not guarantee continuous 24/7 availability. Choose a paid Render instance in the Render dashboard to avoid idle sleep. Paid hosting still needs monitoring and can experience outages. Create a Blueprint from this repository, add the required secret values, and deploy.

Required secrets:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `SETUP_TOKEN`

Open `/setup/telegram` and authenticate with the private setup token. If `RENDER_API_KEY` is configured, the Telegram session is saved to Render and survives service restarts. Without persistence, a restart or free-tier sleep requires another Telegram login.

When you are ready to allow replies, add `OPENAI_API_KEY` and `APPROVED_CHAT_IDS`, then explicitly set `REPLY_ENABLED=true`.

The public `/healthz` route reports connection state without exposing secrets. `/admin/status` requires the generated `ADMIN_TOKEN` bearer token.

The private `/dashboard` page is the Uzbek control panel. Sign in with `SETUP_TOKEN` or `ADMIN_TOKEN` to see Telegram connectivity, the connected account, and a read-only list of recent dialogs. The browser sends its login token through a secure cookie and an in-memory authorization header, so the dashboard remains usable in mobile browsers that do not retain cookies. Its reply switch starts off. Turning replies on requires an explicit confirmation and configured `OPENAI_API_KEY` plus `APPROVED_CHAT_IDS`.

## Local development

```bash
cp .env.example .env
set -a && source .env && set +a
uvicorn shadow.app:app --reload --port 10000
```

Open <http://localhost:10000/healthz>.

## Signing in to the dashboard

`/dashboard` accepts either token (`ADMIN_TOKEN` or `SETUP_TOKEN`) or a **Telegram login code**. The login screen lists saved Telegram account nicknames; choose one before requesting a code. Shadow sends the one-time code to that account’s own Saved Messages, then switches the active account after the code is verified. The code expires after 5 minutes, allows 5 wrong guesses (then 15 minutes lockout), and can be requested once per minute. Sessions last 12 hours and live in memory, so a restart signs you out. Add another account from the login page or dashboard → Akkauntlar.

Each Telegram account has its own approved chats, chat memory, reply and group rules, model selection, AI provider credentials, and account activity. Switching accounts reloads the dashboard for that account; adding an account does not switch the active one.

## When does Shadow reply?

Shadow answers only when Telegram is connected, the dashboard reply switch is on, `OPENAI_API_KEY` is set, and the chat is approved (dashboard → chats). The separate group-reply switch must also be on for groups. In approved private chats it answers every message; in approved groups it answers only when mentioned or replied to, unless `GROUP_REPLY_MODE=all`. Unapproved chats are ignored.

Replies are written like a real person texting (`shadow/skills/human_chat.md`): short, in the other person's language, script and register, no assistant boilerplate. A reply can be sent as up to three consecutive short messages (the model separates them with a `||` line), with a brief "read" pause and a typing pause before each one. Shadow never claims to be human and says it is Shadow AI if someone sincerely asks.

### Instagram / TikTok video download

In an approved chat, a public Instagram (reel, post, IGTV, also `instagram.com/<user>/reel/...`) or TikTok link is downloaded and sent back as a video, even in groups and without a mention. Limits: public videos only (no private accounts, stories or photo posts), up to 50 MB. Private or login-protected links get a specific message (`shadow/skills/video_download.md`).

## Answering bank questions from people who are not approved (optional)

Off by default. Set `PUBLIC_BANK_REPLY=true` (Render → Environment) and keep the dashboard reply switch on to let Shadow answer bank and payment questions from people who message the account privately but are **not** in the approved list. Guard rails (`shadow/public_bank.py`):

- Private chats only, plain text only; never groups, channels, bots, media or Telegram's service account.
- Only bank/payment topics (keyword gate, plus follow-ups for 30 minutes after an answer). Other messages get no reply at all.
- The first answer to each person says it is Shadow AI, the owner's automatic assistant, not a human, and how to stop it. Writing "stop" mutes that chat until the next restart.
- Rate limits: 6 answers per chat per hour and 60 per hour overall.
- Answers use no tools and no private chat memory, never reveal anything about the owner, never ask for PIN/CVV/SMS codes and never invent rates; the model is told to point to the bank's official channels for exact terms.

This does send messages from your personal account to people you have not approved, so enable it deliberately. Counters appear as `public_bank_reply` in `/admin/status`.

## AI models

The dashboard (Sozlamalar → AI modeli) lets you pick one model for everyday chat and one for complex tasks and files: `GPT-5 mini`, `GPT-6 Luna`, `GPT-5.6 Luna` (`gpt-5.6-luna`). The `GPT-5.6 Luna` ID was derived from the name shown in the OpenAI app and is **not confirmed** (it can answer with a quota error on accounts without credits); `gpt-reserve` was tried and the API answers 404, so it is not offered. After choosing a model, press "AI ni tekshirish"; a wrong ID shows up immediately as a model-not-found error and you can switch back. To offer another exact API model ID without a code change, set `EXTRA_OPENAI_MODELS` (comma separated, e.g. `gpt-x,gpt-y`). Model requests are billed to the OpenAI account of `OPENAI_API_KEY`; with no credits every reply fails with `insufficient_quota`.

## Backup AI provider (when OpenAI has no credits)

**Local model option:** [Run Shadow with Ollama](docs/local_ai.md) on an always-on computer or VPS. The local Compose stack keeps the model and Shadow state on persistent volumes and supplies a server-managed fallback to every Telegram account. Render Free cannot run that stack continuously. If Shadow stays on Render, the model needs its own always-on host and an authenticated HTTPS gateway.

**Cloud fallback without a local host:** The dashboard's xKiro preset fills `https://api.xkiro.com/v1`. Create an API key at [xKiro API Keys](https://xkiro.com/dashboard/api/keys), load the model list, and choose a `:free` model if you want to use its daily free allowance. xKiro is an external service with usage limits; it does not keep a sleeping Render instance online or guarantee uninterrupted replies.

The four-model xKiro setup is completed automatically on account activation when that account already has an xKiro key. It reuses the saved key only within that account, preserves existing provider keys and choices, and adds missing models once. Later explicit removals remain removed. There are 12 private provider slots so older backups can remain configured. Free-only is the default mode. A one-time account migration enables it for existing accounts; later explicit mode choices remain in place. To add or rotate a key, open **AI provayderlar → xKiro tayyor toplam**, paste one xKiro API key and save. It adds Qwen3.8 Max Free, Claude Sonnet 5, GPT-6.1 Sol and Claude Opus 5.5 together, with Qwen first by default. Saving again updates the keys in the same slots; other providers remain available. The three paid models need usable xKiro billing. Sonnet 5.5 is not currently offered. Credentials stay with the active Telegram account, and the complete account bundle is persisted in one update.

**AI ish rejimi:** Free-only mode allows only xKiro `:free`, OpenRouter `:free` / `openrouter/free`, the configured local host, and approved FreeLLMAPI auto routes for every chat and development role. It excludes paid clients from replies and diagnostics, including direct OpenAI and Gemini media calls, even if they are configured or selected first. Paid credentials remain saved. A previously saved OpenRouter key automatically supplies an independent `openrouter/free` fallback when there is space and no free OpenRouter route yet; keys stay private to the account. Free quota, outages and model quality still depend on the provider. With no eligible free provider, readiness is false and the panel explains what to configure. Professional mode keeps the chosen model for everyday chat and routes xKiro complex replies to GPT-6.1 Sol. Development Studio uses Qwen for source selection, Sonnet for implementation, Opus for analysis, and GPT for review; its timeline records which model actually responded. Economy mode prefers Qwen for all work; Manual mode uses the saved provider order. These modes affect only configured xKiro models and remain private to each Telegram account. Failed providers move behind healthy alternatives temporarily, and each development stage has a bounded wait. Model checks run in parallel with a 30-second limit per request and show each result's latency. These checks make small inference requests: free mode uses free quotas only; paid modes can consume provider credits. The code and orchestration run in Shadow; xKiro inference still requires its external API. An independent provider or an always-on local host is needed to cover an xKiro-wide outage, and Render Free can still sleep.

OpenAI API credits are prepaid and separate from a ChatGPT/Codex plan's usage limits; with no API credits every reply fails with `insufficient_quota`. Shadow can use a second provider that speaks the OpenAI-compatible **Chat Completions** protocol (for example Groq, Google Gemini's OpenAI-compatible endpoint, OpenRouter, DeepSeek, or a self-hosted router). Set all three in Render → Environment:

- `AI_BASE_URL`: the provider's base URL (https only), e.g. `https://openrouter.ai/api/v1`
- `AI_API_KEY`: an API key from that provider
- `AI_MODEL`: the exact model ID from that provider's docs

**Google Gemini.** With a Gemini API key (from Google AI Studio) use Google's OpenAI-compatible endpoint: `AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`, `AI_API_KEY=<your Gemini key>`, `AI_MODEL=<a model ID from Google's docs, e.g. gemini-3.8-flash>`, `AI_NAME=Gemini`. Model names change often, so confirm the ID with the dashboard's "AI ni tekshirish" button. If a provider rejects `max_tokens` Shadow retries the request once without it. Check Google's terms for how free-tier prompts are used before sending private chats there.

**More than one backup provider.** Add up to eleven more by repeating the three values with a numeric suffix: `AI_BASE_URL_2`, `AI_API_KEY_2`, `AI_MODEL_2` (and `_3` .. `_12`). Optionally name any slot with `AI_NAME` / `AI_NAME_2` ... (shown in the dashboard). Providers are tried in order (slot 1, then 2, 3, ...); the first that answers wins and a failing one is skipped for that message. A slot with only some of its three values is ignored. "AI ni tekshirish" tests every provider separately and `/admin/status` lists them under `ai_backup.providers`.

**Free providers via FreeLLMAPI.** [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) aggregates many free LLM tiers behind one OpenAI-compatible endpoint and works as an approved free gateway (`AI_MODEL=auto:smart`). A hardened start script and the security notes (it needs Docker and a persistent disk, so it cannot run on Render's free plan, and must only be exposed over HTTPS) are in [`integrations/freellmapi/`](integrations/freellmapi/README.md).

In paid modes, the backup is used automatically when OpenAI fails with no credits / rate limit (429), a bad key (401/402/403), a server error or a connection error; other errors (e.g. 400) are shown, not hidden. Set `AI_PRIMARY=true` to use the backup first, and if `OPENAI_API_KEY` is empty it is used on its own. The backup supports the calculator and Word/Excel creation through function calling; if a provider rejects tools, Shadow retries as plain text. Voice-message transcription and speech replies need supported media providers and are disabled in free-only mode. Chat text sent to the AI goes to the provider you configure, so choose one you trust with that content. The dashboard's "AI ni tekshirish" button tests OpenAI and the backup separately.

### Adding an AI from the dashboard

Settings -> "AI qo'shish" adds a backup provider (OpenRouter, Gemini or any OpenAI-compatible API) without opening Render:
name, base URL (templates for OpenRouter and Gemini), model ID and API key. Shadow stores it in the first free `AI_*` slot
(1..12) through the Render API (`RENDER_API_KEY` + `RENDER_SERVICE_ID` must be set; Render then restarts the service) and also
applies it live. The list shows the order (e.g. `Gemini Lite → OpenAI`); "Birinchi qilish" makes a provider the first AI tried (sets `AI_PRIMARY=true` and `AI_FIRST_SLOT=<slot>`), "OpenAI ni birinchi qilish" gives OpenAI the lead back. Each added provider is listed under the form with an "O'chirish" button that frees its slot (variables are removed from Render too). The key is never shown again or logged. Without Render persistence the provider only lives until the next restart.

## Voice messages

Voice messages in approved chats are transcribed with OpenAI (`gpt-transcribe`) and answered like text. When OpenAI is
limited (429), cooling down or not configured, Shadow transcribes with Gemini instead (the models of your Gemini backup
providers, then `gemini-flash-lite-latest`) through the `google-genai` SDK. The answer is a voice note when OpenAI
speech works, otherwise a text reply. Voice messages up to 3 minutes / 10 MB.

## Image generation

Ask Shadow to create an image in an approved chat, or write `/rasm <tavsif>` to yourself in Telegram **Saved Messages**
(Gemini image model, default `gemini-nano-banana-2.1`, override with `IMAGE_MODEL`). The generated image is sent as a photo.
Shadow's own image (whose caption is the prompt) is ignored, so it cannot loop.

- The key is taken from a Gemini backup provider (panel "AI qo'shish", base URL `generativelanguage.googleapis.com`) or
  from `GEMINI_API_KEY`. Image generation usually needs a billing-enabled Google project; a quota error is reported in
  the chat where the request was made.
- One image at a time, prompts up to 1000 characters, 90 s limit. Uses the `google-genai` SDK (`client.interactions`).
- Image generation may use paid Gemini quota. It is only invoked when the user asks for an image.

## Web search and news

The assistant can search and summarize current web pages or news with Google Custom Search or Bing Web Search. Configure these optional Render environment variables:

- Google: `GOOGLE_CSE_API_KEY` and `GOOGLE_CSE_CX` (Programmable Search Engine ID).
- Bing: `BING_SEARCH_API_KEY`.
- `WEB_SEARCH_PROVIDER` is `auto` by default; it uses configured Google first, then Bing as a fallback. Set it to `google` or `bing` to require one provider.

The search tool returns up to five titles, snippets, and source links for the assistant to summarize. Search snippets are treated as untrusted content; private chat history and credentials should not be included in a search query.

## Agents per chat

Each approved chat can have its own agent: in the dashboard open the chat's profile (Suhbatlar → chat → xotira) and pick an **Agent**: Umumiy yordamchi (default), Do'stona suhbatdosh, Bank maslahatchisi, Tarjimon, O'qituvchi or Ish yordamchisi (`shadow/agents.py`). You can also write chat-specific extra instructions (up to 1200 characters, e.g. "always answer in Russian"). The agent is added to the system prompt for that chat only, for both OpenAI and the backup providers, and is stored with the chat profile (so it survives restarts when persistence is configured and is removed when the chat's approval is revoked). Agents only change focus, tone and working style: the honesty rule (Shadow says it is an AI when sincerely asked), the safety rules and chat isolation always apply.

## Always-online status

By default Shadow keeps the connected Telegram account showing as **online** by refreshing its status every ~2 minutes (`shadow/presence.py`). It only changes the visible status; it never reads or sends messages. Turn it off with `ALWAYS_ONLINE=false`. Current state and counters are under `online_presence` in `/admin/status` and the dashboard status.

Things to know: while one session is online, Telegram may stop sending push notifications to your other devices (phone, desktop), so you can miss message alerts. The account also looks online to everyone who can see your status, even when you are away. Staying online 24/7 also needs the service itself to be awake (see Keep-alive ping below; the free Render plan can still sleep).

## Keep-alive ping

On Render's free plan the service sleeps after about 15 minutes without inbound traffic, which interrupts the Telegram connection. Two independent pingers call the lightweight public `/ping` endpoint (it never touches Telegram or the agent):

1. **In-process ping** (`shadow/keepalive.py`). Every 5 minutes (with ±10% jitter) Shadow calls its own public URL. Each ping has a 60s timeout, to survive cold starts, and retries after 5s, 15s and 45s. It starts automatically when `RENDER_EXTERNAL_URL` is set (Render sets it). Override with `KEEPALIVE_URL` and `KEEPALIVE_INTERVAL_SECONDS` (minimum 60). Its counters and last error are in `/admin/status` under `keepalive`.
2. **External ping** (`.github/workflows/keepalive.yml`). GitHub Actions calls `/ping` every 5 minutes from outside. Unlike the in-process ping, it can wake a service that is already asleep or crashed. It targets `https://shadow-telegram-agent.onrender.com` by default; set the optional repository variable `PING_URL` (Settings → Secrets and variables → Actions → Variables) if your service URL differs. It retries up to 4 times with a 90s timeout.

GitHub's scheduler can run a few minutes late, and GitHub disables scheduled workflows after 60 days without repository activity (re-enable in the Actions tab). These pingers reduce idle time but cannot guarantee 24/7 uptime; an always-on host avoids idle sleep but cannot eliminate every outage.

## Surviving restarts (no re-login)

The Telegram session created at `/setup/telegram` would normally live only in memory. To keep it across restarts and redeploys:

1. Create a Render API key (Account Settings → API Keys) and add it as the `RENDER_API_KEY` secret. `RENDER_SERVICE_ID` is provided by Render automatically. This is a service credential, not the dashboard login token.
2. Log in once at `/setup/telegram`. After a successful login Shadow writes the session into the service's `TELEGRAM_SESSION` secret. The setup page confirms which account connected and whether the write succeeded.

The blueprint declares every variable that Shadow updates through the Render API (`TELEGRAM_SESSION`, `APPROVED_CHAT_IDS`, `REPLY_ENABLED`, `SHADOW_MODEL_SELECTION`, and `SHADOW_CHAT_PROFILES`). Keep these variable names unchanged so saved dashboard changes target existing Render variables. Render may restart the service after an environment update; the Telegram session is saved before the restart and the dashboard keeps its login token in the open tab.

The authenticated dashboard and `/admin/status` report `session_persisted` and `can_persist_session`. Public `/healthz` only reports whether the service is up and Telegram is connected. If you prefer, run `scripts/create_session.py` locally and paste `TELEGRAM_SESSION` into Render yourself. The session string is never logged or exposed over HTTP.

To switch to another Telegram account, open `/setup/telegram`, sign in with
that account's phone number, and complete the code and optional two-step
password there. The setup page confirms the connected username and numeric
account ID, and says whether Render saved the new session. The authenticated
dashboard shows the active account. A successful login replaces the active
Telegram client and updates the watchdog's reconnect session; the prior client
is disconnected so it cannot keep processing messages.

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

## Uzbekistan banking guidance

Shadow includes an Uzbek-language reference guide covering loans, deposits, accounts and cards, transfers, currency exchange, utility and other payments, small-business services, consumer rights, and fraud prevention. It is general educational guidance, not a live catalog of every bank's products. Bank rates, tariffs, eligibility, and promotions change and differ by institution; Shadow must ask for the bank/product and rely on a current official source before stating exact terms. The guide was last checked on 2026-10-05 against the Central Bank, LexUZ, and the government portal. A Davr Bank guide maps the bank's public retail and business services, published tariffs, customer-facing offers, and disclosed corporate ethics code; it does not include unpublished internal policies or live product data. Shadow can explain and estimate but cannot make payments, move funds, apply for credit, or access a bank account. Never send card security details, passwords, or verification codes to Shadow.

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

The dashboard's **Suhbatlar** page lets you add a separate response style, owner-written memory, notes, and routine context for each approved chat. These are explicit profile fields; Shadow does not save full chat transcripts as long-term memory. When generating a reply in that chat, the selected profile context is sent to the configured OpenAI API; do not store passwords, login codes, or financial secrets there. Profiles are used only in that chat, can be edited or erased from the chat's **Maxsus xotira** control, and are erased when its reply approval is removed. They are stored in the configured private local state file or Render environment settings. Without persistence configured, edits apply only until the current process restarts. Routine notes are contextual only; they do not trigger scheduled reminders. Shadow can help with math, writing, explanations, planning, coding guidance, and its connected Word/Excel features, but does not claim unavailable browsing or code execution.

### Greeting people who are not approved

Off by default. In the dashboard open **Avtomatik javoblar** and switch on **Notanish chatlar bilan salomlashish** (the reply switch must be on too). Shadow then sends a short greeting to a text message from an unapproved private chat and asks why the person wrote. Guard rails (`shadow/greeting.py`): private chats only, text only, no bots, at most 3 replies per chat per day and 30 per hour overall, "stop" mutes the chat, chats on the friend list are never answered, a bank question goes to the bank answers instead, and Shadow never claims to be a human or the account owner (it says it is Shadow AI when sincerely asked).

### Videos for people who are not approved

Off by default. In **Avtomatik javoblar** switch on **Notanish chatlarga ham video**. A public Instagram or TikTok link from an unapproved private chat is then downloaded and sent back like in approved chats. There is no hourly limit, only two downloads at a time; friend chats, groups and bots are never served, and the reply switch must be on.

### Chat types and skills

Per-chat types (Suhbatlar → "Maxsus xotira" → tur): Umumiy yordamchi, Do'stona suhbatdosh, Bank maslahatchisi, Tarjimon, O'qituvchi, Ish yordamchisi, Kod yordamchisi, Kontent yozuvchi, Taqdimot va hujjat, Savdo va mijozlar bilan. The built-in skills (`shadow/skills/*.md`: general, natural chat, math, Excel, Word, coding, learning, Davr Bank, video) are listed read-only in **Avtomatik javoblar → Suhbat turlari va skillar** (`GET /dashboard/api/agents`).

Several chat types can be combined for one chat (up to 4): tick them in the chat profile dialog (Suhbatlar → "Tur tanlash" / "Turlar: N") or from **Avtomatik javoblar → Suhbat turlari va skillar → Chatlarga tayinlash**. The profile stores them as `agents` (comma-separated ids; the old single `agent` is still read and mirrors the first one).

### 2D / 3D look

The dashboard header has a **2D | 3D** switch. 2D is the flat design; 3D adds depth (raised keys and buttons, extruded cards, a 3D orb in the banner and a slight pointer tilt on cards; the tilt and the orb animation are off when the system asks for reduced motion). The choice is remembered per browser (`localStorage` key `shadow-style`).

### More for people who are not approved

Two more switches in **Avtomatik javoblar → Notanish chatlar** (both off by default, friends are never touched): **Notanishlarning ovozli xabariga javob** transcribes a voice message of up to one minute and answers it briefly like the greeting (5 per chat per day, 20 per hour overall), and **Notanish yozsa menga xabar yuborish** puts a short note in your Saved Messages (who wrote, chat ID and the text; one per chat every 10 minutes, 20 per hour overall). Stored as `VOICE_UNKNOWN` and `NOTIFY_UNKNOWN`.

## Development Studio

The dashboard's **Development Studio** is an owner-operated AI development agent for
Shadow itself. It has two task modes:

- **Audit:** inspect relevant source files and produce evidence-based findings and a plan.
- **Build:** prepare new capabilities, UI improvements, refactors, or repairs as source
  changes in a separate task workspace. Inspect the diff and download an ordinary Git patch.

The agent selects up to six source files, generates bounded exact edits or new files,
checks paths and Python/JSON syntax, and gets one revision attempt when validation fails.
It uses the existing AI provider configuration in a separate request context. Each task
has a five-minute limit; only one generation task can run at a time. Cancelling a task
cancels its outstanding generation request. Source code and a small runtime summary
(connection/reply booleans and reply count) go to the configured provider; Telegram
messages, session files, and environment variables are not read into the agent context.
Do not put secrets in task descriptions or feedback.

Generated code is **not executed or applied to the running service**. The report clearly
separates syntax checks that ran from functional/browser checks the reviewer still needs
to run. Accepted/rejected feedback and owner notes become context for future tasks; this
is preference memory, not model-weight training. The existing repair agent remains
available under Monitoring for live connection and listener repairs.

### Draft GitHub pull requests

To let the studio create update branches and draft pull requests, set these environment
variables on the server:

| Variable | Purpose |
| --- | --- |
| `SHADOW_DEV_GITHUB_TOKEN` | Fine-grained GitHub token for this repository, with Contents and Pull requests read/write permissions. |
| `SHADOW_DEV_GITHUB_REPO` | Target `owner/repository`; defaults to `nodrgold888/shadow-telegram-agent`. |
| `SHADOW_DEV_GITHUB_BRANCH` | Base branch; defaults to `main`. |
| `SHADOW_DEV_MODEL` | Optional OpenAI model override for development tasks. Existing compatible backup providers retain their own models. |
| `SHADOW_DEV_DIR` | Job/feedback workspace path; defaults to `/tmp/shadow-development`. Use a writable persistent volume for durable history. |

The **Draft PR** button verifies that the inspected source still matches GitHub, writes
only a new `shadow/development-…` branch, and opens a draft PR. If source changed, rebuild
against the current deployed version. Retrying a failed publication resumes the same
branch/PR rather than creating duplicates. The studio never merges PRs or pushes to the
base branch. Review and test the PR, then merge it through your normal GitHub workflow;
the existing Render deployment follows changes to `main`.

Without a GitHub token, audit, build, diff review, feedback, and patch downloads still
work. The workspace holds up to 30 tasks; download and remove old tasks to free space.
Local history and preference memory survive process restarts when the directory is
retained, but Render's ephemeral filesystem can be reset by a redeploy. Export patches
or use draft PRs before redeploying; use a persistent disk for durable task history.

### Background agent: Shadow Guardian

Development Studio includes a server-owned supervision loop. It starts with FastAPI,
runs without an open browser, checks Telegram, AI configuration, reply listeners,
recorded reply failures, and the presence/syntax of its core source files every minute.
Source checks are cached for 15 minutes; they are not functional tests. Safe runtime
recovery reuses the connection/listener repair service and never enables replies or
changes chat permissions. The panel polls actual status every five seconds and offers
pause/resume, check-now, 1/5/15-minute intervals, recovery and AI audit controls.

When configured, AI performs a source review at most once every six hours per Telegram
account, using the existing provider chain. This uses API credits. It waits for manual
work to finish and produces an audit in Development Studio; code patches and deployment
remain separate actions. Failed audits consume their scheduled slot to prevent retry
storms. The last five completed automatic audits per account are retained; manual jobs
and patches are not pruned. Settings, counters, events and AI scheduling are isolated
per account. The background loop checks each saved account in its own context; changing the dashboard selection does not stop another account's supervision.

Set `SHADOW_GUARDIAN_ENABLED=false` to default new account schedules to paused. Guardian
stores preferences and a bounded event log in `SHADOW_DEV_DIR/guardian-state.json`;
`SHADOW_GUARDIAN_STATE_FILE` overrides that path. Use a writable persistent disk for
both Guardian and `SHADOW_DEV_DIR` to preserve schedules and audits across deployments.
Pause stops new checks; an already running check or AI job can finish. The normal task
cancel control can cancel an AI audit.

The loop stops when the server process stops. Render Free can sleep and its disk is
ephemeral; existing keep-alive pings cannot guarantee availability. Uninterrupted
24/7 operation requires an always-on host and persistent storage. No paid hosting
upgrade is performed automatically.

## Concurrent Telegram accounts

Every saved Telegram account now has an independent client, reply listener, settings,
chat permissions, memory and recovery task (`shadow/runtime.py`). All saved accounts
start concurrently. Selecting another account changes the dashboard view; it does not
disconnect the previous account. Adding an account preserves existing workers and
starts the new account with replies disabled and an empty private settings bundle.
Telegram panel sessions stay bound to their selected account until the user switches
it explicitly. Deployments still invalidate in-memory panel sessions.

Startup serves health probes without waiting for Telegram. Recovery checks run every
15 seconds with a 20-second connection deadline and failure backoff up to 60 seconds.
A revoked session requires a fresh Telegram login. Each account keeps its own reply
switch; connection recovery never enables replies or grants chat permissions. Login
and account cards show actual socket connection status separately from the account
selected in the panel. These checks do not prove AI inference or 24/7 availability.

Render Free can still sleep, and delayed GitHub scheduled pings cannot prevent every
idle period. For both accounts to remain running overnight, use an always-on Render
instance or VPS and persistent storage. Provider outages, quota and Telegram limits
remain external dependencies.
