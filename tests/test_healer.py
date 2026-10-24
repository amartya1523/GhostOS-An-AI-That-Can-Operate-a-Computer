from ghostos.agents.healer import Healer
from ghostos.core.types import Observation, Step, StepRecord


def record(tool: str, kind: str | None, attempts: int = 1, **args) -> StepRecord:
    r = StepRecord(step=Step(intent="x", tool=tool, args=args), attempts=attempts)
    r.observations.append(Observation(ok=False, error="boom", error_kind=kind))
    return r


def test_denied_is_never_retried() -> None:
    assert Healer().decide(record("shell.run", "denied"), "").action == "give_up"


def test_failed_patch_feeds_evidence_back_to_coder() -> None:
    d = Healer().decide(record("code.fix", None), "1 tests still failing: test_x")
    assert d.action == "retry"
    assert d.new_args["feedback"] == ["attempt 1: 1 tests still failing: test_x"]


def test_assertion_waits_longer() -> None:
    d = Healer().decide(record("browser.assert_text", "assertion", text="ok", timeout_ms=1000), "")
    assert d.action == "retry" and d.new_args["timeout_ms"] == 2000


def test_transient_failures_back_off_exponentially() -> None:
    h = Healer(max_attempts=5)
    waits = [
        h.decide(record("browser.click", "timeout", attempts=n), "").wait_ms for n in (1, 2, 3)
    ]
    assert waits == [300, 600, 1200]


def test_budget_exhaustion_replans_only_with_llm() -> None:
    r = record("browser.click", "locator_not_found", attempts=3)
    assert Healer(3, can_replan=False).decide(r, "").action == "give_up"
    assert Healer(3, can_replan=True).decide(r, "").action == "replan"
