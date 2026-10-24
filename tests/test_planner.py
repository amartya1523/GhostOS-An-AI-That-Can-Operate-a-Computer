import pytest

from ghostos.agents.planner import Planner, PlanningError, offline_plan
from ghostos.llm.scripted import ScriptedProvider
from ghostos.tools import default_registry


def test_browser_grammar_with_login_macro() -> None:
    plan = offline_plan(
        "open localhost:8765/sites/acme/, log in as ada@acme.io with password hunter2, "
        'click "New invoice", type "Globex" into customer field, verify "created"'
    )
    tools = [s.tool for s in plan.steps]
    assert tools == [
        "browser.goto",
        "browser.fill",
        "browser.fill",
        "browser.click",
        "browser.click",
        "browser.fill",
        "browser.assert_text",
    ]
    assert plan.steps[0].args["url"] == "http://localhost:8765/sites/acme/"
    assert plan.steps[2].args["secret"] is True
    assert plan.steps[5].args == {"target": "customer", "text": "Globex"}
    assert plan.steps[-1].expect.kind == "text_visible"


def test_repo_task_builds_dev_pipeline() -> None:
    plan = offline_plan(
        "Fix the failing tests in ./repo and open a pull request on github.com/me/repo"
    )
    assert [s.tool for s in plan.steps] == [
        "workspace.prepare",
        "tests.run",
        "code.fix",
        "git.commit",
        "github.pull_request",
    ]
    assert plan.steps[2].expect.kind == "tests_pass"
    assert plan.steps[-1].args["repo"] == "me/repo"


def test_unknown_instruction_without_llm_fails_loudly() -> None:
    with pytest.raises(PlanningError, match="offline grammar"):
        offline_plan("book me a flight to Tokyo")


async def test_llm_planner_rejects_hallucinated_tools() -> None:
    llm = ScriptedProvider(
        [{"when": "TASK", "reply": {"steps": [{"tool": "teleport", "args": {}}]}}]
    )
    with pytest.raises(KeyError):
        await Planner(default_registry(), llm).plan("book me a flight to Tokyo")


async def test_llm_planner_used_for_open_ended_tasks() -> None:
    reply = {
        "rationale": "search",
        "steps": [
            {
                "intent": "open",
                "tool": "browser.goto",
                "args": {"url": "https://example.com"},
                "expect": None,
            }
        ],
    }
    llm = ScriptedProvider([{"when": "TASK: book", "reply": reply}])
    plan = await Planner(default_registry(), llm).plan("book me a flight")
    assert plan.steps[0].tool == "browser.goto"
