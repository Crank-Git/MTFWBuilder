"""Coverage test: the form writes every userPrefs key that the fixture firmware source reads (FR-config-coverage-18).

The weekly upstream check replaces the fixture with upstream `develop`, so this test also fails for a new upstream key.
"""

import json
import re

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.models import ConfigRequest
from mtfwbuilder.services.jsonc_generator import KEY_FIELDS, generate_jsonc
from mtfwbuilder.services.userprefs_keys import read_keys
from tests.conftest import REPO_ROOT
from tests.test_userprefs_contract import FIXTURE_DIR, full_config

SPEC = REPO_ROOT / "docs" / "specs" / "spec.md"

# Upstream `src/mesh/NodeDB.cpp` at v2.8.2.d5f1619 fails the build with `#error` when one of these removed keys is set.
REMOVED_KEYS = frozenset(
    {
        "USERPREFS_MESH_BEACON_ON_PRESET",
        "USERPREFS_MESH_BEACON_ON_REGION",
        "USERPREFS_MESH_BEACON_ON_CHANNEL_NAME",
        "USERPREFS_MESH_BEACON_ON_CHANNEL_PSK",
        "USERPREFS_MESH_BEACON_ON_CHANNEL_NUM",
    }
)

# `read_keys` keeps string literals, so the `#error` text of the same guard gives these two partial names.
PARTIAL_NAMES = frozenset({"USERPREFS_MESH_BEACON_ON_", "USERPREFS_MESH_BEACON_TARGET_0_"})


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def non_goal_keys() -> frozenset[str]:
    """Return the userPrefs keys that the `Non-goals` section of the spec names."""
    section = re.search(r"^## Non-goals\n(.*?)^## ", SPEC.read_text(), re.S | re.M)[1]
    return frozenset(re.findall(r"USERPREFS_[A-Z0-9_]+", section))


def required_keys() -> frozenset[str]:
    """Return the userPrefs keys that the fixture firmware source reads and that the form must write."""
    return read_keys(FIXTURE_DIR) - non_goal_keys() - REMOVED_KEYS - PARTIAL_NAMES


async def form_controls(client) -> dict[str, set[str]]:
    """Return the names of the form fields in the control of each userPrefs key on the build page."""
    resp = await client.get("/")
    assert resp.status_code == 200
    controls: dict[str, set[str]] = {}
    for key, body in re.findall(r'<fieldset[^>]*data-userprefs-key="([^"]+)"[^>]*>(.*?)</fieldset>', resp.text, re.S):
        controls.setdefault(key, set()).update(re.findall(r'\bname="([^"]+)"', body))
    return controls


def field_name(location: tuple[str, ...]) -> str:
    """Return the form field name of a field location: `("channel_0", "psk")` gives `channel_0[psk]`."""
    return location[0] if len(location) == 1 else f"{location[0]}[{location[1]}]"


def test_non_goal_keys_hold_the_test_and_vendor_keys_of_the_spec():
    assert non_goal_keys() >= {
        "USERPREFS_UI_TEST_LOG",
        "USERPREFS_FIRMWARE_EDITION",
        "USERPREFS_MESH_BEACON_LEGACY_SPLIT",
    }


@pytest.mark.asyncio
async def test_form_has_a_control_for_every_key_that_the_fixture_firmware_reads(client):
    missing = sorted(required_keys() - set(await form_controls(client)))
    assert not missing, f"the form has no control for these userPrefs keys: {', '.join(missing)}"


@pytest.mark.asyncio
async def test_control_of_each_key_posts_the_field_that_writes_the_key(client):
    controls = await form_controls(client)
    wrong = sorted(key for key in required_keys() if field_name(KEY_FIELDS[key]) not in controls.get(key, set()))
    assert not wrong, f"the control of these keys does not post the field that writes them: {', '.join(wrong)}"


def test_generator_writes_every_key_that_the_fixture_firmware_reads():
    request = ConfigRequest(**full_config())
    written = set(json.loads(generate_jsonc(request.generator_input(), firmware_keys=read_keys(FIXTURE_DIR))))
    missing = sorted(required_keys() - written)
    assert not missing, f"the generator does not write these userPrefs keys: {', '.join(missing)}"
