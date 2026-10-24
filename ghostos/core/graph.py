"""A minimal, dependency-free state graph (the LangGraph idea, ~100 lines).

Nodes are async functions ``(state) -> state``. Edges are either fixed or chosen by
a router function. The graph emits ``node.enter`` / ``node.exit`` events, so the
dashboard can light up the agent that is currently thinking.

Why not LangGraph itself? GhostOS needs only the control-flow core, and owning it
keeps every transition observable, testable and free of heavyweight dependencies.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

S = TypeVar("S")

END = "__end__"

Node = Callable[[S], Awaitable[S]]
Router = Callable[[S], str]
Hook = Callable[[str, str, S], None]  # (phase, node_name, state)


class GraphError(RuntimeError):
    pass


class StateGraph(Generic[S]):
    def __init__(self) -> None:
        self._nodes: dict[str, Node[S]] = {}
        self._edges: dict[str, str] = {}
        self._routers: dict[str, tuple[Router[S], set[str]]] = {}
        self._entry: str | None = None

    def add_node(self, name: str, fn: Node[S]) -> StateGraph[S]:
        if name in self._nodes or name == END:
            raise GraphError(f"duplicate or reserved node name: {name}")
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> StateGraph[S]:
        self._edges[src] = dst
        return self

    def add_router(self, src: str, router: Router[S], targets: set[str]) -> StateGraph[S]:
        self._routers[src] = (router, targets)
        return self

    def set_entry(self, name: str) -> StateGraph[S]:
        self._entry = name
        return self

    def validate(self) -> None:
        if self._entry not in self._nodes:
            raise GraphError("entry node is not set or unknown")
        for src in self._nodes:
            if src not in self._edges and src not in self._routers:
                raise GraphError(f"node '{src}' has no outgoing edge")
        known = set(self._nodes) | {END}
        for src, dst in self._edges.items():
            if dst not in known:
                raise GraphError(f"edge {src} -> {dst} targets an unknown node")
        for src, (_, targets) in self._routers.items():
            unknown = targets - known
            if unknown:
                raise GraphError(f"router on '{src}' targets unknown nodes {unknown}")

    def edges(self) -> list[tuple[str, str]]:
        """All possible transitions; used to draw the agent graph in the dashboard."""
        out = list(self._edges.items())
        for src, (_, targets) in self._routers.items():
            out.extend((src, t) for t in sorted(targets))
        return out

    async def run(self, state: S, *, max_transitions: int = 500, hook: Hook[S] | None = None) -> S:
        self.validate()
        current: str = self._entry  # type: ignore[assignment]
        for _ in range(max_transitions):
            if hook:
                hook("enter", current, state)
            state = await self._nodes[current](state)
            if hook:
                hook("exit", current, state)
            nxt = self._next(current, state)
            if nxt == END:
                return state
            current = nxt
        raise GraphError(f"exceeded {max_transitions} transitions; possible infinite loop")

    def _next(self, current: str, state: S) -> str:
        if current in self._routers:
            router, targets = self._routers[current]
            choice = router(state)
            if choice not in targets:
                raise GraphError(f"router on '{current}' returned '{choice}', not in {targets}")
            return choice
        return self._edges[current]
