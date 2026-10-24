"""End-to-end: real Chromium against the demo site, and the full dev-agent loop."""

import httpx
import pytest

from ghostos.core.types import RunStatus
from ghostos.runtime import GhostOS
from ghostos.safety.gate import auto_approve
from tests.conftest import ROOT

TASK = (
    'open {url}/sites/acme/, log in as ada@acme.io with password hunter2, click "New invoice", '
    'type "Globex" into customer, type "420" into amount, click "Save", verify "created for Globex"'
)


@pytest.mark.e2e
async def test_self_healing_across_a_redesign(settings, demo_server) -> None:
    ghost = GhostOS(settings)
    httpx.post(
        f"{demo_server}/sites/acme/deploy",
        json={"version": "v1"},
        headers={"x-ghostos-client": "test"},
    )
    first = await ghost.run(TASK.format(url=demo_server))
    assert first.status == RunStatus.SUCCEEDED and first.healed == 0

    httpx.post(
        f"{demo_server}/sites/acme/deploy",
        json={"version": "v2"},
        headers={"x-ghostos-client": "test"},
    )  # every selector dies
    second = await ghost.run(TASK.format(url=demo_server))
    assert second.status == RunStatus.SUCCEEDED
    assert second.healed >= 5
    strategies = {h["strategy"] for h in ghost.memory.healings()}
    assert "fingerprint" in strategies

    third = await ghost.run(TASK.format(url=demo_server))  # learned: no healing needed
    assert third.status == RunStatus.SUCCEEDED and third.healed == 0


@pytest.mark.e2e
async def test_dev_agent_self_corrects_and_ships(settings) -> None:
    ghost = GhostOS(settings, resolver=auto_approve)
    repo = ROOT / "fixtures" / "buggy_repo"
    result = await ghost.run(
        f"fix the failing tests in {repo} and open a pull request",
        provider="scripted",
        script=repo / ".ghost" / "script.json",
    )
    assert result.status == RunStatus.SUCCEEDED
    events = ghost.memory.events(result.run_id)
    patches = [e for e in events if e["type"] == "code.patch"]
    assert len(patches) == 2  # first patch incomplete, second one fixes it
    assert (result.run_dir / "pull_request.md").exists()
    assert "12 passed" in (result.run_dir / "pull_request.md").read_text()
    # the original repository is never modified
    assert 'replace(",", "")' not in (repo / "invoicing" / "money.py").read_text()


@pytest.mark.e2e
async def test_rejected_approval_stops_the_run(settings) -> None:
    async def reject(_):
        return False

    ghost = GhostOS(settings, resolver=reject)
    repo = ROOT / "fixtures" / "buggy_repo"
    result = await ghost.run(
        f"fix the failing tests in {repo} and open a pull request",
        provider="scripted",
        script=repo / ".ghost" / "script.json",
    )
    assert result.status == RunStatus.FAILED
    assert not (result.run_dir / "pull_request.md").exists()
