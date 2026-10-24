"""Declarative permission policy: what the agent may do alone, and what needs a human."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field

from ghostos.core.types import RiskLevel


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Policy(BaseModel):
    # Anything at or below this level runs without asking.
    auto_approve_up_to: RiskLevel = RiskLevel.EXECUTE
    # Anything above this level is refused outright, even with approval.
    hard_limit: RiskLevel = RiskLevel.IRREVERSIBLE
    # Browser navigation outside these hosts needs approval. Empty = no restriction.
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    denied_tools: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None) -> Policy:
        if path is None or not path.exists():
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})

    def decide(self, tool: str, risk: RiskLevel, url: str | None = None) -> tuple[Decision, str]:
        if tool in self.denied_tools:
            return Decision.DENY, f"tool '{tool}' is denied by policy"
        if risk.rank > self.hard_limit.rank:
            return Decision.DENY, f"risk '{risk}' exceeds hard limit '{self.hard_limit}'"
        if url and self.allowed_hosts:
            host = urlparse(url).hostname or ""
            if host not in self.allowed_hosts:
                return Decision.ASK, f"navigation to non-allowlisted host '{host}'"
        if risk.rank <= self.auto_approve_up_to.rank:
            return Decision.ALLOW, f"risk '{risk}' is auto-approved"
        return Decision.ASK, f"risk '{risk}' requires human approval"
