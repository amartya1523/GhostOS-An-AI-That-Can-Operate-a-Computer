"""Docker sandbox: each command runs in a throwaway, locked-down container."""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from pathlib import Path

from ghostos.sandbox.base import ExecResult, Limits, truncate

DEFAULT_IMAGE = os.environ.get("GHOSTOS_SANDBOX_IMAGE", "ghostos/sandbox:latest")


class DockerSandbox:
    name = "docker"

    def __init__(self, image: str = DEFAULT_IMAGE) -> None:
        self.image = image

    @staticmethod
    async def available() -> bool:
        if shutil.which("docker") is None:
            return False
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "info",
            "--format",
            "{{.ServerVersion}}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await proc.wait() == 0

    def build_argv(self, command: str, workspace: Path, limits: Limits) -> list[str]:
        argv = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "bridge" if limits.network else "none",
            "--memory",
            f"{limits.memory_mb}m",
            "--memory-swap",
            f"{limits.memory_mb}m",
            "--cpus",
            str(limits.cpus),
            "--pids-limit",
            str(limits.max_pids),
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "1000:1000",
            "-e",
            "HOME=/tmp",
            "-v",
            f"{workspace.resolve()}:/workspace:rw",
            "-w",
            "/workspace",
            self.image,
            "sh",
            "-c",
            command,
        ]
        return argv

    async def run(self, command: str, workspace: Path, limits: Limits | None = None) -> ExecResult:
        limits = limits or Limits()
        argv = self.build_argv(command, workspace, limits)
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), limits.timeout_s)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return ExecResult(
                124,
                "",
                f"timed out after {limits.timeout_s}s: {shlex.quote(command)}",
                True,
                self.name,
            )
        return ExecResult(proc.returncode or 0, truncate(out), truncate(err), False, self.name)
