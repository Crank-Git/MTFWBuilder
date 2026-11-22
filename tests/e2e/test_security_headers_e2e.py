"""E2E check that the main pages run under the Content-Security-Policy (FR-platform-modernization-19).

The autouse fixture `_no_policy_console_messages` fails each test when the console reports a policy problem.
"""

import pytest
from playwright.sync_api import Page, expect

from tests.conftest import ADMIN_PASSWORD

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("path", ["/", "/profiles", "/fleets", "/push-config"])
def test_page_loads_its_scripts_under_the_policy(page: Page, live_server: str, path: str):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    page.goto(f"{live_server}{path}")
    page.wait_for_load_state("networkidle")

    # htmx sets `htmx.version` only after its script runs.
    assert page.evaluate("typeof htmx !== 'undefined' && htmx.version") == "2.0.11"
    assert page.evaluate("document.documentElement.getAttribute('data-bs-theme')") == "dark"
    assert errors == []


def test_push_config_page_loads_the_import_map_modules(page: Page, live_server: str):
    page.goto(f"{live_server}/push-config")

    # The page shows the connect controls only after `push-config.js` imports `@meshtastic/core`.
    expect(page.locator("#push-connect")).to_be_visible()


def test_signed_in_admin_dashboard_loads_under_the_policy(page: Page, live_server: str):
    page.goto(f"{live_server}/admin")
    page.fill("#adminKey", ADMIN_PASSWORD)

    page.click("button[type=submit]:has-text('Sign in')")

    expect(page.locator("#firmware-sources")).to_be_visible()
    page.wait_for_load_state("networkidle")
