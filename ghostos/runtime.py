"""GhostOS runtime: assembles memory, sandbox, safety and agents into runnable tasks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ghostos.agents.healer import Healer
from ghostos.agents.orchestrator import Orchestrator, RunState
from ghostos.agents.planner import Planner
from ghostos.agents.verifier import Verifier
from ghostos.config import Settings
from ghostos.core.events import Emitter, EventBus, JsonlTrace
from ghostos.core.types import Event, RunStatus, new_id
from ghostos.llm import LLMProvider, get_provider
from ghostos.memory.store import MemoryStore
from ghostos.safety.gate import ApprovalBroker, PermissionGate, Resolver
from ghostos.safety.policy import Policy
from ghostos.sandbox import select_sandbox
from ghostos.tools import ToolContext, default_registry


@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    summary: str
    run_dir: Path
    healed: int


class GhostOS:
    def __init__(self, settings: Settings | None = None, resolver: Resolver | None = None) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_dirs()
        self.bus = EventBus()
        self.memory = MemoryStore(self.settings.db_path)
        self.broker = ApprovalBroker(resolver)
        self.policy = Policy.load(self.settings.policy_file)
        self.registry = default_registry()
        self.bus.subscribe_sync(self._persist)
        self.active: dict[str, asyncio.Task[RunResult]] = {}

    def _persist(self, event: Event) -> None:
        if event.type == "browser.frame":  # frames are big; keep only the path
            data = {k: v for k, v in event.data.items() if k != "image"}
        else:
            data = event.data
        self.memory.record_event(
            event.run_id, event.ts, event.type, event.agent, event.message, data
        )

    async def run(
        self,
        task: str,
        *,
        provider: str | None = None,
        script: Path | None = None,
        run_id: str | None = None,
        headless: bool | None = None,
    ) -> RunResult:
        run_id = run_id or new_id("run")
        run_dir = self.settings.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        trace = JsonlTrace(run_dir / "trace.jsonl")

        def trace_listener(event: Event) -> None:
            if event.run_id == run_id:
                trace(event)

        self.bus.subscribe_sync(trace_listener)
        emit = Emitter(self.bus, run_id)

        llm: LLMProvider | None = get_provider(
            provider or self.settings.provider, self.settings.model, script
        )
        sandbox = await select_sandbox(self.settings.sandbox)
        provider_name = llm.name if llm else "offline"
        self.memory.start_run(run_id, task, provider_name)
        emit(
            "run.started", task, provider=provider_name, sandbox=sandbox.name, run_dir=str(run_dir)
        )

        ctx = ToolContext(
            run_id=run_id,
            run_dir=run_dir,
            emit=emit,
            memory=self.memory,
            sandbox=sandbox,
            llm=llm,
            workspace=run_dir / "workspace",
        )
        ctx.scratch["headless"] = self.settings.headless if headless is None else headless
        orchestrator = Orchestrator(
            self.registry,
            PermissionGate(self.policy, self.broker),
            Planner(self.registry, llm),
            Verifier(self.registry),
            Healer(self.settings.max_heal_attempts, can_replan=llm is not None),
            self.settings.max_replans,
        )
        state = RunState(task=task, ctx=ctx)
        try:
            state = await orchestrator.run(state)
        except Exception as exc:
            state.status, state.summary = RunStatus.FAILED, f"crashed: {type(exc).__name__}: {exc}"
            emit("run.finished", state.summary, status=state.status.value)
        finally:
            if ctx.browser:
                await ctx.browser.close()
            self.bus.unsubscribe_sync(trace_listener)
        self.memory.finish_run(run_id, state.status.value, state.summary)
        return RunResult(run_id, state.status, state.summary, run_dir, state.healed_steps)

    def submit(self, task: str, **kwargs: object) -> str:
        """Start a run in the background (used by the API server)."""
        run_id = new_id("run")
        self.active[run_id] = asyncio.create_task(self.run(task, run_id=run_id, **kwargs))  # type: ignore[arg-type]
        self.active[run_id].add_done_callback(lambda _: self.active.pop(run_id, None))
        return run_id
