"""Anthropic Messages API provider (text + vision) over plain httpx."""

from __future__ import annotations

import asyncio
import base64
import os

import httpx

from ghostos.llm.base import LLMError

DEFAULT_MODEL = "claude-sonnet-5-5"


class AnthropicProvider:
    name = "anthropic"
    supports_vision = True

    def __init__(
        self, model: str | None = None, api_key: str | None = None, max_tokens: int = 4096
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.max_tokens = max_tokens
        if not self.api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")

    async def complete(self, system: str, prompt: str, images: list[bytes] | None = None) -> str:
        content: list[dict[str, object]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(img).decode(),
                },
            }
            for img in images or []
        ]
        content.append({"type": "text", "text": prompt})
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        async with httpx.AsyncClient(timeout=120) as client:
            for attempt in range(3):
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages", json=body, headers=headers
                )
                if resp.status_code in (429, 529) or resp.status_code >= 500:
                    await asyncio.sleep(2**attempt)
                    continue
                if resp.status_code != 200:
                    raise LLMError(f"Anthropic API {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                return "".join(
                    b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
                )
        raise LLMError("Anthropic API kept failing after retries")
