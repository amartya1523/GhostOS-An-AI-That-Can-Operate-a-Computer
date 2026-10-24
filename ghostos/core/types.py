"""Core data model shared by every GhostOS agent.

The model is deliberately small: a *Task* becomes a *Plan* of *Steps*; each step
is executed as an *Action* against a tool and yields an *Observation*. Everything
an agent does is also published as an *Event*, which is what the dashboard,
the trace files and the memory system consume.
"""

from __future__ import annotations

import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class RiskLevel(StrEnum):
    """How much damage an action can do. Ordered from harmless to irreversible."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"
    IRREVERSIBLE = "irreversible"

    @property
    def rank(self) -> int:
        return list(RiskLevel).index(self)


class Expectation(BaseModel):
    """A checkable post-condition the Verifier evaluates after a step runs."""

    kind: str  # text_visible | url_contains | exit_code | tests_pass | none
    value: Any = None


class Step(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    intent: str  # human-readable "why" of the step
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    expect: Expectation | None = None


class Plan(BaseModel):
    goal: str
    steps: list[Step]
    rationale: str = ""


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    HEALED = "healed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Observation(BaseModel):
    ok: bool
    output: str = ""
    error: str | None = None
    error_kind: str | None = None  # locator_not_found | timeout | assertion | denied | crash
    data: dict[str, Any] = Field(default_factory=dict)
    screenshot: str | None = None  # artifact path relative to the run dir


class StepRecord(BaseModel):
    step: Step
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    observations: list[Observation] = Field(default_factory=list)

    @property
    def last(self) -> Observation | None:
        return self.observations[-1] if self.observations else None


class RunStatus(StrEnum):
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Event(BaseModel):
    run_id: str
    type: str  # e.g. node.enter, step.start, heal.success, approval.request
    agent: str = "system"
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)
