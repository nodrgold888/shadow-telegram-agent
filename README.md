# Shadow

Shadow is a private, always-on Telegram assistant for one personal account. It replies in Uzbek inside approved chats and groups, using the OpenAI Responses API.

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
- `TELEGRAM_SESSION`
- `OPENAI_API_KEY`
- `APPROVED_CHAT_IDS`

The public `/healthz` route reports connection state without exposing secrets. `/admin/status` requires the generated `ADMIN_TOKEN` bearer token.

## Local development

```bash
cp .env.example .env
set -a && source .env && set +a
uvicorn shadow.app:app --reload --port 10000
```

Open <http://localhost:10000/healthz>.
