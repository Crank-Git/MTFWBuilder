"""Shared test fixtures."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from mtfwbuilder.auth import CSRF_COOKIE, SESSION_COOKIE, create_session_token, csrf_token
from mtfwbuilder.config import _hash_password, load_settings
from mtfwbuilder.database import init_db, record_firmware_source
from mtfwbuilder.main import create_app
from mtfwbuilder.rate_limit import configure_rate_limits, limiter
from mtfwbuilder.services.build_service import BuildQueue
from mtfwbuilder.services.device_registry import DeviceRegistry
from mtfwbuilder.services.firmware_catalog import FirmwareCatalog
from mtfwbuilder.services.firmware_updater import FirmwareInstalls

REPO_ROOT = Path(__file__).resolve().parent.parent

# A local config.yaml often sets another password, so the tests own theirs.
ADMIN_PASSWORD = "test-admin-password"
SECRET_KEY = "test-secret-key"
# The firmware version of the firmware fixture and of the fake `pio`.
FIRMWARE_VERSION = "v2.7.26.54e0d8d"

# bcrypt is slow by design, so the suite hashes the password once.
_ADMIN_PASSWORD_HASH = _hash_password(ADMIN_PASSWORD)


# A fixed CSRF cookie value, so a test can sign the CSRF token without a page request.
_CSRF_NONCE = "test-csrf-nonce"


def sign_in_admin(client, settings):
    """Return the CSRF token for admin forms, after the client gets an admin session and the CSRF cookie."""
    # A cookie from a response has a domain, so a second cookie of that name would go with each request.
    for name, value in ((SESSION_COOKIE, create_session_token(settings)), (CSRF_COOKIE, _CSRF_NONCE)):
        client.cookies.delete(name)
        client.cookies.set(name, value)
    return csrf_token(_CSRF_NONCE, settings)


def pytest_collection_modifyitems(items):
    """Move the `e2e` tests to the end of the run.

    The Playwright sync API keeps its event loop running until the session ends.
    An async test after the first `e2e` test then fails with "Runner.run() cannot be called from a running event loop".
    """
    items.sort(key=lambda item: item.get_closest_marker("e2e") is not None)


@pytest.fixture
def variants_path():
    """Path to the real variants.yaml file."""
    return REPO_ROOT / "devices" / "variants.yaml"


@pytest.fixture
def fake_pio(monkeypatch):
    """Return the path of the fake `pio` executable, after it is put first on `PATH`.

    The fake writes upstream-named artifacts into `.pio/build/<env>/` of its working
    directory. `FAKE_PIO_FAIL=1` makes it fail, and `FAKE_PIO_SLEEP=<seconds>` delays it.
    """
    fakes_dir = REPO_ROOT / "tests" / "fakes"
    monkeypatch.setenv("PATH", str(fakes_dir) + os.pathsep + os.environ["PATH"])
    return fakes_dir / "pio"


@pytest.fixture
def temp_dir():
    """Return a temporary directory that the test can write artifacts into."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def admin_password():
    """Return the admin password that the `settings_env` fixture configures."""
    return ADMIN_PASSWORD


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    """Return a directory under `tmp_path` that is both the base directory and the data directory.

    The directory holds a `config.yaml` with a known admin password and secret key.
    The registry stays the real `devices/variants.yaml`, which the tests only read.
    """
    monkeypatch.setenv("MTFW_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MTFW_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MTFW_DEVICES_FILE", str(REPO_ROOT / "devices" / "variants.yaml"))
    config = {"admin_password_hash": _ADMIN_PASSWORD_HASH, "secret_key": SECRET_KEY}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(config))
    return tmp_path


@pytest.fixture
def settings(settings_env):
    """Return the settings that `load_settings()` reads from the `settings_env` directory."""
    return load_settings()


@pytest.fixture
def app(settings):
    """Return an app with the lifespan state, built from the `settings` fixture.

    The build queue has no build workers. A test that needs them calls `build_queue.start()`.
    """
    # A test app has no lifespan, and a build request reads the `firmware_sources` table.
    asyncio.run(init_db(settings))
    app = create_app()
    app.state.settings = settings
    configure_rate_limits(settings)
    app.state.device_registry = DeviceRegistry(settings.devices_file)
    app.state.build_queue = BuildQueue(settings)
    app.state.firmware_catalog = FirmwareCatalog()
    app.state.firmware_installs = FirmwareInstalls(
        settings, app.state.build_queue, resolver=app.state.firmware_catalog.resolve
    )
    # The rate limit counts across tests, so each test starts with an empty count.
    limiter.reset()
    return app


@pytest.fixture
def firmware_source(settings):
    """Return the directory `<firmware_dir>/<tag>/` of one installed firmware source, after its record is written."""
    source_dir = settings.firmware_dir / FIRMWARE_VERSION
    source_dir.mkdir(parents=True)
    asyncio.run(init_db(settings))
    record = {
        "version": FIRMWARE_VERSION,
        "channel": "stable",
        "path": str(source_dir),
        "installed_at": "2026-11-01T00:00:00+00:00",
        "size_bytes": 0,
        "status": "ready",
    }
    record_firmware_source(settings.database_path, record)
    return source_dir


@pytest.fixture
def sample_config_data():
    """Minimal valid configuration data."""
    return {
        "owner_short_name": "TST",
        "owner_long_name": "Test Node",
        "channels_to_write": "1",
        "channel_0[name]": "Primary",
        "channel_0[psk]": "deadbeef" * 8,
    }
