"""OpenAI-compatible chat completions provider: OpenAI, Ollama, vLLM, Groq, LM Studio…"""

from __future__ import annotations

import asyncio
import base64
import os

import httpx

from ghostos.llm.base import LLMError


class OpenAICompatProvider:
    name = "openai"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        supports_vision: bool = True,
    ) -> None:
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o")
        self.base_url = (
            base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.supports_vision = supports_vision
        if not self.api_key and "api.openai.com" in self.base_url:
            raise LLMError("OPENAI_API_KEY is not set")

    async def complete(self, system: str, prompt: str, images: list[bytes] | None = None) -> str:
        content: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        for img in images or []:
            url = "data:image/png;base64," + base64.b64encode(img).decode()
            content.append({"type": "image_url", "image_url": {"url": url}})
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": 0.1,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=120) as client:
            for attempt in range(3):
                resp = await client.post(
                    f"{self.base_url}/chat/completions", json=body, headers=headers
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    await asyncio.sleep(2**attempt)
                    continue
                if resp.status_code != 200:
                    raise LLMError(f"chat completions {resp.status_code}: {resp.text[:300]}")
                return resp.json()["choices"][0]["message"]["content"] or ""
        raise LLMError("chat completions kept failing after retries")
