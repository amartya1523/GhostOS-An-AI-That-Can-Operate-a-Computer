"""Verifier agent: decides whether a step actually achieved what it was supposed to.

"The tool returned without an exception" is not success. The verifier checks the
step's declared post-condition against the real world (page text, URL, exit code,
test suite) before the run is allowed to move on.
"""

from __future__ import annotations

from dataclasses import dataclass

from ghostos.core.types import Observation, Step
from ghostos.tools.base import ToolContext, ToolRegistry


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reason: str


class Verifier:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def check(self, step: Step, obs: Observation, ctx: ToolContext) -> Verdict:
        if not obs.ok:
            return Verdict(False, obs.error or "step failed")
        exp = step.expect
        if exp is None or exp.kind == "none":
            return Verdict(True, "tool reported success")

        if exp.kind == "text_visible":
            if step.tool == "browser.assert_text":
                return Verdict(True, f"“{exp.value}” is visible")
            page = ctx.browser.page if ctx.browser else None
            text = await page.inner_text("body") if page else ""
            ok = str(exp.value).lower() in text.lower()
            return Verdict(ok, f"“{exp.value}” {'found' if ok else 'not found'} on the page")

        if exp.kind == "url_contains":
            url = ctx.browser.page.url if ctx.browser and ctx.browser.page else ""
            ok = str(exp.value) in url
            return Verdict(ok, f"url {url} {'contains' if ok else 'does not contain'} {exp.value}")

        if exp.kind == "exit_code":
            code = obs.data.get("exit_code")
            return Verdict(code == (exp.value or 0), f"exit code {code}")

        if exp.kind == "tests_pass":
            tool, args = self.registry.parse("tests.run", {})
            result = await tool.run(args, ctx)
            report = result.data
            ok = result.ok and report.get("failed", 1) == 0 and report.get("passed", 0) > 0
            if ok:
                return Verdict(True, f"all {report['passed']} tests pass")
            failing = (
                ", ".join(f["test"].split("::")[-1] for f in report.get("failures", []))
                or "unknown"
            )
            return Verdict(False, f"{report.get('failed', '?')} tests still failing: {failing}")

        return Verdict(False, f"unknown expectation kind '{exp.kind}'")
