"""The permission gate every action passes through, with a human-in-the-loop broker."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ghostos.core.events import Emitter
from ghostos.core.types import RiskLevel, new_id
from ghostos.safety.policy import Decision, Policy


@dataclass
class ApprovalRequest:
    id: str
    run_id: str
    tool: str
    summary: str
    risk: RiskLevel
    reason: str
    future: asyncio.Future[bool] = field(repr=False)


# A resolver decides an approval request itself (e.g. a CLI prompt). When absent,
# the request waits until someone calls ``ApprovalBroker.resolve`` (e.g. the dashboard).
Resolver = Callable[[ApprovalRequest], Awaitable[bool]]


class ApprovalBroker:
    def __init__(self, resolver: Resolver | None = None, timeout_s: float = 600) -> None:
        self.resolver = resolver
        self.timeout_s = timeout_s
        self.pending: dict[str, ApprovalRequest] = {}

    async def request(
        self, run_id: str, tool: str, summary: str, risk: RiskLevel, reason: str
    ) -> bool:
        loop = asyncio.get_running_loop()
        req = ApprovalRequest(
            new_id("appr"), run_id, tool, summary, risk, reason, loop.create_future()
        )
        self.pending[req.id] = req
        try:
            if self.resolver is not None:
                return await self.resolver(req)
            return await asyncio.wait_for(req.future, self.timeout_s)
        except TimeoutError:
            return False
        finally:
            self.pending.pop(req.id, None)

    def resolve(self, request_id: str, approved: bool) -> bool:
        req = self.pending.get(request_id)
        if req is None or req.future.done():
            return False
        req.future.set_result(approved)
        return True


async def auto_approve(_: ApprovalRequest) -> bool:
    return True


class PermissionGate:
    def __init__(self, policy: Policy, broker: ApprovalBroker) -> None:
        self.policy = policy
        self.broker = broker

    async def check(
        self, emit: Emitter, tool: str, risk: RiskLevel, summary: str, url: str | None = None
    ) -> tuple[bool, str]:
        decision, reason = self.policy.decide(tool, risk, url)
        if decision == Decision.ALLOW:
            return True, reason
        if decision == Decision.DENY:
            emit(
                "permission.denied", f"{summary} — {reason}", agent="guardian", tool=tool, risk=risk
            )
            return False, reason

        task = asyncio.ensure_future(self.broker.request(emit.run_id, tool, summary, risk, reason))
        # Yield once so the request is registered and its id can be announced.
        await asyncio.sleep(0)
        approval_id = next(
            (r.id for r in reversed(self.broker.pending.values()) if r.run_id == emit.run_id), None
        )
        emit(
            "approval.request",
            summary,
            agent="guardian",
            tool=tool,
            risk=risk,
            reason=reason,
            approval_id=approval_id,
        )
        approved = await task
        emit(
            "approval.resolved",
            f"{'approved' if approved else 'rejected'}: {summary}",
            agent="guardian",
            approved=approved,
        )
        return approved, "approved by human" if approved else "rejected by human"
