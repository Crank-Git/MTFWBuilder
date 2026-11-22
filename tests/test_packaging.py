"""Tests for the packaging metadata in pyproject.toml (FR-platform-modernization-1 to 3)."""

import re
import tomllib
from pathlib import Path

import pytest

import mtfwbuilder
from mtfwbuilder.main import create_app

ROOT = Path(__file__).resolve().parent.parent

FLOORS = {
    "fastapi": (0, 165),
    "pydantic": (2, 14),
    "uvicorn": (0, 54),
    "jinja2": (3, 1, 6),
    "platformio": (6, 2, 0),
}


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _floor(name: str) -> tuple[int, ...]:
    for dep in _pyproject()["project"]["dependencies"]:
        match = re.fullmatch(rf"{name}(\[[^\]]*\])?>=([0-9.]+)", dep)
        if match:
            return tuple(int(part) for part in match.group(2).split("."))
    raise AssertionError(f"{name} has no >= floor in [project].dependencies")


def test_requires_python_is_313_or_later_and_below_315():
    # The `meshtastic` package 2.7.11 declares `Requires-Python: >=3.9,<3.15`, so uv cannot lock for 3.15.
    assert _pyproject()["project"]["requires-python"] == ">=3.13,<3.15"


@pytest.mark.parametrize(("name", "minimum"), FLOORS.items())
def test_dependency_floor_is_at_least_the_spec_version(name, minimum):
    assert _floor(name) >= minimum


def test_uv_lock_is_committed_next_to_pyproject():
    assert (ROOT / "uv.lock").is_file()


def test_app_version_is_the_pyproject_version():
    assert mtfwbuilder.__version__ == _pyproject()["project"]["version"]
    assert create_app().version == mtfwbuilder.__version__
