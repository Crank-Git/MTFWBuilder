"""Fixtures for the E2E tests, which drive a real browser against a real server process."""

import http.server
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing, contextmanager

import pytest
import yaml

from mtfwbuilder.database import SCHEMA, record_firmware_source
from mtfwbuilder.services.registry_generator import registry_entries
from tests.conftest import _ADMIN_PASSWORD_HASH, FIRMWARE_VERSION, REPO_ROOT, SECRET_KEY

STARTUP_TIMEOUT_SECONDS = 20
FIRMWARE_LIST = REPO_ROOT / "tests" / "fixtures" / "firmware" / "firmware_list.json"
PROJECT_CONFIG = REPO_ROOT / "tests" / "fixtures" / "firmware" / "project_config.json"
ALPHA_VERSION = "v2.8.2.d5f1619"
# An older stable firmware version, so the default firmware version stays `FIRMWARE_VERSION`.
NO_REGISTRY_VERSION = "v2.7.20.0000000"


# Chromium writes each Content-Security-Policy violation and each bad Permissions-Policy entry to the console.
POLICY_CONSOLE_TEXT = ("Content Security Policy", "Permissions-Policy", "Permissions policy")


@pytest.fixture(autouse=True)
def _no_policy_console_messages(request):
    """Fail an E2E test when the browser reports a policy problem on the console (FR-platform-modernization-19)."""
    if "page" not in request.fixturenames:
        yield
        return
    page = request.getfixturevalue("page")
    messages = []
    page.on(
        "console", lambda msg: messages.append(msg.text) if any(t in msg.text for t in POLICY_CONSOLE_TEXT) else None
    )
    yield
    assert messages == []


def open_tab(page, tab_id: str) -> None:
    """Select the config tab `tab_id`, for example `security`, so its controls show."""
    page.click(f"#config-tab-{tab_id}")


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


def _server_env(base) -> dict:
    """Return the environment of a server that reads every path from `base` and builds with the fake `pio`."""
    (base / "config.yaml").write_text(
        yaml.safe_dump({"admin_password_hash": _ADMIN_PASSWORD_HASH, "secret_key": SECRET_KEY})
    )
    # The Playwright sync API runs an event loop in this process, so the fixture creates the tables without asyncio.
    with closing(sqlite3.connect(base / "mtfwbuilder.db")) as db:
        db.executescript(SCHEMA)
    return {
        **os.environ,
        "PATH": str(REPO_ROOT / "tests" / "fakes") + os.pathsep + os.environ["PATH"],
        "MTFW_BASE_DIR": str(base),
        "MTFW_TEMP_DIR": str(base / "tmp"),
        "MTFW_DATABASE_PATH": str(base / "mtfwbuilder.db"),
        "MTFW_FIRMWARE_DIR": str(base / "firmware"),
        # The push config page uses `window.__pushConfigTestDevice` only when this setting is on.
        "MTFW_PUSH_TEST_HOOKS": "true",
    }


@contextmanager
def _serve(command: list[str], env: dict, port: int):
    """Yield the base URL of the server process that `command` starts, and stop the process after the yield."""
    url = f"http://127.0.0.1:{port}"
    with subprocess.Popen(command, cwd=REPO_ROOT, env=env) as server:
        try:
            _wait_until_up(f"{url}/", server)
            yield url
        finally:
            server.terminate()
            server.wait(timeout=10)


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Return the base URL of a uvicorn server that builds with the fake `pio`.

    The server reads every path from a directory under `tmp_path`. Two firmware sources have a ready record:
    the stable `FIRMWARE_VERSION` and the alpha `ALPHA_VERSION`. Each is a directory `firmware/<tag>/` that holds
    no firmware, because the fake `pio` reads only the userPrefs file and `.fake-pio-version`.
    The stable firmware version has the registry of `tests/fixtures/firmware/project_config.json`.
    The alpha firmware version has that registry with `heltec-v4` renamed to `heltec-v4-r2`.
    A third ready firmware version `NO_REGISTRY_VERSION` has no registry rows, as an import before feature 10 gives.
    The server stops when the module ends.
    """
    base = tmp_path_factory.mktemp("e2e")
    env = _server_env(base)
    source_dir = base / "firmware" / FIRMWARE_VERSION
    source_dir.mkdir(parents=True)
    record = {
        "version": FIRMWARE_VERSION,
        "channel": "stable",
        "path": str(source_dir),
        "installed_at": "2026-11-01T00:00:00+00:00",
        "size_bytes": 0,
        "status": "ready",
    }
    database_path = base / "mtfwbuilder.db"
    registry = registry_entries(json.loads(PROJECT_CONFIG.read_text()))
    record_firmware_source(database_path, record, registry)
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
    # Upstream can rename an env between firmware versions, so the alpha registry holds `heltec-v4` under a new name.
    renamed = [{**entry, "env": "heltec-v4-r2"} if entry["env"] == "heltec-v4" else entry for entry in registry]
    record_firmware_source(database_path, {**record, **alpha}, renamed)
    old_dir = base / "firmware" / NO_REGISTRY_VERSION
    old_dir.mkdir()
    old = {"version": NO_REGISTRY_VERSION, "path": str(old_dir), "installed_at": "2026-10-01T00:00:00+00:00"}
    record_firmware_source(database_path, {**record, **old})
    port = _free_port()
    command = [sys.executable, "-m", "uvicorn", "mtfwbuilder.main:app", "--host", "127.0.0.1", "--port", str(port)]
    with _serve(command, env, port) as url:
        yield url


class _FirmwareListHandler(http.server.BaseHTTPRequestHandler):
    """Answer each GET with the recorded response of api.meshtastic.org."""

    def do_GET(self):
        body = FIRMWARE_LIST.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


# The server process imports the app, then points the firmware catalog at the local list before uvicorn starts.
_CATALOG_LAUNCHER = """
import sys
import uvicorn
from mtfwbuilder.services import firmware_catalog
firmware_catalog.MESHTASTIC_LIST_URL = sys.argv[1]
uvicorn.run("mtfwbuilder.main:app", host="127.0.0.1", port=int(sys.argv[2]))
"""


@pytest.fixture(scope="module")
def empty_server(tmp_path_factory):
    """Return the base URL of a uvicorn server with no firmware source.

    The firmware catalog of the server reads `tests/fixtures/firmware/firmware_list.json` from a local
    HTTP server, so the test reaches no upstream. The servers stop when the module ends.
    """
    base = tmp_path_factory.mktemp("e2e-empty")
    env = _server_env(base)
    catalog = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FirmwareListHandler)
    threading.Thread(target=catalog.serve_forever, daemon=True).start()
    list_url = f"http://127.0.0.1:{catalog.server_address[1]}/github/firmware/list"
    port = _free_port()
    try:
        with _serve([sys.executable, "-c", _CATALOG_LAUNCHER, list_url, str(port)], env, port) as url:
            yield url
    finally:
        catalog.shutdown()


RELEASES = REPO_ROOT / "tests" / "fixtures" / "firmware" / "releases.json"


class _GitHubOnlyHandler(http.server.BaseHTTPRequestHandler):
    """Answer the GitHub release list from the recorded fixture, and answer each other GET with 404.

    The firmware list path gets 404 too, so the catalog reads the GitHub list, which holds the revoked tags.
    A zipball request gets 404, so an install fails at the download and reaches no upstream.
    """

    def do_GET(self):
        if not self.path.startswith("/releases"):
            self.send_error(404)
            return
        body = RELEASES.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


# The catalog module copies `GITHUB_REPO_API` at import, so the launcher sets it in both modules.
_GITHUB_LAUNCHER = """
import sys
import uvicorn
from mtfwbuilder.services import firmware_catalog, firmware_updater
firmware_catalog.MESHTASTIC_LIST_URL = sys.argv[1] + "/github/firmware/list"
firmware_catalog.GITHUB_REPO_API = firmware_updater.GITHUB_REPO_API = sys.argv[1]
uvicorn.run("mtfwbuilder.main:app", host="127.0.0.1", port=int(sys.argv[2]))
"""


@pytest.fixture(scope="module")
def github_server(tmp_path_factory):
    """Return the base URL of a uvicorn server with no firmware source, whose upstream is a local GitHub stand-in.

    The catalog gets its list from `tests/fixtures/firmware/releases.json`, and each download fails with 404.
    The servers stop when the module ends.
    """
    base = tmp_path_factory.mktemp("e2e-github")
    env = _server_env(base)
    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _GitHubOnlyHandler)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}"
    port = _free_port()
    try:
        with _serve([sys.executable, "-c", _GITHUB_LAUNCHER, upstream_url, str(port)], env, port) as url:
            yield url
    finally:
        upstream.shutdown()
