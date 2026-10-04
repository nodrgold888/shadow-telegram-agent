"""Start Shadow on this computer without Render."""
from __future__ import annotations

import os
from pathlib import Path
import secrets
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    env_file = ROOT / ".env"
    if not env_file.exists():
        env_file.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        env_file.chmod(0o600)
    text = env_file.read_text(encoding="utf-8")
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            raise ValueError("Each .env entry must be KEY=VALUE")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    if not values.get("SETUP_TOKEN") and not os.getenv("SETUP_TOKEN"):
        token = secrets.token_urlsafe(32)
        lines = [line for line in text.splitlines() if line.partition("=")[0].strip() != "SETUP_TOKEN"]
        lines.append("SETUP_TOKEN=" + token)
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        env_file.chmod(0o600)
        values["SETUP_TOKEN"] = token
    for key, value in values.items():
        os.environ.setdefault(key, value)
    os.environ.setdefault("SHADOW_STATE_FILE", str(ROOT / ".shadow-state" / "state.json"))
    print("Shadow dashboard: http://127.0.0.1:10000/")
    print("Telegram login: http://127.0.0.1:10000/setup/telegram")
    print("Dashboard login code: SETUP_TOKEN in your .env file.")
    if not os.getenv("TELEGRAM_API_ID") or not os.getenv("TELEGRAM_API_HASH"):
        print("Fill TELEGRAM_API_ID and TELEGRAM_API_HASH in .env, then restart.")
    import uvicorn
    uvicorn.run("shadow.app:app", host="127.0.0.1", port=10000)


if __name__ == "__main__":
    main()
