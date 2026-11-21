"""Tests for the Modules tab of the config form (feature module-settings, FR 1 to 4 and 7)."""

import re

import pytest
from httpx import ASGITransport, AsyncClient

from mtfwbuilder.models import MODULE_BOOL_FIELDS, MODULE_ENUMS, MODULE_FIELDS, MODULE_NUMBER_LIMITS
from tests.test_config_tabs import _page

PANE = "config-pane-modules"
NOTE = "These settings are not compiled into the firmware. Push config sends them to a connected device."
HARDWARE_HELP = "Leave empty to keep the pin of the device. A wrong pin can drive the wrong GPIO."
KEEP = "Keep device value"
NEIGHBOR_INFO_MESSAGE = "The firmware uses at least 14400 seconds (4 hours)."

# The cards of FR 1 in page order, with the form field prefix of the section.
CARDS = (
    ("serial", "Serial", "serial_"),
    ("store-forward", "Store and forward", "store_forward_"),
    ("range-test", "Range test", "range_test_"),
    ("ext-notify", "External notification", "ext_notify_"),
    ("neighbor-info", "Neighbor info", "neighbor_info_"),
    ("detection-sensor", "Detection sensor", "detection_sensor_"),
    ("paxcounter", "Paxcounter", "paxcounter_"),
)
# The fields with HW in the Behaviour rules table.
HARDWARE_FIELDS = (
    "serial_rxd",
    "serial_txd",
    "serial_override_console_serial_port",
    "ext_notify_output",
    "ext_notify_output_vibra",
    "ext_notify_output_buzzer",
    "ext_notify_active",
    "ext_notify_use_pwm",
    "ext_notify_use_i2s_as_buzzer",
    "detection_sensor_monitor_pin",
    "detection_sensor_use_pullup",
)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _control(tree, field):
    [control] = [e for e in tree.elements if e["attrs"].get("name") == field and e["tag"] in ("input", "select")]
    return control


def _options(tree, control):
    return [e for e in tree.elements if e["tag"] == "option" and control["attrs"]["id"] in e["parents"]]


def _fieldset(tree, control):
    """Return the fieldset that holds a control, so a test can read its label and help text."""
    [fieldset] = [
        e
        for e in tree.elements
        if e["tag"] == "fieldset" and e["attrs"].get("data-module-field") == control["attrs"]["name"]
    ]
    return fieldset


async def test_modules_tab_shows_the_note_that_push_config_sends_the_settings(client):
    _, tree = await _page(client)
    note = tree.by_id("moduleSettingsNote")
    assert " ".join(note["text"].split()) == NOTE
    assert PANE in note["parents"]


async def test_modules_tab_has_one_card_for_each_section_in_order(client):
    _, tree = await _page(client)
    cards = [e for e in tree.elements if e["attrs"].get("id", "").startswith("module-card-")]
    assert [e["attrs"]["id"] for e in cards] == [f"module-card-{card_id}" for card_id, _, _ in CARDS]
    for card, (_, title, _) in zip(cards, CARDS, strict=True):
        assert PANE in card["parents"]
        assert " ".join(card["text"].split()).startswith(title)


@pytest.mark.parametrize("field", MODULE_FIELDS)
async def test_each_module_field_is_in_the_card_of_its_section(client, field):
    _, tree = await _page(client)
    [card_id] = [card_id for card_id, _, prefix in CARDS if field.startswith(prefix)]
    assert f"module-card-{card_id}" in _control(tree, field)["parents"]


@pytest.mark.parametrize("field", MODULE_FIELDS)
async def test_each_module_field_has_an_error_slot(client, field):
    _, tree = await _page(client)
    slot = tree.by_id(f"error-{field}")
    assert "field-error" in slot["attrs"]["class"].split()
    assert PANE in slot["parents"]


async def test_modules_tab_has_no_userprefs_key_so_the_key_scan_does_not_hide_a_field(client):
    _, tree = await _page(client)
    assert [e for e in tree.elements if PANE in e["parents"] and "data-userprefs-key" in e["attrs"]] == []


@pytest.mark.parametrize("field", (*MODULE_BOOL_FIELDS, *MODULE_ENUMS))
async def test_each_module_select_is_empty_by_default_with_the_keep_device_value_option(client, field):
    _, tree = await _page(client)
    control = _control(tree, field)
    assert control["tag"] == "select"
    options = _options(tree, control)
    assert options[0]["attrs"]["value"] == ""
    assert options[0]["text"].strip() == KEEP
    assert [o for o in options if "selected" in o["attrs"]] in ([], [options[0]])


@pytest.mark.parametrize("field", MODULE_BOOL_FIELDS)
async def test_each_module_toggle_offers_true_and_false(client, field):
    _, tree = await _page(client)
    assert [o["attrs"]["value"] for o in _options(tree, _control(tree, field))] == ["", "true", "false"]


@pytest.mark.parametrize(("field", "values"), MODULE_ENUMS.items())
async def test_each_module_enum_offers_the_values_that_the_validator_accepts(client, field, values):
    _, tree = await _page(client)
    assert [o["attrs"]["value"] for o in _options(tree, _control(tree, field))] == ["", *values]


# The browser blocks a submit below `min` with its own message, so the FR 11 message of the server never shows.
NO_MIN_FIELDS = {"neighbor_info_update_interval"}


@pytest.mark.parametrize(
    ("field", "limits"), [(name, limits) for name, limits in MODULE_NUMBER_LIMITS.items() if name not in NO_MIN_FIELDS]
)
async def test_each_module_number_is_empty_with_the_range_that_the_validator_accepts(client, field, limits):
    _, tree = await _page(client)
    control = _control(tree, field)
    assert control["attrs"]["type"] == "number"
    assert "value" not in control["attrs"]
    assert (int(control["attrs"]["min"]), int(control["attrs"]["max"])) == limits


async def test_neighbor_info_interval_input_has_no_min_so_the_server_message_shows(client):
    _, tree = await _page(client)
    control = _control(tree, "neighbor_info_update_interval")
    assert "min" not in control["attrs"]
    assert int(control["attrs"]["max"]) == MODULE_NUMBER_LIMITS["neighbor_info_update_interval"][1]


async def test_detection_sensor_name_is_an_empty_text_field(client):
    _, tree = await _page(client)
    control = _control(tree, "detection_sensor_name")
    assert control["attrs"]["type"] == "text"
    assert "value" not in control["attrs"]


@pytest.mark.parametrize("field", MODULE_FIELDS)
async def test_only_a_hardware_field_has_the_hardware_label_and_the_pin_help_text(client, field):
    _, tree = await _page(client)
    control = _control(tree, field)
    fieldset = _fieldset(tree, control)
    [label] = [e for e in tree.elements if e["tag"] == "label" and e["attrs"].get("for") == control["attrs"]["id"]]
    text = " ".join(fieldset["text"].split())
    if field in HARDWARE_FIELDS:
        assert label["text"].strip().endswith("(hardware)")
        assert HARDWARE_HELP in text
    else:
        assert "(hardware)" not in label["text"]
        assert HARDWARE_HELP not in text


async def test_a_wrong_module_field_puts_its_message_in_the_error_slot_of_the_field(client):
    data = {"neighbor_info_update_interval": "3600", "serial_rxd": "-1"}

    response = await client.post("/api/v1/preview", data=data, headers={"HX-Request": "true"})

    assert response.status_code == 422
    assert 'id="error-neighbor_info_update_interval"' in response.text
    assert 'data-field="neighbor_info_update_interval"' in response.text
    assert NEIGHBOR_INFO_MESSAGE in response.text
    assert 'id="error-serial_rxd"' in response.text
    _, tree = await _page(client)
    assert PANE in tree.by_id("error-neighbor_info_update_interval")["parents"]
    assert PANE in tree.by_id("error-serial_rxd")["parents"]


async def test_a_profile_with_module_fields_saves_loads_and_exports_them_unchanged(client):
    config = {
        "owner_long_name": "Ridge Node",
        "serial_enabled": "true",
        "serial_mode": "NMEA",
        "serial_rxd": "44",
        "neighbor_info_update_interval": "14400",
        "detection_sensor_name": "Door",
        "paxcounter_wifi_threshold": "-80",
    }
    saved = (await client.post("/api/v1/profiles", json={"name": "Modules", "config": config})).json()

    loaded = (await client.get(f"/api/v1/profiles/{saved['id']}")).json()
    exported = (await client.get(f"/api/v1/profiles/{saved['id']}/export")).json()

    # The profile route adds the default of each field that the request leaves out, for example `channels_to_write`.
    assert config.items() <= loaded["config"].items()
    assert exported["config"] == loaded["config"]
    assert loaded["must_reenter"] == []


def _error_slots(html: str) -> dict[str, str]:
    """Return the text of each out-of-band error slot in a 422 fragment, by its field."""
    return dict(re.findall(r'<div id="error-([^"]+)"[^>]*hx-swap-oob="true">([^<]*)</div>', html))


async def test_neighbor_info_interval_of_14399_shows_only_the_firmware_message_under_the_field(client):
    data = {"neighbor_info_update_interval": "14399"}

    response = await client.post("/api/v1/preview", data=data, headers={"HX-Request": "true"})

    assert response.status_code == 422
    assert _error_slots(response.text)["neighbor_info_update_interval"] == NEIGHBOR_INFO_MESSAGE


async def test_no_build_form_field_error_begins_with_the_pydantic_prefix(client):
    data = {
        "neighbor_info_update_interval": "3600",
        "serial_rxd": "-1",
        "lora_tx_power": "x",
        "node_info_reply_suppress_secs": "x",
        "channel_0[name]": "a name longer than twelve bytes",
    }

    response = await client.post("/api/v1/preview", data=data, headers={"HX-Request": "true"})

    assert response.status_code == 422
    slots = _error_slots(response.text)
    assert len(slots) == len(data)
    items = re.findall(r"<li>(?:<code>[^<]*</code>: )?([^<]*)</li>", response.text)
    assert len(items) == len(data)
    assert [m for m in [*slots.values(), *items] if m.startswith("Value error, ")] == []
