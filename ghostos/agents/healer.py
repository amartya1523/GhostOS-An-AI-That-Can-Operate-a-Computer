"""Healer agent: when a step fails, decide *how* to recover — or that recovery is impossible.

UI-level healing (finding a moved button) happens inside the self-healing locator.
This agent handles everything above that: transient failures (retry with backoff),
unmet post-conditions (wait longer), wrong code patches (self-correct with the
verifier's evidence as feedback), and dead ends (replan, or stop and say why).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ghostos.core.types import StepRecord

Action = Literal["retry", "replan", "give_up"]

_UNRECOVERABLE = {"denied", "no_llm"}
_TRANSIENT = {"timeout", "navigation", "action_failed", "http", "locator_not_found"}


@dataclass
class HealDecision:
    action: Action
    reason: str
    new_args: dict[str, Any] = field(default_factory=dict)
    wait_ms: int = 0


class Healer:
    def __init__(self, max_attempts: int = 3, can_replan: bool = False) -> None:
        self.max_attempts = max_attempts
        self.can_replan = can_replan

    def decide(self, record: StepRecord, verdict_reason: str) -> HealDecision:
        obs = record.last
        kind = obs.error_kind if obs else None
        step = record.step

        if kind in _UNRECOVERABLE:
            return HealDecision(
                "give_up", f"cannot recover from '{kind}': {obs.error if obs else ''}"
            )

        if record.attempts >= self.max_attempts:
            if self.can_replan:
                return HealDecision(
                    "replan",
                    f"{record.attempts} attempts failed; asking the planner for a new route",
                )
            return HealDecision(
                "give_up", f"gave up after {record.attempts} attempts: {verdict_reason}"
            )

        if step.tool == "code.fix":
            feedback = [
                *step.args.get("feedback", []),
                f"attempt {record.attempts}: {verdict_reason}",
            ]
            return HealDecision(
                "retry",
                "patch did not make the suite green — feeding the verifier's evidence back to the coder",
                new_args={**step.args, "feedback": feedback},
            )

        if kind == "assertion":
            timeout = int(step.args.get("timeout_ms", 4000)) * 2
            return HealDecision(
                "retry",
                f"post-condition not met yet; waiting up to {timeout} ms",
                new_args={**step.args, "timeout_ms": timeout},
            )

        if kind in _TRANSIENT or kind is None:
            backoff = 300 * 2 ** (record.attempts - 1)
            return HealDecision(
                "retry",
                f"transient '{kind or 'verification'}' failure; retrying in {backoff} ms",
                new_args=dict(step.args),
                wait_ms=backoff,
            )

        return HealDecision("retry", f"retrying after '{kind}'", new_args=dict(step.args))
