"""Owner-only image generation through Gemini (`/rasm <tavsif>` in Saved Messages)."""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re

from .config import Settings

log = logging.getLogger("shadow.image_gen")

GEMINI_HOST = "generativelanguage.googleapis.com"
DEFAULT_IMAGE_MODEL = "gemini-nano-banana-2.1"
MAX_PROMPT_CHARS = 1000
IMAGE_TIMEOUT = 90.0
_COMMAND = re.compile(r"^/rasm(?:@\w+)?(?:\s+(.*))?$", re.S | re.I)
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


class ImageGenError(Exception):
    """The message is Uzbek and safe to show to the owner (never contains keys or prompts)."""


def parse_image_command(text: str) -> str | None:
    """'/rasm cat on a moon' -> 'cat on a moon'; bare '/rasm' -> ''; anything else -> None."""
    match = _COMMAND.match((text or "").strip())
    if not match:
        return None
    return (match.group(1) or "").strip()


def gemini_api_key(settings: Settings) -> str:
    """Reuse the key of a Gemini backup provider, else GEMINI_API_KEY."""
    for provider in settings.backup_providers:
        if GEMINI_HOST in provider.base_url:
            return provider.api_key
    return os.getenv("GEMINI_API_KEY", "").strip()


def image_model() -> str:
    model = os.getenv("IMAGE_MODEL", "").strip()
    return model if _MODEL_ID.match(model) else DEFAULT_IMAGE_MODEL


def _as_bytes(data) -> bytes:
    if isinstance(data, bytes):
        return data if data[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1") else base64.b64decode(data)
    return base64.b64decode(data)


def extract_image(interaction) -> tuple[bytes, str]:
    """Image bytes and mime type from an Interactions API result (convenience property first)."""
    block = getattr(interaction, "output_image", None)
    if block is not None and getattr(block, "data", None):
        return _as_bytes(block.data), getattr(block, "mime_type", None) or "image/png"
    for step in getattr(interaction, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for item in getattr(step, "content", None) or []:
            if getattr(item, "type", None) == "image" and getattr(item, "data", None):
                return _as_bytes(item.data), getattr(item, "mime_type", None) or "image/png"
    raise ImageGenError("Rasm yaratilmadi: model rasm qaytarmadi. Boshqacha tavsif yozib ko‘ring.")


def _create_sync(api_key: str, prompt: str, model: str):
    from google import genai  # imported lazily: only needed when the owner asks for an image

    client = genai.Client(api_key=api_key)
    return client.interactions.create(model=model, input=prompt)


async def generate_image(api_key: str, prompt: str, model: str | None = None, create=_create_sync) -> tuple[bytes, str]:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ImageGenError("Tavsif yozing: /rasm <nima chizish kerak>")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ImageGenError(f"Tavsif juda uzun (eng ko‘pi {MAX_PROMPT_CHARS} belgi).")
    if not api_key:
        raise ImageGenError("Gemini kaliti topilmadi. Panelda ‘AI qo‘shish’ orqali Gemini qo‘shing.")
    try:
        interaction = await asyncio.wait_for(
            asyncio.to_thread(create, api_key, prompt, model or image_model()), IMAGE_TIMEOUT,
        )
    except ImageGenError:
        raise
    except asyncio.TimeoutError:
        raise ImageGenError("Rasm yaratilmadi: Gemini vaqtida javob bermadi.") from None
    except ImportError:
        raise ImageGenError("Rasm yaratilmadi: google-genai kutubxonasi o‘rnatilmagan.") from None
    except Exception as exc:  # log only the class; the SDK message may echo request details
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        log.warning("Image generation failed (%s, %s)", type(exc).__name__, code)
        if code == 429:
            raise ImageGenError("Rasm yaratilmadi: Gemini kvotasi tugagan (rasm uchun billing kerak bo‘lishi mumkin).") from None
        if code in (401, 403):
            raise ImageGenError("Rasm yaratilmadi: Gemini kaliti qabul qilinmadi yoki rasmga ruxsat yo‘q.") from None
        raise ImageGenError(f"Rasm yaratilmadi ({type(exc).__name__}).") from None
    return extract_image(interaction)
