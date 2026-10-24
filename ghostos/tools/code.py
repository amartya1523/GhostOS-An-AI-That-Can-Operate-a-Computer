"""Developer tools: prepare a workspace, run tests, let the Coder agent patch code, ship a PR."""

from __future__ import annotations

import difflib
import os
import re
import shlex
import shutil
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ghostos.core.types import Observation, RiskLevel
from ghostos.llm.base import LLMError, extract_json
from ghostos.sandbox.base import Limits
from ghostos.tools.base import Tool, ToolContext
from ghostos.tools.shell import resolve_in

AGENT_GIT = "-c user.name='GhostOS Agent' -c user.email='ghostos-agent@users.noreply.github.com'"
_IGNORE = shutil.ignore_patterns(
    ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ghost", "*.pyc"
)


class PrepareArgs(BaseModel):
    source: str = Field(description="Local path of the repository to work on")


class PrepareWorkspace(Tool[PrepareArgs]):
    name = "workspace.prepare"
    description = "Copy a repository into an isolated workspace and snapshot it with git."
    risk = RiskLevel.READ
    Args = PrepareArgs

    async def run(self, args: PrepareArgs, ctx: ToolContext) -> Observation:
        src = Path(args.source).expanduser().resolve()
        if not src.is_dir():
            return Observation(ok=False, error=f"{src} is not a directory", error_kind="io")
        if ctx.workspace.exists():
            shutil.rmtree(ctx.workspace)
        shutil.copytree(src, ctx.workspace, ignore=_IGNORE)
        ctx.scratch["source"] = str(src)
        if not (ctx.workspace / ".git").exists():
            res = await ctx.sandbox.run(
                "git init -q && git symbolic-ref HEAD refs/heads/main && git add -A && "
                f"git {AGENT_GIT} commit -qm 'baseline'",
                ctx.workspace,
            )
            if not res.ok:
                return Observation(ok=False, error=res.summary(), error_kind="exit_code")
        files = sorted(
            str(p.relative_to(ctx.workspace))
            for p in ctx.workspace.rglob("*")
            if p.is_file() and ".git" not in p.parts
        )
        return Observation(
            ok=True, output=f"workspace ready with {len(files)} files", data={"files": files}
        )


_FAILED = re.compile(r"^FAILED (\S+?)(?: - (.*))?$", re.M)
_SUMMARY = re.compile(r"(\d+) (passed|failed|error)")


def parse_pytest(output: str) -> dict[str, Any]:
    counts = {kind: int(n) for n, kind in _SUMMARY.findall(output)}
    failures = [{"test": t, "reason": (r or "").strip()} for t, r in _FAILED.findall(output)]
    return {
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0) + counts.get("error", 0),
        "failures": failures,
    }


class TestsArgs(BaseModel):
    command: str = "python -m pytest -q -rf --tb=short --no-header -p no:cacheprovider"


class RunTests(Tool[TestsArgs]):
    name = "tests.run"
    description = "Run the project's test suite and report failures in a structured way."
    risk = RiskLevel.EXECUTE
    Args = TestsArgs

    async def run(self, args: TestsArgs, ctx: ToolContext) -> Observation:
        res = await ctx.sandbox.run(args.command, ctx.workspace, Limits(timeout_s=180))
        report = parse_pytest(res.stdout + res.stderr)
        ctx.scratch["test_report"] = report
        ctx.scratch["test_output"] = res.summary(8000)
        ctx.emit(
            "tests.report",
            f"{report['passed']} passed, {report['failed']} failed",
            agent="verifier",
            **report,
            backend=res.backend,
        )
        # Running the tests succeeded even when tests fail: failures are information.
        ok = res.exit_code in (0, 1) and not res.timed_out
        return Observation(
            ok=ok,
            output=res.summary(4000),
            data=report,
            error=None if ok else res.summary(1000),
            error_kind=None if ok else "exit_code",
        )


CODER_SYSTEM = """You are the Coder agent inside GhostOS, an autonomous software engineer.
Fix the SOURCE code so the failing tests pass. Never edit tests. Make minimal, correct edits.
Reply with JSON only:
{"diagnosis": "<root cause in one or two sentences>",
 "edits": [{"path": "<file>", "search": "<exact existing text>", "replace": "<new text>", "why": "<short>"}]}
Each "search" must match the file exactly once, including whitespace."""


class FixArgs(BaseModel):
    goal: str = "make the failing tests pass"
    feedback: list[str] = Field(default_factory=list, description="Why previous attempts failed")


class FixCode(Tool[FixArgs]):
    name = "code.fix"
    description = (
        "Diagnose failing tests and apply a minimal patch to the source code (LLM-driven)."
    )
    risk = RiskLevel.WRITE
    Args = FixArgs

    async def run(self, args: FixArgs, ctx: ToolContext) -> Observation:
        if ctx.llm is None:
            return Observation(
                ok=False,
                error="code.fix needs an LLM provider (set ANTHROPIC_API_KEY or use --provider scripted)",
                error_kind="no_llm",
            )
        report = ctx.scratch.get("test_report") or {}
        prompt = self._prompt(args, ctx, report)
        ctx.emit(
            "coder.thinking",
            f"Reading {len(report.get('failures', []))} failing tests and the source",
            agent="coder",
        )
        try:
            reply = extract_json(await ctx.llm.complete(CODER_SYSTEM, prompt))
        except LLMError as exc:
            return Observation(ok=False, error=str(exc), error_kind="llm")

        diffs, errors = [], []
        for edit in reply.get("edits", []):
            try:
                diffs.append(self._apply(ctx.workspace, edit))
            except (ValueError, PermissionError, OSError) as exc:
                errors.append(str(exc))
        diagnosis = reply.get("diagnosis", "")
        ctx.emit(
            "code.patch",
            diagnosis,
            agent="coder",
            diff="\n".join(diffs),
            errors=errors,
            edits=[{"path": e.get("path"), "why": e.get("why")} for e in reply.get("edits", [])],
        )
        if errors and not diffs:
            return Observation(ok=False, error="; ".join(errors), error_kind="patch_rejected")
        ctx.scratch.setdefault("diagnoses", []).append(diagnosis)
        return Observation(
            ok=True, output=diagnosis, data={"diff": "\n".join(diffs), "errors": errors}
        )

    @staticmethod
    def _prompt(args: FixArgs, ctx: ToolContext, report: dict[str, Any]) -> str:
        sources = []
        budget = 40_000
        for path in sorted(ctx.workspace.rglob("*.py")):
            rel = path.relative_to(ctx.workspace)
            if ".git" in rel.parts or rel.name.startswith("test_") or "tests" in rel.parts:
                continue
            text = path.read_text(errors="replace")
            if len(text) > budget:
                break
            budget -= len(text)
            sources.append(f"### {rel}\n```python\n{text}\n```")
        failing = "\n".join(f"- {f['test']}: {f['reason']}" for f in report.get("failures", []))
        feedback = "\n".join(f"- {f}" for f in args.feedback) or "- (first attempt)"
        return (
            f"GOAL: {args.goal}\n\nFAILING TESTS:\n{failing}\n\n"
            f"TEST OUTPUT:\n```\n{ctx.scratch.get('test_output', '')[-6000:]}\n```\n\n"
            f"PREVIOUS ATTEMPTS:\n{feedback}\n\nSOURCE FILES:\n" + "\n\n".join(sources)
        )

    @staticmethod
    def _apply(workspace: Path, edit: dict[str, Any]) -> str:
        rel = edit["path"]
        if re.search(r"(^|/)tests?/|(^|/)test_[^/]*\.py$", rel):
            raise PermissionError(f"refusing to edit test file {rel}")
        path = resolve_in(workspace, rel)
        before = path.read_text()
        count = before.count(edit["search"])
        if count != 1:
            raise ValueError(f"{rel}: search text matched {count} times (need exactly 1)")
        after = before.replace(edit["search"], edit["replace"], 1)
        path.write_text(after)
        return "".join(
            difflib.unified_diff(
                before.splitlines(True), after.splitlines(True), f"a/{rel}", f"b/{rel}"
            )
        )


class CommitArgs(BaseModel):
    branch: str = "ghostos/fix-failing-tests"
    message: str = "Fix failing tests"


class Commit(Tool[CommitArgs]):
    name = "git.commit"
    description = "Create a branch in the workspace and commit all changes."
    risk = RiskLevel.WRITE
    Args = CommitArgs

    async def run(self, args: CommitArgs, ctx: ToolContext) -> Observation:
        branch, msg = shlex.quote(args.branch), shlex.quote(args.message)
        cmd = f"git checkout -q -B {branch} && git add -A && git {AGENT_GIT} commit -qm {msg} && git log --oneline -1"
        res = await ctx.sandbox.run(cmd, ctx.workspace)
        if not res.ok:
            return Observation(ok=False, error=res.summary(), error_kind="exit_code")
        ctx.scratch["branch"] = args.branch
        return Observation(ok=True, output=res.stdout.strip(), data={"branch": args.branch})


class PRArgs(BaseModel):
    title: str
    body: str = ""
    base: str = "main"
    repo: str | None = Field(
        default=None, description="owner/name on GitHub; omit for a local dry-run"
    )


class PullRequest(Tool[PRArgs]):
    name = "github.pull_request"
    description = (
        "Push the branch and open a pull request on GitHub (dry-run writes the PR locally)."
    )
    risk = RiskLevel.IRREVERSIBLE
    Args = PRArgs

    def summarize(self, args: PRArgs) -> str:
        where = args.repo or "local dry-run"
        return f"open pull request “{args.title}” → {where}"

    async def run(self, args: PRArgs, ctx: ToolContext) -> Observation:
        branch = ctx.scratch.get("branch", "HEAD")
        span = shlex.quote(f"{args.base}...{branch}")
        diff = await ctx.sandbox.run(f"git diff {span} --stat && git diff {span}", ctx.workspace)
        body = args.body or self._default_body(ctx)
        token = os.environ.get("GITHUB_TOKEN")
        if args.repo and token:
            return await self._open_on_github(args, ctx, branch, body, token)
        pr_file = ctx.run_dir / "pull_request.md"
        pr_file.write_text(
            f"# {args.title}\n\n`{branch}` → `{args.base}`\n\n{body}\n\n```diff\n{diff.stdout}\n```\n"
        )
        ctx.emit(
            "pr.draft",
            f"Pull request drafted: {args.title}",
            agent="operator",
            path=str(pr_file),
            title=args.title,
            body=body,
            diff=diff.stdout,
        )
        return Observation(
            ok=True,
            output=f"dry-run: PR written to {pr_file}",
            data={"path": str(pr_file), "dry_run": True},
        )

    @staticmethod
    def _default_body(ctx: ToolContext) -> str:
        report = ctx.scratch.get("test_report", {})
        lines = [
            "## What GhostOS changed",
            *[f"- {d}" for d in ctx.scratch.get("diagnoses", [])],
            "",
            "## Verification",
            f"- Test suite: {report.get('passed', '?')} passed, {report.get('failed', '?')} failed",
            "",
            "_Opened by GhostOS after human approval._",
        ]
        return "\n".join(lines)

    async def _open_on_github(
        self, args: PRArgs, ctx: ToolContext, branch: str, body: str, token: str
    ) -> Observation:
        remote = f"https://x-access-token:{token}@github.com/{args.repo}.git"
        refspec = shlex.quote(f"{branch}:{branch}")
        push = await ctx.sandbox.run(
            f"git push -q {shlex.quote(remote)} {refspec}", ctx.workspace, Limits(network=True)
        )
        if not push.ok:
            return Observation(
                ok=False, error=push.summary().replace(token, "***"), error_kind="exit_code"
            )
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"https://api.github.com/repos/{args.repo}/pulls",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"title": args.title, "head": branch, "base": args.base, "body": body},
            )
        if resp.status_code >= 300:
            return Observation(
                ok=False, error=f"GitHub {resp.status_code}: {resp.text[:300]}", error_kind="http"
            )
        url = resp.json()["html_url"]
        ctx.emit("pr.opened", f"Opened {url}", agent="operator", url=url)
        return Observation(ok=True, output=url, data={"url": url})


CODE_TOOLS: list[Tool[Any]] = [PrepareWorkspace(), RunTests(), FixCode(), Commit(), PullRequest()]
