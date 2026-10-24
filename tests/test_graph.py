import pytest

from ghostos.core.graph import END, GraphError, StateGraph


async def inc(state: dict) -> dict:
    state["n"] += 1
    return state


async def test_router_loops_until_condition() -> None:
    g: StateGraph[dict] = StateGraph()
    g.add_node("inc", inc).set_entry("inc")
    g.add_router("inc", lambda s: END if s["n"] >= 3 else "inc", {"inc", END})
    seen: list[str] = []
    out = await g.run({"n": 0}, hook=lambda phase, node, _: seen.append(f"{phase}:{node}"))
    assert out["n"] == 3
    assert seen.count("enter:inc") == 3


async def test_infinite_loop_is_caught() -> None:
    g: StateGraph[dict] = StateGraph()
    g.add_node("inc", inc).set_entry("inc").add_edge("inc", "inc")
    with pytest.raises(GraphError, match="exceeded"):
        await g.run({"n": 0}, max_transitions=10)


async def test_router_must_return_declared_target() -> None:
    g: StateGraph[dict] = StateGraph()
    g.add_node("inc", inc).set_entry("inc")
    g.add_router("inc", lambda s: "nowhere", {END})
    with pytest.raises(GraphError, match="returned 'nowhere'"):
        await g.run({"n": 0})


def test_validation_rejects_dangling_nodes() -> None:
    g: StateGraph[dict] = StateGraph()
    g.add_node("a", inc).add_node("b", inc).set_entry("a").add_edge("a", END)
    with pytest.raises(GraphError, match="'b' has no outgoing edge"):
        g.validate()


def test_edges_lists_router_targets() -> None:
    g: StateGraph[dict] = StateGraph()
    g.add_node("a", inc).set_entry("a").add_router("a", lambda s: END, {"a", END})
    assert ("a", "a") in g.edges() and ("a", END) in g.edges()
