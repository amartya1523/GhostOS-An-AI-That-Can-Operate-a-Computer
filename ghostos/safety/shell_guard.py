"""Static classification of shell commands before they ever reach a sandbox.

This is defence in depth, not the only line: commands also run inside a sandbox.
The guard catches the obviously catastrophic and flags anything that escalates
risk (network, privilege, history rewriting) so the permission gate can ask a human.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from ghostos.core.types import RiskLevel

# Always refused, regardless of policy or approval.
_BLOCKED: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(/|~|\$HOME|\*)(\s|$)", "recursive delete of root/home"),
    (r"\bmkfs(\.\w+)?\b", "filesystem format"),
    (r"\bdd\b.*\bof=/dev/", "raw write to a device"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "power state change"),
    (r"\bchmod\s+-R\s+0?777\s+/(\s|$)", "world-writable root"),
    (r">\s*/dev/sd[a-z]", "raw write to a disk"),
    (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b", "pipe remote script into a shell"),
]

# Allowed only with escalation to the given risk level.
_ESCALATE: list[tuple[str, RiskLevel, str]] = [
    (r"\bsudo\b|\bsu\s", RiskLevel.IRREVERSIBLE, "privilege escalation"),
    (r"\bgit\s+push\b.*(--force|-f\b)", RiskLevel.IRREVERSIBLE, "force push"),
    (r"\bgit\s+push\b", RiskLevel.NETWORK, "pushes to a remote"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)", RiskLevel.IRREVERSIBLE, "discards work"),
    (r"\brm\s+-[a-zA-Z]*r", RiskLevel.IRREVERSIBLE, "recursive delete"),
    (
        r"\b(curl|wget|pip\s+install|npm\s+(i|install)|apt(-get)?|brew)\b",
        RiskLevel.NETWORK,
        "network access",
    ),
    (r"\bssh\b|\bscp\b|\bnc\b", RiskLevel.NETWORK, "remote connection"),
]

_MUTATING_PROGRAMS = {"rm", "mv", "cp", "mkdir", "touch", "sed", "tee", "git", "chmod", "patch"}


@dataclass(frozen=True)
class ShellVerdict:
    blocked: bool
    risk: RiskLevel
    reasons: tuple[str, ...]


def classify(command: str) -> ShellVerdict:
    for pattern, reason in _BLOCKED:
        if re.search(pattern, command):
            return ShellVerdict(True, RiskLevel.IRREVERSIBLE, (reason,))

    risk = RiskLevel.EXECUTE
    reasons: list[str] = []
    for pattern, level, reason in _ESCALATE:
        if re.search(pattern, command) and level.rank >= risk.rank:
            if level.rank > risk.rank:
                reasons.clear()
            risk = level
            reasons.append(reason)

    if risk == RiskLevel.EXECUTE and _is_read_only(command):
        risk = RiskLevel.READ
    return ShellVerdict(False, risk, tuple(reasons))


def _is_read_only(command: str) -> bool:
    if re.search(r"[>;&|`$]", command):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return True
    program = argv[0]
    if program == "git":
        return len(argv) > 1 and argv[1] in {"status", "diff", "log", "show", "branch"}
    readers = {"ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "pwd", "echo", "which"}
    return program in readers and program not in _MUTATING_PROGRAMS
