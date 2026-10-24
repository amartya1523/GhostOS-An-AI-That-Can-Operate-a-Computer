from pathlib import Path

import pytest

from ghostos.tools.code import FixCode, parse_pytest
from ghostos.tools.shell import resolve_in

PYTEST_OUT = """
FAILED tests/test_ids.py::test_pad - AssertionError: assert 'INV-42' == 'INV-0042'
FAILED tests/test_money.py::test_sep - ValueError: not an amount
2 failed, 9 passed in 0.21s
"""


def test_parse_pytest_summary() -> None:
    r = parse_pytest(PYTEST_OUT)
    assert (r["passed"], r["failed"]) == (9, 2)
    assert r["failures"][0] == {
        "test": "tests/test_ids.py::test_pad",
        "reason": "AssertionError: assert 'INV-42' == 'INV-0042'",
    }


def test_resolve_in_blocks_escape(tmp_path: Path) -> None:
    assert resolve_in(tmp_path, "a/b.txt") == (tmp_path / "a/b.txt").resolve()
    with pytest.raises(PermissionError):
        resolve_in(tmp_path, "../../etc/passwd")
    with pytest.raises(PermissionError):
        resolve_in(tmp_path, "/etc/passwd")


def test_patch_applies_exactly_once(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("x = 1\ny = 1\n")
    diff = FixCode._apply(tmp_path, {"path": "m.py", "search": "x = 1", "replace": "x = 2"})
    assert "-x = 1" in diff and "+x = 2" in diff
    with pytest.raises(ValueError, match="matched 0 times"):
        FixCode._apply(tmp_path, {"path": "m.py", "search": "z = 9", "replace": ""})
    (tmp_path / "d.py").write_text("a\na\n")
    with pytest.raises(ValueError, match="matched 2 times"):
        FixCode._apply(tmp_path, {"path": "d.py", "search": "a", "replace": "b"})


@pytest.mark.parametrize("path", ["tests/test_x.py", "test_y.py", "pkg/tests/conftest.py"])
def test_coder_may_not_edit_tests(tmp_path: Path, path: str) -> None:
    with pytest.raises(PermissionError, match="test file"):
        FixCode._apply(tmp_path, {"path": path, "search": "a", "replace": "b"})
