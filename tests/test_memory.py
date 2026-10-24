from ghostos.memory.store import MemoryStore
from ghostos.memory.vector import HashingEmbedder


def test_embedder_is_deterministic_and_normalised() -> None:
    e = HashingEmbedder()
    a, b = e.embed("click the sign in button"), e.embed("click the sign in button")
    assert (a == b).all()
    assert abs(float((a * a).sum()) - 1.0) < 1e-5


def test_fingerprint_memory_upserts(tmp_path) -> None:
    m = MemoryStore(tmp_path / "m.db")
    m.remember_element("s", "Sign in", "#a", {"tag": "button"}, "shot1.png")
    m.remember_element("s", "sign in", "#b", {"tag": "button"}, None)
    row = m.recall_element("s", "SIGN IN")
    assert row["selector"] == "#b"
    assert row["hits"] == 2
    assert row["screenshot"] == "shot1.png"  # kept when the update has none
    assert m.recall_element("other", "sign in") is None


def test_lessons_are_recalled_by_meaning(tmp_path) -> None:
    m = MemoryStore(tmp_path / "m.db")
    m.add_lesson("On acme the Sign in button moved to the header; healed with fingerprint.")
    m.add_lesson("pytest needs PYTHONPATH set for src layouts")
    m.add_lesson("Docker daemon was not running; fell back to process sandbox")
    hits = m.recall_lessons("log in to acme and press the sign in button")
    assert hits and "Sign in" in hits[0]["text"]


def test_runs_and_events_roundtrip(tmp_path) -> None:
    m = MemoryStore(tmp_path / "m.db")
    m.start_run("r1", "do things", "offline")
    m.record_event("r1", 1.0, "step.start", "operator", "go", {"index": 0})
    m.finish_run("r1", "succeeded", "done")
    assert m.run("r1")["status"] == "succeeded"
    assert m.events("r1")[0]["data"] == {"index": 0}
    assert m.stats()["events"] == 1
