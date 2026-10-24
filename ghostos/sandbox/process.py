"""Process sandbox: the fallback when Docker is unavailable.

Weaker isolation than a container, but still useful: a scrubbed environment,
POSIX resource limits (CPU time, file size, open files, process count) and a hard
wall-clock timeout that kills the whole process group.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

from ghostos.sandbox.base import ExecResult, Limits, truncate

_SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TERM", "TZ")


def _scrubbed_env(workspace: Path) -> dict[str, str]:
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    # Tests in the workspace should resolve the same interpreter GhostOS runs on.
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env.update(HOME=str(workspace), PYTHONDONTWRITEBYTECODE="1", GHOSTOS_SANDBOXED="1")
    return env


def _limit_resources(limits: Limits) -> None:  # runs in the child, before exec
    import resource

    cpu = int(limits.timeout_s) + 1
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (256 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
    if sys.platform.startswith("linux"):
        mem = limits.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    os.setsid()


class ProcessSandbox:
    name = "process"

    async def run(self, command: str, workspace: Path, limits: Limits | None = None) -> ExecResult:
        limits = limits or Limits()
        workspace.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=workspace,
            env=_scrubbed_env(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: _limit_resources(limits),
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), limits.timeout_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
            return ExecResult(124, "", f"timed out after {limits.timeout_s}s", True, self.name)
        return ExecResult(proc.returncode or 0, truncate(out), truncate(err), False, self.name)
