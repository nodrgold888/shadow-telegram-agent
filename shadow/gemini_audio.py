"""Voice message transcription through Gemini (fallback when OpenAI is limited or not configured)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .config import Settings
from .image_gen import GEMINI_HOST

log = logging.getLogger("shadow.gemini_audio")

DEFAULT_AUDIO_MODEL = "gemini-flash-lite-latest"
AUDIO_TIMEOUT = 45.0
MAX_AUDIO_BYTES = 10 * 1024 * 1024
PROMPT = (
    "Bu Telegram ovozli xabari. Nutqni so‘zma-so‘z matnga aylantiring. Agar nutq o‘zbekcha bo‘lsa, "
    "mazmunni o‘zgartirmay o‘zbek lotin yozuvida, tabiiy imlo va tinish belgilari bilan yozing. "
    "Sheva, og‘zaki ibora va ruscha/inglizcha aralash so‘zlarni saqlang; eshitilmagan so‘zlarni qo‘shmang. "
    "Boshqa tilda gapirilsa, o‘sha tilda yozing, tarjima qilmang. Faqat matnning o‘zini qaytaring, izoh qo‘shmang."
)


def gemini_models(settings: Settings) -> list[str]:
    """Models of the Gemini backup providers (in chain order), then the default audio-capable one."""
    models = [p.model for p in settings.backup_providers if GEMINI_HOST in p.base_url]
    if DEFAULT_AUDIO_MODEL not in models:
        models.append(DEFAULT_AUDIO_MODEL)
    return models


def _transcribe_sync(api_key: str, model: str, data: bytes, mime: str) -> str:
    from google import genai  # imported lazily: only needed for voice fallback
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model, contents=[types.Part.from_bytes(data=data, mime_type=mime), PROMPT],
    )
    return (response.text or "").strip()


async def transcribe(api_key: str, models: list[str], path: Path, mime: str = "audio/ogg", call=_transcribe_sync) -> str:
    """First non-empty transcript from the given models. Raises the last error if every model failed;
    returns '' if the models answered but heard nothing."""
    data = await asyncio.to_thread(path.read_bytes)
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("Ovozli xabar juda katta")
    last: Exception | None = None
    heard_nothing = False
    for model in models:
        try:
            text = await asyncio.wait_for(asyncio.to_thread(call, api_key, model, data, mime), AUDIO_TIMEOUT)
        except Exception as exc:  # log only the class: SDK messages can echo request details
            last = exc
            log.warning("Gemini transcription with %s failed (%s)", model, type(exc).__name__)
            continue
        if text:
            return text
        heard_nothing = True
    if last is not None and not heard_nothing:
        raise last
    return ""
