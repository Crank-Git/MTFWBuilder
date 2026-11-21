"""Fixtures for the E2E tests, which drive a real browser against a real server process."""

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest
import yaml

from tests.conftest import _ADMIN_PASSWORD_HASH, REPO_ROOT, SECRET_KEY

STARTUP_TIMEOUT_SECONDS = 20


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

    The server reads every path from a directory under `tmp_path`. The firmware source is an
    empty directory, because the fake `pio` reads only the userPrefs file that the build writes.
    The server stops when the module ends.
    """
    base = tmp_path_factory.mktemp("e2e")
    (base / "config.yaml").write_text(
        yaml.safe_dump({"admin_password_hash": _ADMIN_PASSWORD_HASH, "secret_key": SECRET_KEY})
    )
    (base / "firmware").mkdir()
    env = {
        **os.environ,
        "PATH": str(REPO_ROOT / "tests" / "fakes") + os.pathsep + os.environ["PATH"],
        "MTFW_BASE_DIR": str(base),
        "MTFW_TEMP_DIR": str(base / "tmp"),
        "MTFW_DATABASE_PATH": str(base / "mtfwbuilder.db"),
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
