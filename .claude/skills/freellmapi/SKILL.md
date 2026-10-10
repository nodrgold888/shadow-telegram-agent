---
name: freellmapi
description: Configure and troubleshoot the protected FreeLLMAPI free-provider gateway for Shadow, including Render Docker deployment, persistent storage, approved endpoints, per-account keys and automatic fallback.
---

# FreeLLMAPI with Shadow

[FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi) (MIT) is a single-owner Node/SQLite router for free provider tiers. It exposes OpenAI-compatible chat completions, tools and streaming. Shadow's preferred alias is `auto:smart`; upstream routing can switch models and keys after quota or server failures. It does not bypass paid billing or guarantee continuous availability.

Read `integrations/freellmapi/README.md` for the current connection and deployment procedure. Shadow supports twelve backup slots. In default free mode, a gateway must exactly match `SHADOW_FREE_GATEWAY_BASE_URL`, and its model must be `auto`, `auto:fast` or `auto:smart`. Keep only free upstream routes enabled. Each Telegram account saves its own provider slot and unified key; the gateway itself pools the owner's upstream quotas.

## Deployment

Use `integrations/freellmapi/render.yaml` as a separate Render Blueprint, not the root Shadow Blueprint. The paid Starter service includes a 1 GB persistent disk and generated secrets. The pinned upstream image runs privately on loopback behind `gateway.mjs`; only `/healthz` is public. API requests require `X-Shadow-Gateway-Key` plus the upstream unified Bearer key. Browser access uses username `shadow` and `GATEWAY_ADMIN_PASSWORD`, followed by the upstream admin login. The wrapper bootstraps the initial admin and preserves the existing account on restart.

The built-in Playground uses the signed browser access cookie and the unified key instead of Shadow's edge header. Upstream validates the actual key; a dashboard session token alone cannot authorize inference. External API clients still require the edge header.

Set `SHADOW_FREE_GATEWAY_ACCESS_KEY` on Shadow to the gateway's `GATEWAY_ACCESS_KEY`; the extra header is sent only to the approved endpoint. Never log keys. Preserve and securely back up `ENCRYPTION_KEY` and the disk: the encrypted provider keys cannot be recovered with a different encryption key.

The local Compose option still binds to loopback. Upstream must not be exposed directly to the public internet; use HTTPS and an authenticated proxy. Read installers before executing them; do not pipe unreviewed downloads into a shell.

## Checks

Run the Node gateway tests and full Shadow pytest suite. Verify actual Docker health, protected routes, browser dashboard login and saved-key persistence after restart. Real inference requires configured free provider credentials; report missing credentials or hosting access honestly. Use the dashboard's model check for inference validation.

401 `gateway_access_required` means the edge key is missing or wrong. An upstream 401 means the unified key is wrong. A 503 health response means the private upstream is unavailable. Quota exhaustion means another independent free provider or a quota reset is required.
