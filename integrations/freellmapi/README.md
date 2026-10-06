# FreeLLMAPI as a backup AI for Shadow

[FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) (MIT) is a self-hosted router that stacks the free
tiers of many LLM providers behind one OpenAI-compatible `/v1` endpoint. Shadow already supports any
OpenAI-compatible backup provider (`AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL`, up to five slots), so
FreeLLMAPI plugs in without any Shadow code change: Shadow calls `POST /v1/chat/completions` with the
unified key and `model: "auto"`, including the calculator / Word / Excel tool calls.

This folder only contains a hardened way to start the upstream image. FreeLLMAPI itself is not vendored.

## Where it can run

FreeLLMAPI needs a machine with Docker and a **persistent disk** (SQLite holds the encrypted provider
keys). That rules out Render's free web service: the disk is wiped on every restart and the router would
have to be reachable from the internet. Use a home PC/server, a small VPS, or any always-on box.

Upstream states that FreeLLMAPI is single-user and "must not be exposed to the internet": it is guarded only
by its unified API key. Shadow on Render has to reach it over the internet, so:

- publish it **only over HTTPS** (Shadow rejects plain `http://` except for localhost), through a tunnel or
  reverse proxy you control (for example Cloudflare Tunnel or Caddy), not by opening the raw port;
- keep the compose file's default loopback binding (`127.0.0.1`) and let the tunnel/proxy connect locally;
- treat the unified key as a password and never paste it into a chat; if it leaks, replace it as described in the upstream docs.

## Start it

```bash
cd integrations/freellmapi
./setup.sh
```

The script creates `.env` with a fresh `ENCRYPTION_KEY` (back it up), starts the container and waits for
`/api/ping`. Then open http://127.0.0.1:3001, set the dashboard password, add free provider keys on the
**Keys** page and copy the **unified API key** from the page header.

## Connect Shadow

In Render -> Environment (use slot 2, 3... if slot 1 is taken):

| Variable | Value |
| --- | --- |
| `AI_BASE_URL` | `https://<your-https-address>/v1` |
| `AI_API_KEY` | the unified key (`freellmapi-...`) |
| `AI_MODEL` | `auto` (or `auto:fast`, `auto:smart`, or a specific model ID) |
| `AI_NAME` | optional label, e.g. `FreeLLM` |

Shadow tries OpenAI first and falls back to the backup providers in slot order; set `AI_PRIMARY=true` to use
them first. The dashboard's "AI ni tekshirish" button tests every provider separately.

## Things to know

- Quality, speed and availability depend on whichever free model the router picks; free tiers change often.
- Chat text leaves Shadow for FreeLLMAPI and then for the free provider it routes to. Do not enable this if
  the chats are too private for those providers.
- Upstream's router refreshes its model catalog from a signed feed on freellmapi.co (a paid tier gets it
  live). Review the upstream README if that matters to you.
- Voice transcription and speech replies in Shadow still use OpenAI.
- Without Docker you can run it from source (`npm run dev` in the upstream repo); see upstream
  `docs/en/install/01-install.md`.
