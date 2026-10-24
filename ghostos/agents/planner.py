"""Planner agent: natural-language task → executable Plan.

With an LLM, the planner sees the tool catalogue (names, risks, JSON schemas) and
past lessons recalled from memory, and writes a plan as JSON. Without one, a
deterministic command grammar covers the common cases, so GhostOS is fully usable
offline and in CI:

    open http://localhost:8765/acme, log in as ada@acme.io with password hunter2,
    click "New invoice", type "Globex" into customer, click "Save", verify "created"

    fix the failing tests in ./fixtures/buggy_repo and open a pull request
"""

from __future__ import annotations

import json
import re
from typing import Any

from ghostos.core.types import Expectation, Plan, Step
from ghostos.llm.base import LLMError, LLMProvider, extract_json
from ghostos.tools.base import ToolRegistry

PLANNER_SYSTEM = """You are the Planner agent inside GhostOS, an AI that operates a computer.
Turn the user's task into a short, executable plan using ONLY the tools listed.
Prefer human-visible labels for browser targets (e.g. "Sign in"), never CSS selectors.
Add an "expect" post-condition to steps whose success can be checked:
  {"kind": "text_visible", "value": "..."} | {"kind": "url_contains", "value": "..."} | {"kind": "tests_pass"}
Reply with JSON only:
{"rationale": "<one sentence>", "steps": [{"intent": "...", "tool": "...", "args": {...}, "expect": {...} | null}]}"""


class PlanningError(ValueError):
    pass


_SPLIT = re.compile(r"\s*(?:\n|;|,\s*(?:and\s+)?then\s+|,\s+|\s+then\s+|\s+and then\s+)\s*", re.I)
_Q = r"[\"“”']?(.+?)[\"“”']?"


def _unquote(s: str) -> str:
    return s.strip().strip("\"“”'").strip()


def _clause(text: str) -> list[Step]:
    t = text.strip().rstrip(".")
    if not t:
        return []

    if m := re.match(r"^(?:open|go to|visit|navigate to)\s+(\S+)$", t, re.I):
        url = m.group(1)
        url = url if re.match(r"^https?://", url) else f"http://{url}"
        return [Step(intent=f"Open {url}", tool="browser.goto", args={"url": url})]

    if m := re.match(
        r"^(?:log ?in|sign ?in)\s+as\s+(\S+)\s+(?:with|using)\s+(?:password\s+)?(\S+)$", t, re.I
    ):
        user, pw = _unquote(m.group(1)), _unquote(m.group(2))
        return [
            Step(
                intent="Enter the username",
                tool="browser.fill",
                args={"target": "username", "text": user},
            ),
            Step(
                intent="Enter the password",
                tool="browser.fill",
                args={"target": "password", "text": pw, "secret": True},
            ),
            Step(intent="Submit the login form", tool="browser.click", args={"target": "Sign in"}),
        ]

    if m := re.match(
        rf"^(?:type|enter|fill(?: in)?|write)\s+{_Q}\s+(?:into|in|for)\s+(?:the\s+)?{_Q}(?:\s+(?:field|box|input))?$",
        t,
        re.I,
    ):
        value, target = _unquote(m.group(1)), _unquote(m.group(2))
        return [
            Step(
                intent=f"Type into {target}",
                tool="browser.fill",
                args={"target": target, "text": value},
            )
        ]

    if m := re.match(
        rf"^(?:click|press|tap|hit)(?: on)?\s+(?:the\s+)?{_Q}(?:\s+(?:button|link|tab))?$", t, re.I
    ):
        target = _unquote(m.group(1))
        if target.lower() in {"enter", "tab", "escape", "esc"}:
            return [
                Step(intent=f"Press {target}", tool="browser.press", args={"key": target.title()})
            ]
        return [Step(intent=f"Click {target}", tool="browser.click", args={"target": target})]

    if m := re.match(
        rf"^(?:verify|check|assert|expect|confirm)(?: that)?(?: the page)?(?: (?:shows|contains|says|has))?\s+{_Q}(?:\s+is (?:visible|shown))?$",
        t,
        re.I,
    ):
        text = _unquote(m.group(1))
        return [
            Step(
                intent=f"Verify “{text}” is visible",
                tool="browser.assert_text",
                args={"text": text},
                expect=Expectation(kind="text_visible", value=text),
            )
        ]

    if m := re.match(r"^run\s+`?(.+?)`?$", t, re.I):
        cmd = m.group(1)
        if cmd.lower() in {"tests", "the tests", "test suite", "the test suite"}:
            return [Step(intent="Run the test suite", tool="tests.run", args={})]
        return [
            Step(
                intent=f"Run {cmd}",
                tool="shell.run",
                args={"command": cmd},
                expect=Expectation(kind="exit_code", value=0),
            )
        ]

    raise PlanningError(
        f"don't know how to do: “{t}” (offline grammar). Configure an LLM for open-ended tasks."
    )


_REPO_TASK = re.compile(
    r"(?:fix|repair|debug)\s+(?:the\s+)?(?:bugs?|failing tests?|tests?)\s+(?:in|of|for)\s+(?P<path>\S+)",
    re.I,
)


def repo_fix_plan(task: str, path: str) -> Plan:
    wants_pr = bool(re.search(r"pull request|\bpr\b", task, re.I))
    repo = (
        m.group(1)
        if (m := re.search(r"github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?(?:\s|$)", task))
        else None
    )
    steps = [
        Step(
            intent="Copy the repository into an isolated workspace",
            tool="workspace.prepare",
            args={"source": path},
        ),
        Step(intent="Run the test suite to find bugs", tool="tests.run", args={}),
        Step(
            intent="Diagnose the failures and patch the source",
            tool="code.fix",
            args={},
            expect=Expectation(kind="tests_pass"),
        ),
        Step(
            intent="Commit the fix on a new branch",
            tool="git.commit",
            args={
                "branch": "ghostos/fix-failing-tests",
                "message": "Fix bugs found by the test suite",
            },
        ),
    ]
    if wants_pr:
        steps.append(
            Step(
                intent="Open a pull request (needs your approval)",
                tool="github.pull_request",
                args={"title": "Fix bugs found by the test suite", "repo": repo},
            )
        )
    return Plan(goal=task, steps=steps, rationale="test → diagnose → patch → verify → ship")


def offline_plan(task: str) -> Plan:
    if m := _REPO_TASK.search(task):
        return repo_fix_plan(task, m.group("path").rstrip(",."))
    steps: list[Step] = []
    for clause in _SPLIT.split(task):
        steps.extend(_clause(clause))
    if not steps:
        raise PlanningError("empty task")
    return Plan(goal=task, steps=steps, rationale="parsed with the offline command grammar")


class Planner:
    def __init__(self, registry: ToolRegistry, llm: LLMProvider | None) -> None:
        self.registry = registry
        self.llm = llm

    async def plan(
        self, task: str, lessons: list[dict[str, Any]] | None = None, replanning: bool = False
    ) -> Plan:
        # Structured tasks are planned deterministically even with an LLM available:
        # it is faster, cheaper and auditable. The LLM handles everything else, including
        # replanning (the grammar would just produce the same failing plan again).
        if not replanning:
            try:
                return offline_plan(task)
            except PlanningError:
                if self.llm is None:
                    raise
        elif self.llm is None:
            raise PlanningError("replanning needs an LLM provider")
        return await self._llm_plan(task, lessons or [])

    async def _llm_plan(self, task: str, lessons: list[dict[str, Any]]) -> Plan:
        assert self.llm is not None
        tools = json.dumps(self.registry.schemas(), indent=1)
        memory = "\n".join(f"- {lesson['text']}" for lesson in lessons) or "- none"
        prompt = f"TASK: {task}\n\nTOOLS:\n{tools}\n\nLESSONS FROM MEMORY:\n{memory}"
        try:
            raw = extract_json(await self.llm.complete(PLANNER_SYSTEM, prompt))
        except LLMError as exc:
            raise PlanningError(str(exc)) from exc
        steps = []
        for s in raw.get("steps", []):
            self.registry.get(s["tool"])  # reject hallucinated tools early
            exp = s.get("expect")
            steps.append(
                Step(
                    intent=s.get("intent", s["tool"]),
                    tool=s["tool"],
                    args=s.get("args") or {},
                    expect=Expectation(**exp) if exp else None,
                )
            )
        if not steps:
            raise PlanningError("the LLM returned an empty plan")
        return Plan(goal=task, steps=steps, rationale=raw.get("rationale", ""))
