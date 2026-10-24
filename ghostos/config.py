"""Runtime settings, read from environment variables prefixed with ``GHOSTOS_``."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


def _env(name: str, default: str) -> str:
    return os.environ.get(f"GHOSTOS_{name}", default)


class Settings(BaseModel):
    home: Path = Field(default_factory=lambda: Path(_env("HOME", ".ghostos")).resolve())
    provider: str = Field(default_factory=lambda: _env("PROVIDER", "auto"))
    model: str = Field(default_factory=lambda: _env("MODEL", ""))
    sandbox: str = Field(default_factory=lambda: _env("SANDBOX", "auto"))  # auto | docker | process
    policy_file: Path | None = Field(
        default_factory=lambda: Path(p) if (p := os.environ.get("GHOSTOS_POLICY")) else None
    )
    headless: bool = Field(default_factory=lambda: _env("HEADLESS", "1") != "0")
    max_heal_attempts: int = 3
    max_replans: int = 2
    # Pause between steps so humans can follow along (demos, recordings). Like Playwright's slowMo.
    step_delay_ms: int = Field(default_factory=lambda: int(_env("STEP_DELAY_MS", "0")))

    @field_validator("home")
    @classmethod
    def _absolute_home(cls, v: Path) -> Path:
        # Resolve symlinks (e.g. macOS /var -> /private/var) so containment checks are exact.
        return Path(v).expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return self.home / "memory.sqlite3"

    @property
    def runs_dir(self) -> Path:
        return self.home / "runs"

    def ensure_dirs(self) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
