"""A deterministic provider that replays scripted replies.

It exists for two reasons: reproducible demos/CI without an API key, and tests that
need to exercise the self-correction loop (e.g. a first patch that is wrong on
purpose). It never pretends to reason — every reply it gives is written in the script file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ghostos.llm.base import LLMError


class ScriptedProvider:
    name = "scripted"
    supports_vision = False

    def __init__(self, rules: list[dict[str, Any]]) -> None:
        # Each rule: {"when": "<regex over the prompt>", "reply": "<text>"}; consumed in order.
        self._rules = [dict(r, used=False) for r in rules]

    @classmethod
    def from_file(cls, path: Path) -> ScriptedProvider:
        return cls(json.loads(path.read_text())["rules"])

    async def complete(self, system: str, prompt: str, images: list[bytes] | None = None) -> str:
        for rule in self._rules:
            if not rule["used"] and re.search(rule["when"], prompt, re.S | re.I):
                rule["used"] = True
                reply = rule["reply"]
                return reply if isinstance(reply, str) else json.dumps(reply)
        raise LLMError(f"scripted provider has no reply for prompt: {prompt[:160]!r}")
