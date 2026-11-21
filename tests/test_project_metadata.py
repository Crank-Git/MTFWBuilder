"""Tests for the license metadata that the package declares (ADR 0004)."""

from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parent.parent


def _project_table() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_pyproject_declares_gpl_3_or_later_license():
    assert _project_table()["license"] == "GPL-3.0-or-later"


def test_pyproject_declares_only_the_gpl_3_or_later_classifier():
    license_classifiers = [c for c in _project_table()["classifiers"] if c.startswith("License ::")]
    assert license_classifiers == ["License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)"]


def test_license_file_holds_the_gpl_3_text():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    # The upstream text centers its title with spaces and a line break, so compare words.
    assert " ".join(text.split()[:6]) == "GNU GENERAL PUBLIC LICENSE Version 3,"
