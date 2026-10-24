from __future__ import annotations

import os
from pathlib import Path

from ghostos.llm.base import LLMError, LLMProvider, extract_json
from ghostos.llm.scripted import ScriptedProvider

__all__ = ["LLMError", "LLMProvider", "ScriptedProvider", "extract_json", "get_provider"]


def get_provider(
    name: str = "auto", model: str = "", script: Path | None = None
) -> LLMProvider | None:
    """Return a configured provider, or ``None`` for fully offline (rule-based) operation.

    ``auto`` picks Anthropic, then OpenAI(-compatible), based on which keys are present.
    """
    if name == "scripted":
        if script is None:
            raise LLMError("the scripted provider needs a script file")
        return ScriptedProvider.from_file(script)
    if name == "offline":
        return None
    if name in ("anthropic", "auto") and os.environ.get("ANTHROPIC_API_KEY"):
        from ghostos.llm.anthropic import AnthropicProvider

        return AnthropicProvider(model or None)
    if name in ("openai", "auto") and (
        os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_BASE_URL")
    ):
        from ghostos.llm.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(model or None)
    if name == "auto":
        return None
    raise LLMError(f"provider '{name}' is not configured (missing API key?)")
