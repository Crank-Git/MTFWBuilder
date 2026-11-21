"""Tests for the ruff configuration in pyproject.toml (FR-foundation-6, FR-foundation-7)."""

import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Ten `if` branches give a cyclomatic complexity of 11, one above the limit.
COMPLEXITY_11 = textwrap.dedent(
    """\
    def classify(value: int) -> int:
        if value == 1:
            return 1
        if value == 2:
            return 2
        if value == 3:
            return 3
        if value == 4:
            return 4
        if value == 5:
            return 5
        if value == 6:
            return 6
        if value == 7:
            return 7
        if value == 8:
            return 8
        if value == 9:
            return 9
        if value == 10:
            return 10
        return 0
    """
)


def _ruff_check(path: Path) -> subprocess.CompletedProcess:
    # The ruff wheel ships a `python -m ruff` entry, so the test uses the ruff of this interpreter.
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--no-cache",
            "--output-format",
            "concise",
            "--config",
            str(ROOT / "pyproject.toml"),
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_ruff_reports_c901_for_a_function_with_complexity_11(tmp_path):
    source = tmp_path / "complex_module.py"
    source.write_text(COMPLEXITY_11, encoding="utf-8")

    result = _ruff_check(source)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "C901 `classify` is too complex (11 > 10)" in result.stdout


def test_dev_group_does_not_install_black():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)

    dev_group = pyproject["dependency-groups"]["dev"]
    assert not [dep for dep in dev_group if dep.startswith("black")]
    assert "black" not in pyproject.get("tool", {})
