"""E2E tests of Save as profile, Load and the profiles page (feature fleet-builds, FR 1 and 4)."""

import json
import re

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import open_tab

pytestmark = pytest.mark.e2e

CHANNEL_PSK = "deadbeef" * 8
REENTER_HINT = "Re-enter this value. The profile does not store it."
EMPTY_STATE = "No profiles yet. Save one from the build page."
MUST_REENTER_CLASS = re.compile(r"\bmust-reenter\b")


def _remove_every_profile(page: Page, base_url: str) -> None:
    """Delete each profile, because the tests of this module share one server."""
    for profile in page.request.get(f"{base_url}/api/v1/profiles").json():
        page.request.delete(f"{base_url}/api/v1/profiles/{profile['id']}")


def _save_profile(page: Page, base_url: str, name: str) -> dict:
    config = {"owner_long_name": "Ridge Node", "channel_0[psk]": CHANNEL_PSK}
    response = page.request.post(f"{base_url}/api/v1/profiles", data={"name": name, "config": config})
    assert response.status == 201
    return response.json()


def test_saved_profile_loads_after_a_reload_with_the_psk_marked_must_reenter(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    page.goto(f"{live_server}/")
    page.fill("#ownerLongName", "Ridge Node")
    open_tab(page, "channels")
    page.fill("#channel0PSK", CHANNEL_PSK)
    page.fill("#profileName", "Ridge SAR base")

    page.click("#saveProfileBtn")

    expect(page.locator("#profile-status")).to_contain_text("Saved the profile Ridge SAR base.")
    page.reload()
    open_tab(page, "device")
    page.fill("#ownerLongName", "")
    page.select_option("#profileSelect", label="Ridge SAR base")

    page.click("#loadProfileBtn")

    expect(page.locator("#config-tab-channels")).to_have_attribute("aria-selected", "true")
    psk = page.locator("#channel0PSK")
    expect(psk).to_have_value("")
    expect(psk).to_have_class(MUST_REENTER_CLASS)
    expect(page.locator("#reenter-channel0PSK")).to_have_text(REENTER_HINT)
    expect(page.locator("#reenter-channel0PSK")).to_be_visible()
    expect(page.locator("#profile-status")).to_contain_text("Re-enter 1 secret: channel_0[psk].")
    expect(page.locator("#ownerLongName")).to_have_value("Ridge Node")


def test_typing_in_a_must_reenter_field_removes_its_hint(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    profile = _save_profile(page, live_server, "Typed")
    page.goto(f"{live_server}/?profile={profile['id']}")
    expect(page.locator("#reenter-channel0PSK")).to_be_visible()

    page.fill("#channel0PSK", CHANNEL_PSK)

    expect(page.locator("#reenter-channel0PSK")).to_have_count(0)


def test_save_with_a_name_in_use_shows_the_conflict(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    _save_profile(page, live_server, "Taken")
    page.goto(f"{live_server}/")
    page.fill("#profileName", "Taken")

    page.click("#saveProfileBtn")

    expect(page.locator("#profile-status")).to_contain_text("A profile with the name Taken exists")


def test_profiles_page_load_opens_the_build_page_with_the_profile(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    _save_profile(page, live_server, "Linked")
    page.goto(f"{live_server}/profiles")

    page.get_by_role("link", name="Load", exact=True).click()

    expect(page.locator("#ownerLongName")).to_have_value("Ridge Node")
    expect(page.locator("#profile-status")).to_contain_text("Loaded the profile Linked.")


def test_profiles_page_renames_a_profile(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    profile = _save_profile(page, live_server, "Old name")
    page.goto(f"{live_server}/profiles")

    page.click(f'[data-rename="{profile["id"]}"]')
    page.fill(f"#rename-{profile['id']}", "New name")
    page.get_by_role("button", name="Save name").click()

    expect(page.locator("#profile-status")).to_contain_text("Renamed the profile to New name.")
    expect(page.locator("#profile-list .profile-name")).to_have_text(["New name"])


def test_profiles_page_exports_the_profile_as_json(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    _save_profile(page, live_server, "Exported")
    page.goto(f"{live_server}/profiles")

    with page.expect_download() as download:
        page.get_by_role("link", name="Export JSON").click()

    exported = json.loads(download.value.path().read_text())
    assert exported["name"] == "Exported"
    assert exported["must_reenter"] == ["channel_0[psk]"]
    assert CHANNEL_PSK not in json.dumps(exported)


def test_profiles_page_deletes_a_profile_only_after_the_confirm(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    profile = _save_profile(page, live_server, "Doomed")
    page.goto(f"{live_server}/profiles")
    confirm = page.locator(".profile-delete-confirm")

    page.click(f'[data-delete="{profile["id"]}"]')
    expect(confirm).to_be_visible()
    expect(confirm).to_contain_text("Delete the profile Doomed? You cannot undo this.")
    page.click(".profile-delete-confirm [data-cancel]")
    expect(confirm).to_be_hidden()
    expect(page.locator("#profile-list .profile-name")).to_have_text(["Doomed"])

    page.click(f'[data-delete="{profile["id"]}"]')
    page.click(f'[data-confirm-delete="{profile["id"]}"]')

    expect(page.locator("#profile-status")).to_contain_text("Deleted the profile Doomed.")
    expect(page.locator("#profile-list")).to_contain_text(EMPTY_STATE)


def test_load_shows_the_warning_of_the_profile_response(page: Page, live_server: str):
    _remove_every_profile(page, live_server)
    profile = _save_profile(page, live_server, "Stored")
    warning = "Sign in as admin to load the stored secrets."

    # The route answers as for a profile with stored secrets and no admin session (FR-fleet-builds-3, #62).
    def answer(route):
        route.fulfill(json={**profile, "stores_secrets": True, "warning": warning})

    page.route(f"**/api/v1/profiles/{profile['id']}", answer)
    page.goto(f"{live_server}/?profile={profile['id']}")

    expect(page.locator("#profile-status")).to_contain_text(warning)
    expect(page.locator("#reenter-channel0PSK")).to_be_visible()
