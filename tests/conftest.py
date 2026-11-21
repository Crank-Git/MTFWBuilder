"""Shared test fixtures."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from mtfwbuilder.config import _hash_password, load_settings
from mtfwbuilder.main import create_app
from mtfwbuilder.rate_limit import limiter
from mtfwbuilder.services.build_service import BuildQueue
from mtfwbuilder.services.device_registry import DeviceRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent

# A local config.yaml often sets another password, so the tests own theirs.
ADMIN_PASSWORD = "test-admin-password"
SECRET_KEY = "test-secret-key"

# bcrypt is slow by design, so the suite hashes the password once.
_ADMIN_PASSWORD_HASH = _hash_password(ADMIN_PASSWORD)


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
    """Return a base directory under `tmp_path` that the `MTFW_*` variables point at.

    The directory holds a `config.yaml` with a known admin password and secret key.
    The registry stays the real `devices/variants.yaml`, which the tests only read.
    """
    monkeypatch.setenv("MTFW_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("MTFW_TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setenv("MTFW_DATABASE_PATH", str(tmp_path / "mtfwbuilder.db"))
    monkeypatch.setenv("MTFW_FIRMWARE_DIR", str(tmp_path / "firmware"))
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
    app = create_app()
    app.state.settings = settings
    app.state.device_registry = DeviceRegistry(settings.devices_file)
    app.state.build_queue = BuildQueue(settings)
    # The rate limit counts across tests, so each test starts with an empty count.
    limiter.reset()
    return app


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
