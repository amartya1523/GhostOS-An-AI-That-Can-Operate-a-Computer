import os
from pathlib import Path

from ghostos.sandbox import DockerSandbox, Limits, ProcessSandbox


async def test_process_sandbox_runs_in_workspace(tmp_path: Path) -> None:
    res = await ProcessSandbox().run("pwd && echo hi", tmp_path)
    assert res.ok
    assert Path(res.stdout.splitlines()[0]).resolve() == tmp_path.resolve()


async def test_process_sandbox_scrubs_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    res = await ProcessSandbox().run("env", tmp_path)
    assert "leak-me" not in res.stdout
    assert "GHOSTOS_SANDBOXED=1" in res.stdout


async def test_process_sandbox_timeout_kills_process(tmp_path: Path) -> None:
    res = await ProcessSandbox().run("sleep 5", tmp_path, Limits(timeout_s=0.5))
    assert res.timed_out and not res.ok


def test_docker_argv_is_locked_down(tmp_path: Path) -> None:
    argv = DockerSandbox("img").build_argv("pytest", tmp_path, Limits(memory_mb=256))
    joined = " ".join(argv)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges",
        "--memory 256m",
        "--pids-limit",
        "--user 1000:1000",
    ):
        assert flag in joined
    assert argv[-3:] == ["sh", "-c", "pytest"]
    assert f"{tmp_path.resolve()}:/workspace:rw" in argv
    assert os.sep  # keep import used on all platforms


def test_docker_network_opt_in(tmp_path: Path) -> None:
    argv = DockerSandbox("img").build_argv("pip install x", tmp_path, Limits(network=True))
    assert argv[argv.index("--network") + 1] == "bridge"
