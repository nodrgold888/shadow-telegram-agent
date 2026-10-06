#!/usr/bin/env bash
# Starts FreeLLMAPI with Docker Compose. Safe to re-run: an existing .env (and so the
# encryption key) is kept. Requires docker (with the compose plugin) and openssl.
set -euo pipefail
cd "$(dirname "$0")"

for tool in docker openssl; do
  command -v "$tool" >/dev/null 2>&1 || { echo "Missing required tool: $tool" >&2; exit 1; }
done
docker compose version >/dev/null 2>&1 || { echo "Docker Compose plugin not found" >&2; exit 1; }

if [ ! -f .env ]; then
  umask 077
  printf 'ENCRYPTION_KEY=%s\nPORT=3001\n' "$(openssl rand -hex 32)" > .env
  echo "Created .env with a new ENCRYPTION_KEY (back this file up: it protects the stored provider keys)."
fi

docker compose up -d

port="$(grep -E '^PORT=' .env | cut -d= -f2)"
port="${port:-3001}"
echo "Waiting for FreeLLMAPI on http://127.0.0.1:${port} ..."
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${port}/api/ping" >/dev/null 2>&1; then
    cat <<MSG

FreeLLMAPI is running: http://127.0.0.1:${port}
Next:
  1. Open it, set the dashboard password, add free provider keys on the Keys page.
  2. Copy the unified API key from the Keys page header.
  3. Give Shadow (Render -> Environment):
       AI_BASE_URL=https://<your-https-address>/v1
       AI_API_KEY=<the unified key>
       AI_MODEL=auto
See integrations/freellmapi/README.md (HTTPS and security notes).
MSG
    exit 0
  fi
  sleep 2
done
echo "FreeLLMAPI did not answer in time. Check: docker compose logs freellmapi" >&2
exit 1
