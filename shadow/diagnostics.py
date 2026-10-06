from __future__ import annotations

import re

_KEY = re.compile(r"(sk-|rnd_)[A-Za-z0-9_\-*.]+")


def safe_error_detail(exc: BaseException, limit: int = 240) -> str:
    """Short, secret-free description of an upstream error for the owner's dashboard.

    OpenAI errors carry the useful reason ("model does not exist", "quota exceeded").
    API keys and Render keys are masked and the text is truncated; message content is
    never included because only the exception's own message is used.
    """
    message = getattr(exc, "message", None) or str(exc) or type(exc).__name__
    message = _KEY.sub(lambda m: m.group(1) + "…", " ".join(str(message).split()))
    status = getattr(exc, "status_code", None)
    prefix = f"[{status}] " if status else ""
    return (prefix + message)[:limit]
