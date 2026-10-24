"""Keep secrets typed into tasks out of memory, traces, events and LLM prompts."""

from __future__ import annotations

import re

_SECRET = re.compile(
    r"(?P<key>\b(?:password|passcode|passwd|pwd|token|api[ _-]?key|secret)\b\s*(?:is|=|:)?\s*)"
    r"(?P<val>[\"“”']?[^\s,\"“”']+[\"“”']?)",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """``log in as ada with password hunter2`` → ``log in as ada with password ••••••``."""
    return _SECRET.sub(lambda m: f"{m.group('key')}••••••", text)
