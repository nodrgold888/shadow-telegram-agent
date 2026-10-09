"""Image generation plugin adapter for the existing Gemini image service."""
from __future__ import annotations

from pathlib import Path

from ..config import Settings
from ..image_gen import generate_image, gemini_api_key

TOOL = {
    "type": "function",
    "name": "generate_image",
    "description": "Generate an image from the user's explicit image request. Use only when they ask to create or draw an image.",
    "parameters": {
        "type": "object",
        "properties": {"prompt": {"type": "string"}},
        "required": ["prompt"],
        "additionalProperties": False,
    },
    "strict": True,
}


async def run(settings: Settings, prompt: str) -> tuple[bytes, str]:
    """Generate an image using the configured Gemini key; caller controls who can invoke it."""
    return await generate_image(gemini_api_key(settings), prompt)


async def save(settings: Settings, prompt: str, directory: Path, index: int) -> Path:
    data, mime = await run(settings, prompt)
    extension = ".jpg" if "jpeg" in mime or "jpg" in mime else ".png"
    path = directory / f"shadow-image-{index}{extension}"
    path.write_bytes(data)
    return path
