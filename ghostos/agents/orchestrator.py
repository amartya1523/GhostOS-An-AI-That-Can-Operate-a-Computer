"""The orchestrator wires the agents into a state graph:

┌────────┐   ┌──────────┐   ┌──────────┐  passed  ┌────────┐
│ plan   │──▶│ act      │──▶│ verify   │─────────▶│ finish │
└────────┘   │(operator)│   │(verifier)│ next ┐   └────────┘
    ▲        └──────────┘   └──────────┘      │        ▲
    │             ▲   ▲          │ failed     │        │
    │             │   └──────────┼────────────┘        │
    │   replan    │ retry   ┌────▼─────┐   give up     │
    └─────────────┴─────────│ heal     │───────────────┘
                            │ (healer) │
                            └──────────┘
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from ghostos.agents.healer import Healer
from ghostos.agents.planner import Planner, PlanningError
from ghostos.agents.verifier import Verifier
from ghostos.core.graph import END, StateGraph
from ghostos.core.types import Observation, Plan, RunStatus, StepRecord, StepStatus
from ghostos.safety.gate import PermissionGate
from ghostos.safety.redact import redact
from ghostos.tools.base import ToolContext, ToolRegistry


@dataclass
class RunState:
    task: str
    ctx: ToolContext
    plan: Plan | None = None
    records: list[StepRecord] = field(default_factory=list)
    index: int = 0
    replans: int = 0
    status: RunStatus = RunStatus.PLANNING
    last_verdict: str = ""
    next_after_heal: str = "act"
    summary: str = ""
    healed_steps: int = 0

    @property
    def current(self) -> StepRecord:
        return self.records[self.index]


AGENT_OF_NODE = {
    "plan": "planner",
    "act": "operator",
    "verify": "verifier",
    "heal": "healer",
    "finish": "system",
}


class Orchestrator:
    def __init__(
        self,
        registry: ToolRegistry,
        gate: PermissionGate,
        planner: Planner,
        verifier: Verifier,
        healer: Healer,
        max_replans: int = 2,
        step_delay_ms: int = 0,
    ) -> None:
        self.registry = registry
        self.gate = gate
        self.planner = planner
        self.verifier = verifier
        self.healer = healer
        self.max_replans = max_replans
        self.step_delay_ms = step_delay_ms
        self.graph = self._build()

    def _build(self) -> StateGraph[RunState]:
        g: StateGraph[RunState] = StateGraph()
        g.add_node("plan", self.plan_node)
        g.add_node("act", self.act_node)
        g.add_node("verify", self.verify_node)
        g.add_node("heal", self.heal_node)
        g.add_node("finish", self.finish_node)
        g.set_entry("plan")
        g.add_router("plan", lambda s: "act" if s.plan else "finish", {"act", "finish"})
        g.add_edge("act", "verify")
        g.add_router("verify", self._after_verify, {"act", "heal", "finish"})
        g.add_router("heal", lambda s: s.next_after_heal, {"act", "plan", "finish"})
        g.add_edge("finish", END)
        return g

    async def run(self, state: RunState) -> RunState:
        emit = state.ctx.emit

        def hook(phase: str, node: str, s: RunState) -> None:
            emit(f"node.{phase}", node, agent=AGENT_OF_NODE[node], node=node, step_index=s.index)

        emit("graph.topology", "agent graph", edges=self.graph.edges())
        return await self.graph.run(state, hook=hook)

    # -- nodes ----------------------------------------------------------------
    async def plan_node(self, s: RunState) -> RunState:
        emit = s.ctx.emit
        lessons = s.ctx.memory.recall_lessons(s.task)
        if lessons:
            emit(
                "memory.recall",
                f"Recalled {len(lessons)} related lessons",
                agent="planner",
                lessons=lessons,
            )
        try:
            plan = await self.planner.plan(s.task, lessons, replanning=s.replans > 0)
        except PlanningError as exc:
            s.plan, s.summary = None, f"planning failed: {exc}"
            emit("plan.failed", str(exc), agent="planner")
            return s
        done = s.records[: s.index] if s.replans else []
        s.plan = plan
        s.records = done + [StepRecord(step=step) for step in plan.steps]
        s.status = RunStatus.RUNNING
        emit(
            "plan.created",
            plan.rationale or f"{len(plan.steps)} steps",
            agent="planner",
            steps=[
                {
                    "id": st.id,
                    "intent": st.intent,
                    "tool": st.tool,
                    "risk": self.registry.get(st.tool).risk.value,
                }
                for st in plan.steps
            ],
            offset=len(done),
        )
        return s

    async def act_node(self, s: RunState) -> RunState:
        rec, ctx = s.current, s.ctx
        if self.step_delay_ms:
            await asyncio.sleep(self.step_delay_ms / 1000)
        rec.status, rec.attempts = StepStatus.RUNNING, rec.attempts + 1
        try:
            tool, args = self.registry.parse(rec.step.tool, rec.step.args)
        except (KeyError, ValueError) as exc:
            rec.observations.append(Observation(ok=False, error=str(exc), error_kind="denied"))
            return s
        summary = tool.summarize(args)
        risk = tool.risk_for(args)
        ctx.emit(
            "step.start",
            rec.step.intent,
            agent="operator",
            index=s.index,
            tool=tool.name,
            summary=summary,
            risk=risk.value,
            attempt=rec.attempts,
        )

        allowed, reason = await self.gate.check(
            ctx.emit, tool.name, risk, summary, tool.url_for(args)
        )
        if not allowed:
            rec.observations.append(Observation(ok=False, error=reason, error_kind="denied"))
            return s
        try:
            obs = await tool.run(args, ctx)
        except Exception as exc:  # a tool crash must never take the whole run down
            obs = Observation(ok=False, error=f"{type(exc).__name__}: {exc}", error_kind="crash")
        rec.observations.append(obs)
        ctx.emit(
            "step.result",
            obs.output or obs.error or "",
            agent="operator",
            index=s.index,
            ok=obs.ok,
            error_kind=obs.error_kind,
            data={k: v for k, v in obs.data.items() if k != "evidence"},
            screenshot=obs.screenshot,
        )
        return s

    async def verify_node(self, s: RunState) -> RunState:
        rec = s.current
        obs = rec.last
        assert obs is not None
        verdict = await self.verifier.check(rec.step, obs, s.ctx)
        s.last_verdict = verdict.reason
        # "Healed" means the UI changed under us or the agent corrected its own work —
        # not a plain retry after a timeout.
        healed = bool(obs.data.get("healed")) or (rec.step.tool == "code.fix" and rec.attempts > 1)
        if s.ctx.browser:
            # Only remember what an element looks like once the step is verified.
            s.ctx.browser.locator.settle(commit=verdict.passed)
        if verdict.passed:
            rec.status = StepStatus.HEALED if healed else StepStatus.PASSED
            s.healed_steps += int(healed)
        else:
            rec.status = StepStatus.FAILED
            if obs.ok:  # tool ran fine but the post-condition failed
                obs.error_kind = obs.error_kind or "assertion"
        s.ctx.emit(
            "step.verdict",
            verdict.reason,
            agent="verifier",
            index=s.index,
            passed=verdict.passed,
            status=rec.status.value,
        )
        return s

    def _after_verify(self, s: RunState) -> str:
        if s.current.status == StepStatus.FAILED:
            return "heal"
        if s.index + 1 < len(s.records):
            s.index += 1
            return "act"
        return "finish"

    async def heal_node(self, s: RunState) -> RunState:
        rec = s.current
        decision = self.healer.decide(rec, s.last_verdict)
        if decision.action == "replan" and s.replans >= self.max_replans:
            decision.action, decision.reason = (
                "give_up",
                f"{decision.reason} (replan budget exhausted)",
            )
        s.ctx.emit(
            "heal.decision", decision.reason, agent="healer", index=s.index, action=decision.action
        )
        if decision.action == "retry":
            rec.step.args = decision.new_args
            if decision.wait_ms:
                await asyncio.sleep(decision.wait_ms / 1000)
            s.next_after_heal = "act"
        elif decision.action == "replan":
            s.replans += 1
            s.task = f"{s.task}\n\nThe step “{rec.step.intent}” failed: {s.last_verdict}. Find another way."
            s.next_after_heal = "plan"
        else:
            s.summary = decision.reason
            s.next_after_heal = "finish"
        return s

    async def finish_node(self, s: RunState) -> RunState:
        ok = bool(s.records) and all(
            r.status in (StepStatus.PASSED, StepStatus.HEALED) for r in s.records
        )
        s.status = RunStatus.SUCCEEDED if ok else RunStatus.FAILED
        done = sum(r.status in (StepStatus.PASSED, StepStatus.HEALED) for r in s.records)
        if ok:
            s.summary = f"completed {done}/{len(s.records)} steps" + (
                f", self-healed {s.healed_steps}" if s.healed_steps else ""
            )
        else:
            s.summary = s.summary or f"stopped after {done}/{len(s.records)} steps"
            failed = next((r for r in s.records if r.status == StepStatus.FAILED), None)
            if failed:
                s.ctx.memory.add_lesson(
                    f"Task “{redact(s.task)[:120]}” failed at “{failed.step.intent}”: {s.last_verdict}",
                    run_id=s.ctx.run_id,
                    tags=["failure", failed.step.tool],
                )
        s.ctx.emit(
            "run.finished",
            s.summary,
            status=s.status.value,
            healed=s.healed_steps,
            steps=[
                {"intent": r.step.intent, "status": r.status.value, "attempts": r.attempts}
                for r in s.records
            ],
        )
        return s
