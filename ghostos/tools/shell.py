"""Shell and filesystem tools. Everything runs inside the sandbox, scoped to the workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ghostos.core.types import Observation, RiskLevel
from ghostos.safety.shell_guard import classify
from ghostos.sandbox.base import Limits
from ghostos.tools.base import Tool, ToolContext


class ShellArgs(BaseModel):
    command: str
    timeout_s: float = 120
    network: bool = False


class Shell(Tool[ShellArgs]):
    name = "shell.run"
    description = "Run a shell command in the sandboxed workspace."
    risk = RiskLevel.EXECUTE
    Args = ShellArgs

    def risk_for(self, args: ShellArgs) -> RiskLevel:
        verdict = classify(args.command)
        if args.network and verdict.risk.rank < RiskLevel.NETWORK.rank:
            return RiskLevel.NETWORK
        return verdict.risk

    def summarize(self, args: ShellArgs) -> str:
        return f"$ {args.command}"

    async def run(self, args: ShellArgs, ctx: ToolContext) -> Observation:
        verdict = classify(args.command)
        if verdict.blocked:
            return Observation(
                ok=False, error=f"blocked: {', '.join(verdict.reasons)}", error_kind="denied"
            )
        result = await ctx.sandbox.run(
            args.command, ctx.workspace, Limits(timeout_s=args.timeout_s, network=args.network)
        )
        ctx.emit(
            "shell.output",
            f"$ {args.command} → exit {result.exit_code}",
            agent="operator",
            output=result.summary(2000),
            backend=result.backend,
        )
        return Observation(
            ok=result.ok,
            output=result.summary(),
            error=None if result.ok else f"exit code {result.exit_code}",
            error_kind=None if result.ok else ("timeout" if result.timed_out else "exit_code"),
            data={"exit_code": result.exit_code, "backend": result.backend},
        )


def resolve_in(workspace: Path, rel: str) -> Path:
    """Resolve a path and refuse anything that escapes the workspace (../, symlinks, absolute)."""
    root = workspace.resolve()
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise PermissionError(f"path '{rel}' escapes the workspace")
    return target


class ReadFileArgs(BaseModel):
    path: str
    max_chars: int = 20_000


class ReadFile(Tool[ReadFileArgs]):
    name = "files.read"
    description = "Read a text file from the workspace."
    risk = RiskLevel.READ
    Args = ReadFileArgs

    async def run(self, args: ReadFileArgs, ctx: ToolContext) -> Observation:
        try:
            path = resolve_in(ctx.workspace, args.path)
            return Observation(ok=True, output=path.read_text(errors="replace")[: args.max_chars])
        except (OSError, PermissionError) as exc:
            return Observation(ok=False, error=str(exc), error_kind="io")


class WriteFileArgs(BaseModel):
    path: str
    content: str = Field(description="Full new content of the file")


class WriteFile(Tool[WriteFileArgs]):
    name = "files.write"
    description = "Create or overwrite a text file in the workspace."
    risk = RiskLevel.WRITE
    Args = WriteFileArgs

    async def run(self, args: WriteFileArgs, ctx: ToolContext) -> Observation:
        try:
            path = resolve_in(ctx.workspace, args.path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.content)
            return Observation(ok=True, output=f"wrote {len(args.content)} chars to {args.path}")
        except (OSError, PermissionError) as exc:
            return Observation(ok=False, error=str(exc), error_kind="io")


SHELL_TOOLS: list[Tool[Any]] = [Shell(), ReadFile(), WriteFile()]
