"""E2E tests of the form for the chosen firmware version (FR-config-coverage-16, criterion 8)."""

import shutil
import sys

import pytest
from playwright.sync_api import Page, expect

from mtfwbuilder.database import record_firmware_source
from tests.conftest import REPO_ROOT
from tests.e2e.conftest import _free_port, _serve, _server_env, open_tab
from tests.test_config_mesh_beacon import UPSTREAM_KEYS as MESH_BEACON_KEYS

pytestmark = pytest.mark.e2e

FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "firmware"
OLD = "v2.7.20.6658ec2"
NEW = "v2.8.2.d5f1619"


@pytest.fixture(scope="module")
def versions_server(tmp_path_factory):
    """Return the base URL of a server with two firmware sources, each a copy of the fixture firmware source.

    The stable `OLD` is the default and reads no mesh beacon key. The alpha `NEW` also reads the mesh beacon keys.
    """
    base = tmp_path_factory.mktemp("e2e-versions")
    env = _server_env(base)
    for version, channel, keys in ((OLD, "stable", ()), (NEW, "alpha", sorted(MESH_BEACON_KEYS))):
        source_dir = base / "firmware" / version
        source_dir.mkdir(parents=True)
        shutil.copy(FIXTURE_DIR / "userPrefs.jsonc", source_dir)
        shutil.copytree(FIXTURE_DIR / "src", source_dir / "src")
        (source_dir / "src" / "newer.cpp").write_text("".join(f"#ifdef {key}\n#endif\n" for key in keys))
        record = {
            "version": version,
            "channel": channel,
            "path": str(source_dir),
            "installed_at": "2026-11-01T00:00:00+00:00",
            "size_bytes": 0,
            "status": "ready",
        }
        record_firmware_source(base / "mtfwbuilder.db", record)
    port = _free_port()
    command = [sys.executable, "-m", "uvicorn", "mtfwbuilder.main:app", "--host", "127.0.0.1", "--port", str(port)]
    with _serve(command, env, port) as url:
        yield url


def test_with_firmware_version_v2_7_20_chosen_no_mesh_beacon_control_shows(page: Page, versions_server: str):
    page.goto(f"{versions_server}/")

    expect(page.locator("#meshBeaconSection fieldset[data-userprefs-key]:visible")).to_have_count(0)
    expect(page.locator("#config-pane-mesh-beacon .unread-note")).to_have_text(
        f"{len(MESH_BEACON_KEYS)} settings are not available in {OLD}."
    )


def test_a_change_of_firmware_version_shows_the_controls_that_the_version_reads(page: Page, versions_server: str):
    page.goto(f"{versions_server}/")

    page.select_option("#firmware_version", NEW)
    open_tab(page, "mesh-beacon")

    expect(page.locator("#configForm")).to_have_attribute("data-firmware-version", NEW)
    expect(page.locator("#meshBeaconMessage")).to_be_visible()
    expect(page.locator("#config-pane-mesh-beacon .unread-note")).to_be_hidden()


def test_preview_after_a_change_back_to_v2_7_20_sends_no_mesh_beacon_value(page: Page, versions_server: str):
    page.goto(f"{versions_server}/")
    page.select_option("#firmware_version", NEW)
    open_tab(page, "mesh-beacon")
    page.fill("#meshBeaconMessage", "Join us")

    page.select_option("#firmware_version", OLD)
    expect(page.locator("#configForm")).to_have_attribute("data-firmware-version", OLD)
    page.click("#previewBtn")

    expect(page.locator("#preview-container")).to_contain_text("USERPREFS_CHANNELS_TO_WRITE")
    expect(page.locator("#preview-container")).not_to_contain_text("USERPREFS_MESH_BEACON_MESSAGE")


def test_channel_tabs_follow_the_channel_slots_of_the_firmware_version(page: Page, versions_server: str):
    page.goto(f"{versions_server}/")
    open_tab(page, "channels")

    page.select_option("#channelsToWrite", "3")

    expect(page.locator("#channel-2-tab")).to_be_visible()
    expect(page.locator("#channel-3-tab")).to_be_hidden()
    expect(page.locator('#channelsToWrite option[value="4"]')).to_be_disabled()
