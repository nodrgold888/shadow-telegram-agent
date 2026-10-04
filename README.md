# Shadow

Shadow is a private, always-on Telegram assistant for one personal account. It replies in Uzbek inside approved chats and groups, using the OpenAI Responses API.

New deployments start in read-only connection mode with `REPLY_ENABLED=false`. In this mode Shadow connects to Telegram but does not register a message handler, mark messages read, show typing activity, or send replies.

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
