# FreeLLMAPI for Shadow

[FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) (MIT) combines free provider quotas behind an OpenAI-compatible endpoint. Shadow uses `auto:smart` to request a stronger available model; FreeLLMAPI handles model/key fallback when an upstream reaches its limit or fails. It does not unlock paid models without credits, and requests can still fail when every enabled provider is unavailable.

This integration uses a pinned upstream Docker image. The wrapper keeps the upstream server on loopback and provides an authenticated public entry point. Shadow supports twelve backup slots and accepts this gateway in its default **Bepul** mode only after the operator configures its exact URL.

## Deploy a separate Render service

1. Open [Render Blueprints](https://dashboard.render.com/blueprints), choose **New Blueprint Instance**, and connect `nodrgold888/shadow-telegram-agent` on `main`.
2. Set the Blueprint path to **`integrations/freellmapi/render.yaml`**. Keep Shadow's existing root Blueprint unchanged.
3. Review and create the `shadow-freellmapi` service. This configuration uses the **paid Starter plan and a 1 GB persistent disk**. Render Free cannot keep the encrypted SQLite database across restarts or provide an always-on service.
4. Copy the new service's public URL from Render. Its `/healthz` endpoint should report `{"ok":true,"gateway":"freellmapi"}`.
5. Open that URL. The browser's access prompt uses username **`shadow`** and the service's generated **`GATEWAY_ADMIN_PASSWORD`**, available under Render Environment.
6. Sign in to the FreeLLMAPI dashboard with **`admin@shadow.local`** and that same password. The initial account is created before the server accepts requests. On later starts, the existing account and routing choices are retained.
7. On **Keys**, add keys from providers' free tiers. On **Models / Fallback**, keep only free routes enabled and order the available models. Do not add paid custom endpoints to this gateway's free pool. Copy the **unified API key** from the API-key section of Keys.

The raw upstream port is not published. External API clients need both the unified API key and an extra gateway access key; dashboard access has a separate password. The built-in Playground uses its signed browser access cookie plus the unified key, which upstream still validates. Render supplies HTTPS. The public health check exposes only availability.

## Connect Shadow

In the **existing Shadow Render service**, add:

| Environment variable | Value |
| --- | --- |
| `SHADOW_FREE_GATEWAY_BASE_URL` | The new gateway URL followed by `/v1`, e.g. `https://your-gateway.onrender.com/v1` |
| `SHADOW_FREE_GATEWAY_ACCESS_KEY` | The gateway service's `GATEWAY_ACCESS_KEY` |

Save and wait for Shadow to restart. For **each Telegram account**:

1. Open **AI provayderlar → AI qoshish → Tayyor shablon → FreeLLMAPI**.
2. The approved Base URL and `auto:smart` model are filled automatically. Paste the gateway's **unified API key**, then click **AI ni qoshish**.
3. Keep **AI ish rejimi → Bepul** and run **Modellarni tekshirish**.
4. Confirm that automatic replies are enabled and the desired chats are approved.

Alternatively, configure an unused `AI_*` slot with `AI_BASE_URL=<approved gateway>/v1`, `AI_API_KEY=<unified key>`, `AI_MODEL=auto:smart`, and `AI_NAME=FreeLLMAPI`. Use suffixes `_2` through `_12` for occupied slots.

Shadow sends the extra gateway header only to the exact configured endpoint, including Development Studio and model-list requests. It never forwards that key to other AI providers. Telegram account settings remain independent; this single-owner gateway pools the owner's upstream quotas and does not provide separate quotas per Telegram account.

## Persistence and maintenance

### All models and stronger choices

Open **AI modellar** in Shadow's menu. The dedicated page includes every endpoint in the bundled, signed FreeLLMAPI catalog: chat, images, audio, embeddings, transcription and video. The version is visible. Eight distinct stronger chat models are highlighted using the catalog's `intelligenceRank` (lower numbers rank higher); this is the publisher's ranking, not a benchmark run by Shadow.

Search by name or ID, filter by provider/type/status, compare context and quota details, or copy an ID. Gateway availability is checked with the signed-in Telegram account's saved unified key. Status applies to the logical model ID; the gateway chooses the provider route. Missing credentials, disabled models and exhausted keys are distinguished from ready entries. An offline gateway leaves the full catalog visible with unverified availability. Media entries are shown as unverified; listing them does not enable Telegram media replies.

Use **Asosiy qilish** on an available chat model to save it as the first model for this account. Shadow rechecks availability before writing, preserves other providers, and keeps an `auto:smart` fallback in a second slot. Repeated changes reuse the same slots. If no fallback slot is free, the selection fails without changing existing settings. **Eng aqlli avto tanlash** returns to automatic smart routing. When no gateway key has been saved yet, **Tanlash** fills the provider form; save the unified key there first.

The catalog comes from the upstream's signed public feed. On a development machine with Node.js, run `node scripts/update_free_catalog.mjs` from the repository root to refresh the snapshot, then run tests and commit it. The updater verifies the Ed25519 signature before writing; CI checks the bundled signature with `node scripts/update_free_catalog.mjs --check`. Refreshing the dashboard updates live gateway status while keeping the visible catalog version explicit.

The disk at `/app/server/data` stores the encrypted provider keys, unified key, dashboard account and routing configuration. **Keep `ENCRYPTION_KEY` unchanged and back it up securely.** The wrapper preserves a 64-character hexadecimal key or derives one from Render's generated secret. Changing it would prevent existing provider keys from being decrypted.

The upstream image is pinned by digest in `Dockerfile`; update it deliberately and repeat the checks below. Hosting, Telegram connectivity and provider quotas remain separate dependencies. No configuration guarantees zero outages.

## Local Docker option

For an existing always-on computer or VPS:

```bash
cd integrations/freellmapi
./setup.sh
```

This existing Compose setup binds the upstream to `127.0.0.1:3001`, creates a persistent Docker volume and saves a generated encryption key in `.env`. Open the local dashboard, create an admin and add free provider keys. For a remote Shadow deployment, place it behind your own authenticated HTTPS proxy before configuring the approved URL. The Render wrapper above includes that extra access layer; its `SHADOW_FREE_GATEWAY_ACCESS_KEY` setting is only for that wrapper or a compatible proxy.

## Troubleshooting and validation

- `/healthz` returns 503: the private upstream is starting or unavailable; inspect Render logs.
- `gateway_access_required`: Shadow's gateway access key is missing or differs from the gateway's key.
- Upstream 401: check the unified API key copied from Keys.
- Free mode reports no eligible AI: check the approved URL and use an auto route or an available chat model from the signed catalog.
- Quota/exhaustion errors: add another independent free provider or wait for its quota reset. Paid model credits cannot be bypassed.

Run `node --test integrations/freellmapi/gateway.test.mjs` and `python -m pytest -q tests` from the repository root. Docker validation should also check initial admin login, dashboard requests with a Bearer session, protected `/v1/models`, and retention of the unified key after restart. A successful model-list request is not proof of inference: add real free-provider keys and run Shadow's model check to verify replies.
