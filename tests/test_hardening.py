"""Regression tests for issues found in code review."""

import pydantic
import pytest

from ghostos.agents.planner import offline_plan
from ghostos.safety.redact import redact
from ghostos.sandbox import DockerSandbox, Limits
from ghostos.tools.code import CommitArgs, FixCode, PRArgs


def test_secrets_are_redacted_from_task_text() -> None:
    task = "log in as ada@acme.io with password hunter2, then use api key sk-123 and token=abc"
    out = redact(task)
    for secret in ("hunter2", "sk-123", "abc"):
        assert secret not in out
    assert "ada@acme.io" in out
    # the planner still receives the real value through the unredacted task
    assert offline_plan("log in as a with password hunter2").steps[1].args["text"] == "hunter2"


@pytest.mark.parametrize("bad", ["--output=/tmp/x", "-f", "main;rm -rf ~", "a b", ""])
def test_git_refs_reject_option_and_shell_injection(bad: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        PRArgs(title="t", base=bad)
    with pytest.raises(pydantic.ValidationError):
        CommitArgs(branch=bad)


def test_repo_name_is_validated() -> None:
    assert PRArgs(title="t", repo="me/my-repo.js").repo == "me/my-repo.js"
    with pytest.raises(pydantic.ValidationError):
        PRArgs(title="t", repo="me/repo --upload-pack=evil")


@pytest.mark.parametrize("path", ["conftest.py", "pkg/conftest.py", "pkg/money_test.py"])
def test_coder_may_not_touch_test_infrastructure(tmp_path, path: str) -> None:
    with pytest.raises(PermissionError):
        FixCode._apply(tmp_path, {"path": path, "search": "a", "replace": "b"})


def test_docker_never_pulls_and_names_containers(tmp_path) -> None:
    argv = DockerSandbox("img").build_argv("true", tmp_path, Limits(), "ghostos-abc")
    assert argv[argv.index("--pull") + 1] == "never"
    assert argv[argv.index("--name") + 1] == "ghostos-abc"


async def test_unconfigured_provider_fails_cleanly(settings) -> None:
    from ghostos.runtime import GhostOS

    ghost = GhostOS(settings)
    listeners = len(ghost.bus._listeners)
    result = await ghost.run("anything", provider="anthropic")  # no API key in tests
    assert result.status == "failed" and "ANTHROPIC_API_KEY" in result.summary
    assert ghost.memory.run(result.run_id)["status"] == "failed"
    assert len(ghost.bus._listeners) == listeners  # trace listener removed
