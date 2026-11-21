"""Tests for the merged Docker Compose configuration of the production overlay (#101)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not on PATH")


def _merged_config(*files: str) -> dict:
    args = ["docker", "compose"]
    for name in files:
        args += ["-f", name]
    result = subprocess.run(
        [*args, "config", "--format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_production_overlay_publishes_no_app_port():
    config = _merged_config("docker-compose.yml", "docker-compose.prod.yml")
    assert config["services"]["mtfwbuilder"].get("ports", []) == []


def test_production_overlay_keeps_the_nginx_ports():
    config = _merged_config("docker-compose.yml", "docker-compose.prod.yml")
    published = {port["published"] for port in config["services"]["nginx"]["ports"]}
    assert published == {"80", "443"}


def test_no_compose_file_mounts_a_shared_build_cache():
    # The project sets no shared PlatformIO build cache, because the cache kept objects compiled with secrets.
    for files in (("docker-compose.yml",), ("docker-compose.yml", "docker-compose.prod.yml")):
        config = _merged_config(*files)
        assert "build_cache" not in json.dumps(config)
