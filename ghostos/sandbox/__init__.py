from __future__ import annotations

from ghostos.sandbox.base import ExecResult, Limits, Sandbox
from ghostos.sandbox.docker import DockerSandbox
from ghostos.sandbox.process import ProcessSandbox

__all__ = ["DockerSandbox", "ExecResult", "Limits", "ProcessSandbox", "Sandbox", "select_sandbox"]


async def select_sandbox(preference: str = "auto") -> Sandbox:
    """Pick Docker when requested or available (daemon up + image built), else the process sandbox."""
    if preference == "process":
        return ProcessSandbox()
    if preference == "docker" or await DockerSandbox.available():
        return DockerSandbox()
    return ProcessSandbox()
