"""Sandbox interface: run a command against a workspace with hard resource limits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    backend: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self, limit: int = 4000) -> str:
        text = (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()
        return text if len(text) <= limit else "…" + text[-limit:]


@dataclass(frozen=True)
class Limits:
    timeout_s: float = 120
    memory_mb: int = 1024
    cpus: float = 1.0
    max_pids: int = 256
    network: bool = False


class Sandbox(Protocol):
    name: str

    async def run(
        self, command: str, workspace: Path, limits: Limits | None = None
    ) -> ExecResult: ...


def truncate(data: bytes, limit: int = 200_000) -> str:
    text = data.decode("utf-8", errors="replace")
    return text if len(text) <= limit else text[:limit] + "\n…[truncated]"
