"""Provider-agnostic LLM interface. GhostOS only needs text-in/text-out (+ optional images)."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str
    supports_vision: bool

    async def complete(
        self, system: str, prompt: str, images: list[bytes] | None = None
    ) -> str: ...


def extract_json(text: str) -> Any:
    """Pull the first JSON object/array out of a model reply (tolerates ```json fences)."""
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    body = fenced.group(1) if fenced else text
    start = min((i for i in (body.find("{"), body.find("[")) if i >= 0), default=-1)
    if start < 0:
        raise LLMError(f"no JSON found in reply: {text[:200]!r}")
    decoder = json.JSONDecoder()
    try:
        value, _ = decoder.raw_decode(body[start:])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON in reply: {exc}") from exc
    return value
