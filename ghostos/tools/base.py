"""Tool abstraction: typed arguments, a declared risk level, and a uniform Observation result."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from ghostos.core.types import Observation, RiskLevel

if TYPE_CHECKING:
    from ghostos.core.events import Emitter
    from ghostos.llm.base import LLMProvider
    from ghostos.memory.store import MemoryStore
    from ghostos.sandbox.base import Sandbox
    from ghostos.tools.browser import BrowserSession


@dataclass
class ToolContext:
    run_id: str
    run_dir: Path
    emit: Emitter
    memory: MemoryStore
    sandbox: Sandbox
    llm: LLMProvider | None
    workspace: Path
    browser: BrowserSession | None = None
    scratch: dict[str, Any] = field(default_factory=dict)  # per-run state shared between tools


A = TypeVar("A", bound=BaseModel)


class Tool(ABC, Generic[A]):
    name: ClassVar[str]
    description: ClassVar[str]
    risk: ClassVar[RiskLevel]
    Args: ClassVar[type[BaseModel]]

    def risk_for(self, args: A) -> RiskLevel:
        """Risk can depend on the arguments (e.g. which shell command)."""
        return self.risk

    def summarize(self, args: A) -> str:
        return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in args.model_dump().items())})"

    def url_for(self, args: A) -> str | None:
        return None

    @abstractmethod
    async def run(self, args: A, ctx: ToolContext) -> Observation: ...

    @classmethod
    def schema(cls) -> dict[str, Any]:
        return {
            "name": cls.name,
            "description": cls.description,
            "risk": cls.risk.value,
            "input_schema": cls.Args.model_json_schema(),
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool[Any]] | None = None) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool[Any]) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any]:
        if name not in self._tools:
            raise KeyError(f"unknown tool '{name}'. Known: {sorted(self._tools)}")
        return self._tools[name]

    def parse(self, name: str, raw: dict[str, Any]) -> tuple[Tool[Any], BaseModel]:
        tool = self.get(name)
        try:
            return tool, tool.Args.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"bad arguments for {name}: {exc}") from exc

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return sorted(self._tools)
