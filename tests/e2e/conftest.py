"""Fixtures for the E2E tests, which drive a real browser against a real server process."""

import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing

import pytest
import yaml

from mtfwbuilder.database import SCHEMA, record_firmware_source
from tests.conftest import _ADMIN_PASSWORD_HASH, FIRMWARE_VERSION, REPO_ROOT, SECRET_KEY

STARTUP_TIMEOUT_SECONDS = 20
ALPHA_VERSION = "v2.8.2.d5f1619"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_up(url: str, server: subprocess.Popen) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        assert server.poll() is None, f"the server stopped with exit code {server.returncode}"
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(0.1)
    raise AssertionError(f"the server did not answer at {url}")


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Return the base URL of a uvicorn server that builds with the fake `pio`.

    The server reads every path from a directory under `tmp_path`. Two firmware sources have a ready record:
    the stable `FIRMWARE_VERSION` and the alpha `ALPHA_VERSION`. Each is a directory `firmware/<tag>/` that holds
    no firmware, because the fake `pio` reads only the userPrefs file and `.fake-pio-version`.
    The server stops when the module ends.
    """
    base = tmp_path_factory.mktemp("e2e")
    (base / "config.yaml").write_text(
        yaml.safe_dump({"admin_password_hash": _ADMIN_PASSWORD_HASH, "secret_key": SECRET_KEY})
    )
    source_dir = base / "firmware" / FIRMWARE_VERSION
    source_dir.mkdir(parents=True)
    database_path = base / "mtfwbuilder.db"
    # The Playwright sync API runs an event loop in this process, so the fixture creates the tables without asyncio.
    with closing(sqlite3.connect(database_path)) as db:
        db.executescript(SCHEMA)
    record = {
        "version": FIRMWARE_VERSION,
        "channel": "stable",
        "path": str(source_dir),
        "installed_at": "2026-11-01T00:00:00+00:00",
        "size_bytes": 0,
        "status": "ready",
    }
    record_firmware_source(database_path, record)
    # The fake `pio` reads `.fake-pio-version`, so the artifact names show which firmware source the build used.
    alpha_dir = base / "firmware" / ALPHA_VERSION
    alpha_dir.mkdir()
    (alpha_dir / ".fake-pio-version").write_text(ALPHA_VERSION.removeprefix("v"))
    alpha = {
        "version": ALPHA_VERSION,
        "channel": "alpha",
        "path": str(alpha_dir),
        "installed_at": "2026-11-10T00:00:00+00:00",
    }
    record_firmware_source(database_path, {**record, **alpha})
    env = {
        **os.environ,
        "PATH": str(REPO_ROOT / "tests" / "fakes") + os.pathsep + os.environ["PATH"],
        "MTFW_BASE_DIR": str(base),
        "MTFW_TEMP_DIR": str(base / "tmp"),
        "MTFW_DATABASE_PATH": str(database_path),
        "MTFW_FIRMWARE_DIR": str(base / "firmware"),
        "MTFW_DEVICES_FILE": str(REPO_ROOT / "devices" / "variants.yaml"),
    }
    port = _free_port()
    command = [sys.executable, "-m", "uvicorn", "mtfwbuilder.main:app", "--host", "127.0.0.1", "--port", str(port)]
    url = f"http://127.0.0.1:{port}"
    with subprocess.Popen(command, cwd=REPO_ROOT, env=env) as server:
        try:
            _wait_until_up(f"{url}/", server)
            yield url
        finally:
            server.terminate()
            server.wait(timeout=10)
