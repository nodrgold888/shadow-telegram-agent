"""Small Anthropic Messages adapter exposing the chat-completions shape Shadow already uses."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"


class AnthropicProviderError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(item.get("text", "") for item in value if isinstance(item, dict) and item.get("type") == "text")
    return "" if value is None else str(value)


def _merge_message(messages: list[dict], role: str, blocks: list[dict]) -> None:
    if not blocks:
        return
    if messages and messages[-1]["role"] == role:
        messages[-1]["content"].extend(blocks)
    else:
        messages.append({"role": role, "content": blocks})


def _anthropic_request(messages: list[dict]) -> tuple[str, list[dict]]:
    systems: list[str] = []
    converted: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            text = _text(message.get("content"))
            if text:
                systems.append(text)
        elif role == "tool":
            _merge_message(converted, "user", [{
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id", "")),
                "content": _text(message.get("content")),
            }])
        elif role in {"user", "assistant"}:
            blocks = []
            text = _text(message.get("content"))
            if text:
                blocks.append({"type": "text", "text": text})
            if role == "assistant":
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {}) if isinstance(call, dict) else getattr(call, "function", None)
                    name = function.get("name", "") if isinstance(function, dict) else getattr(function, "name", "")
                    arguments = function.get("arguments", "{}") if isinstance(function, dict) else getattr(function, "arguments", "{}")
                    try:
                        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
                    except (TypeError, json.JSONDecodeError):
                        parsed = {}
                    call_id = call.get("id", "") if isinstance(call, dict) else getattr(call, "id", "")
                    blocks.append({"type": "tool_use", "id": str(call_id), "name": str(name), "input": parsed if isinstance(parsed, dict) else {}})
            _merge_message(converted, role, blocks or [{"type": "text", "text": ""}])
    return "\n\n".join(systems), converted


def _anthropic_tools(tools: list[dict] | None) -> list[dict]:
    result = []
    for item in tools or []:
        function = item.get("function", {}) if isinstance(item, dict) else {}
        if not isinstance(function, dict) or not function.get("name"):
            continue
        schema = function.get("parameters")
        result.append({
            "name": function["name"],
            "description": function.get("description", ""),
            "input_schema": schema if isinstance(schema, dict) else {"type": "object", "properties": {}},
        })
    return result


class _Completions:
    def __init__(self, api_key: str, timeout: float):
        self.api_key = api_key
        self.timeout = timeout

    async def create(self, *, model: str, messages: list[dict], max_tokens: int = 1500, tools: list[dict] | None = None, **_ignored):
        system, transcript = _anthropic_request(messages)
        body: dict = {"model": model, "max_tokens": max_tokens, "messages": transcript}
        if system:
            body["system"] = system
        converted_tools = _anthropic_tools(tools)
        if converted_tools:
            body["tools"] = converted_tools
        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                response = await client.post(
                    ANTHROPIC_BASE_URL + "/messages",
                    headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise AnthropicProviderError(504, "Anthropic request timed out") from exc
        except httpx.HTTPError as exc:
            raise AnthropicProviderError(503, "Anthropic connection failed") from exc
        if response.status_code >= 400:
            raise AnthropicProviderError(response.status_code, f"Anthropic returned HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AnthropicProviderError(502, "Anthropic returned invalid JSON") from exc
        blocks = payload.get("content", []) if isinstance(payload, dict) else []
        text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")
        calls = []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append(SimpleNamespace(
                    id=block.get("id", ""), type="function",
                    function=SimpleNamespace(name=block.get("name", ""), arguments=json.dumps(block.get("input", {}), ensure_ascii=False)),
                ))
        message = SimpleNamespace(role="assistant", content=text or None, tool_calls=calls or None)
        finish_reason = "tool_calls" if calls else "stop"
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


class AnthropicCompatClient:
    """Implements the narrow Chat Completions interface used by Shadow's fallback chain."""

    def __init__(self, api_key: str, timeout: float = 40.0):
        self.chat = SimpleNamespace(completions=_Completions(api_key, timeout))
