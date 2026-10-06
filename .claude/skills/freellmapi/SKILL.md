---
name: freellmapi
description: How FreeLLMAPI (free-tier multi-provider LLM router) is used with the Shadow Telegram agent as a backup AI provider - hosting, HTTPS/security cautions, Render env wiring (AI_BASE_URL/AI_API_KEY/AI_MODEL) and troubleshooting. Use when the user asks about free AI models, backup providers, "insufficient_quota", or FreeLLMAPI.
---

# FreeLLMAPI with Shadow

**What it is.** https://github.com/tashfeenahmed/freellmapi (MIT): a self-hosted Node/SQLite router that
aggregates free LLM tiers behind one OpenAI-compatible API (`/v1/chat/completions`, tool calling,
streaming, model `auto`, `auto:fast`, `auto:smart`). Auth is one "unified key" (`freellmapi-...`).
Health: `GET /api/ping`. Default port 3001. Docker image `ghcr.io/tashfeenahmed/freellmapi`.

**How Shadow uses it.** Not vendored. Shadow's backup provider chain (`shadow/config.py` `AIProvider`,
`shadow/assistant.py` `_try_providers`) talks to any OpenAI-compatible endpoint:
`AI_BASE_URL=https://host/v1`, `AI_API_KEY=<unified key>`, `AI_MODEL=auto` (slots `_2`..`_5` for more).
Backup is tried when OpenAI fails with 429/401/402/403/5xx/connection errors, or first with `AI_PRIMARY=true`.
Calculator/Word/Excel tools work through function calling (providers rejecting tools get a plain-text retry).

**Setup files.** `integrations/freellmapi/` has `docker-compose.yml` (loopback bind, required
`ENCRYPTION_KEY`), `setup.sh` (creates `.env`, starts it, waits for `/api/ping`) and a README.

**Cautions to repeat to the user.**
- It needs Docker plus a persistent disk, so it cannot live on Render's free web service; run it on an
  always-on machine.
- Upstream says it is single-user and must not be exposed to the internet. Shadow on Render must reach it,
  so expose it only over HTTPS through a tunnel/proxy, keep the unified key secret, never paste keys in chat.
- Shadow refuses plain `http://` base URLs except localhost.
- Chat text goes to the free provider the router picks: do not use it for private chats the owner would not
  send to those providers.
- Never run `curl ... | bash` installers on the user's behalf without reading them.

**Troubleshooting.** Use the dashboard "AI ni tekshirish" button (per-provider result, secret-free error
detail). 401 = wrong unified key; 404 = wrong `AI_MODEL` or base URL missing `/v1`; connection errors =
tunnel down or the container stopped (`docker compose logs freellmapi`); empty replies = the routed free
model returned nothing, try `auto:smart`.
