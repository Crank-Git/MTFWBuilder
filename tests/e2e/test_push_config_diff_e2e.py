"""E2E tests of the device read and the diff on the push config page (feature push-config, FR 4, 5, 6 and 12).

Each test connects to the fake device of `test_push_config_e2e`, so no test opens a serial port.
"""

import base64
import json
import math
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Page, expect

from mtfwbuilder.models import ChannelConfig, ConfigRequest
from tests.conftest import ADMIN_PASSWORD
from tests.e2e.test_push_config_e2e import FAKE_DEVICE, SET_CALLS, device_calls, open_push_config

pytestmark = pytest.mark.e2e

MASK = "***"
NO_CHANGES = "The device already matches this profile."
CHANNEL_0_KEY = bytes(range(16))
OTHER_KEY = bytes(range(16, 32))
WIFI_PSK = "hunter2-wifi"
MQTT_PASSWORD = "mqtt-pass-77"

# A device that runs LoRa on US / MEDIUM_FAST with two channels, and the owner "Team A Lead".
DEVICE = {
    "config": {
        "device": {"role": 0, "rebroadcastMode": 0, "nodeInfoBroadcastSecs": 10800, "tzdef": "", "buttonGpio": 0},
        "lora": {
            "region": 1,
            "modemPreset": 4,
            "channelNum": 0,
            "ignoreMqtt": False,
            "txPower": 0,
            "txEnabled": True,
            "configOkToMqtt": False,
        },
        "network": {"wifiEnabled": True, "wifiSsid": "fieldnet", "wifiPsk": "device-wifi", "ipv6Enabled": False},
    },
    "moduleConfig": {"mqtt": {"enabled": True, "password": "device-mqtt"}},
    "channels": [
        {"index": 0, "role": 1, "settings": {"name": "LongFast", "psk": {"$bytes": list(CHANNEL_0_KEY)}}},
        {"index": 1, "role": 0, "settings": {"name": "", "psk": {"$bytes": []}}},
    ],
    "owner": {"longName": "Team A Lead", "shortName": "TAL"},
}

# The profile matches the device on region, role and owner, and differs on four fields.
PROFILE = {
    "lora_enabled": "true",
    "lora_region": "US",
    "lora_modem_preset": "LONG_FAST",
    "device_role": "CLIENT",
    "node_info_broadcast_secs": "900",
    "channels_to_write": "2",
    "channel_0[name]": "RidgeNet",
    "channel_0[psk]": base64.b64encode(CHANNEL_0_KEY).decode(),
    "channel_1[name]": "Ops",
    "owner_long_name": "Team A Lead",
    "owner_short_name": "TAL",
}

SECTIONS_JS = """() => [...document.querySelectorAll('#push-diff-sections [data-section]')].map(section => ({
  section: section.dataset.section,
  label: section.querySelector('label').textContent,
  checked: section.querySelector('input[type=checkbox]').checked,
  rows: [...section.querySelectorAll('tbody tr')].map(row => [...row.cells].map(cell => cell.textContent)),
}))"""


def record_requests(page: Page) -> list:
    """Return a list that gets each request of `page`."""
    requests = []

    # Playwright sets an attribute on the listener, so a bound method of a list cannot be the listener.
    def record(request) -> None:
        requests.append(request)

    page.on("request", record)
    return requests


def create_profile(page: Page, url: str, name: str, config: dict, headers: dict | None = None) -> dict:
    body = {"name": name, "config": config, "stores_secrets": headers is not None}
    response = page.request.post(f"{url}/api/v1/profiles", data=body, headers=headers)
    assert response.status == 201, response.text()
    return response.json()


def connect(page: Page, url: str, profile_name: str, read: dict = DEVICE) -> None:
    """Open the page with the fake device, choose the profile, and connect."""
    page.add_init_script(FAKE_DEVICE % json.dumps({"managed": False, "read": read}))
    open_push_config(page, url)
    page.select_option("#push-profile", label=profile_name)
    page.click("#push-connect-usb")
    expect(page.locator("#push-connected")).to_be_visible()


def diff_sections(page: Page) -> list[dict]:
    return page.evaluate(SECTIONS_JS)


def test_diff_lists_only_the_fields_that_differ(page: Page, live_server: str):
    create_profile(page, live_server, "Ridge SAR base", PROFILE)

    connect(page, live_server, "Ridge SAR base")

    expect(page.locator("#push-diff")).to_be_visible()
    sections = diff_sections(page)
    assert [(s["section"], s["label"]) for s in sections] == [
        ("config.device", "Device"),
        ("config.lora", "LoRa"),
        ("channels.0", "Channel 0"),
        ("channels.1", "Channel 1"),
    ]
    assert {s["section"]: s["rows"] for s in sections} == {
        "config.device": [["node_info_broadcast_secs", "10800", "900"]],
        "config.lora": [["modem_preset", "MEDIUM_FAST", "LONG_FAST"]],
        "channels.0": [["name", "LongFast", "RidgeNet"]],
        "channels.1": [["name", "", "Ops"]],
    }
    # Each section has a checkbox, so the operator selects the sections to push (FR-push-config-6).
    assert all(s["checked"] for s in sections)
    expect(page.locator("#push-no-changes")).to_be_hidden()
    assert SET_CALLS.isdisjoint(device_calls(page))


def test_profile_that_matches_the_device_shows_the_no_changes_message(page: Page, live_server: str):
    same = {"lora_enabled": "true", "lora_region": "US", "device_role": "CLIENT", "owner_long_name": "Team A Lead"}
    create_profile(page, live_server, "Same as device", same)

    connect(page, live_server, "Same as device")

    expect(page.locator("#push-no-changes")).to_have_text(NO_CHANGES)
    expect(page.locator("#push-diff")).to_be_hidden()


def test_choosing_another_profile_after_connect_shows_its_diff(page: Page, live_server: str):
    create_profile(page, live_server, "Owner only", {"owner_short_name": "NEW"})
    create_profile(page, live_server, "Region only", {"lora_enabled": "true", "lora_region": "EU_868"})
    connect(page, live_server, "Owner only")
    expect(page.locator("#push-diff")).to_be_visible()

    page.select_option("#push-profile", label="Region only")

    expect(page.locator("[data-section='config.lora']")).to_be_visible()
    assert {s["section"]: s["rows"] for s in diff_sections(page)} == {
        "config.lora": [["region", "US", "EU_868"]],
    }


def test_diff_masks_the_psk_and_password_values(page: Page, live_server: str):
    config = {
        "channels_to_write": "1",
        "channel_0[psk]": OTHER_KEY.hex(),
        "network_enabled": "true",
        "wifi_enabled": "true",
        "wifi_psk": WIFI_PSK,
        "mqtt_enabled": "true",
        "mqtt_password": MQTT_PASSWORD,
    }
    # A profile with stored secrets needs an admin session, and only an admin session gets the secrets back.
    page.goto(f"{live_server}/admin")
    page.fill("#adminKey", ADMIN_PASSWORD)
    with page.expect_response(lambda response: urlsplit(response.url).path == "/admin/login"):
        page.click("button[type=submit]:has-text('Sign in')")
    page.goto(f"{live_server}/")
    csrf = {"X-CSRF-Token": page.get_attribute("#saveProfileBtn", "data-csrf-token")}
    create_profile(page, live_server, "Secrets stored", config, headers=csrf)

    connect(page, live_server, "Secrets stored")

    rows = {s["section"]: s["rows"] for s in diff_sections(page)}
    assert rows["channels.0"] == [["psk", MASK, MASK]]
    assert rows["config.network"] == [["wifi_psk", MASK, MASK]]
    assert rows["moduleConfig.mqtt"] == [["password", MASK, MASK]]
    html = page.content()
    secret_forms = [
        OTHER_KEY.hex(),
        base64.b64encode(OTHER_KEY).decode(),
        base64.b64encode(CHANNEL_0_KEY).decode(),
        WIFI_PSK,
        MQTT_PASSWORD,
        "device-wifi",
        "device-mqtt",
    ]
    assert [form for form in secret_forms if form in html] == []


def test_must_reenter_secret_shows_a_prompt_and_stays_in_the_page(page: Page, live_server: str):
    requests = record_requests(page)
    config = {"channels_to_write": "1", "channel_0[psk]": base64.b64encode(OTHER_KEY).decode()}
    profile = create_profile(page, live_server, "Secret not stored", config)
    assert profile["must_reenter"] == ["channel_0[psk]"]

    connect(page, live_server, "Secret not stored")

    expect(page.locator("#push-reenter")).to_be_visible()
    field = page.locator("#push-reenter input[data-form='channel_0[psk]']")
    expect(field).to_have_attribute("type", "password")
    expect(page.locator("#push-send")).to_be_disabled()
    # The device key is equal to no profile value yet, so the diff shows no PSK row.
    expect(page.locator("[data-section='channels.0']")).to_have_count(0)

    entered = base64.b64encode(OTHER_KEY).decode()
    field.fill(entered)

    expect(page.locator("[data-section='channels.0']")).to_be_visible()
    assert {s["section"]: s["rows"] for s in diff_sections(page)}["channels.0"] == [["psk", MASK, MASK]]
    stored = page.evaluate(
        "() => [...Object.values(localStorage), ...Object.values(sessionStorage), document.cookie].join(' ')"
    )
    assert entered not in stored
    assert [r.url for r in requests if entered in r.url or entered in (r.post_data or "")] == []


def test_invalid_reentered_key_shows_an_error_and_no_diff_row(page: Page, live_server: str):
    config = {"channels_to_write": "1", "channel_0[psk]": base64.b64encode(OTHER_KEY).decode()}
    create_profile(page, live_server, "Bad secret", config)
    connect(page, live_server, "Bad secret")

    page.locator("#push-reenter input[data-form='channel_0[psk]']").fill("not a key")

    expect(page.locator("#push-reenter .invalid-feedback")).to_have_text(
        "The value is not hex, base64 or a { 0x.. } byte list of 0, 1, 16 or 32 bytes."
    )
    expect(page.locator("[data-section='channels.0']")).to_have_count(0)


def test_no_page_request_to_the_server_holds_device_data(page: Page, live_server: str):
    requests = record_requests(page)
    create_profile(page, live_server, "Request check", PROFILE)

    connect(page, live_server, "Request check")
    expect(page.locator("#push-diff")).to_be_visible()

    origin = urlsplit(live_server).netloc
    to_server = [r for r in requests if urlsplit(r.url).netloc == origin]
    assert [r.method for r in to_server if r.method != "GET"] == []
    assert [r.url for r in to_server if r.post_data] == []
    device_values = ["LongFast", "MEDIUM_FAST", "10800", "Other node", "fieldnet", "device-wifi", "device-mqtt"]
    assert [(r.url, v) for r in to_server for v in device_values if v in r.url] == []
    api = sorted({urlsplit(r.url).path for r in to_server if urlsplit(r.url).path.startswith("/api/")})
    assert len(api) == 2 and api[0] == "/api/v1/profiles" and api[1].startswith("/api/v1/profiles/"), api


# Each sample is a profile value, a device value that is equal to it, and a device value that differs from it.
BOOL = ("true", True, False)
NOT_BOOL = ("true", False, True)
UINT = ("120", 120, 60)
TEXT = ("alpha", "alpha", "beta")
SECRET = ("s3cret-alpha", "s3cret-alpha", "s3cret-beta")
PSK = (base64.b64encode(CHANNEL_0_KEY).decode(), {"$bytes": list(CHANNEL_0_KEY)}, {"$bytes": [1]})
ADMIN_KEY = (bytes(range(32)).hex(), {"$bytes": list(range(32))}, {"$bytes": list(range(1, 33))})
TZ = ("America/New_York", "EST5EDT,M3.2.0,M11.1.0", "UTC0")
# Core sends `Math.floor(degrees / 1e-7)` in `setFixedPosition`, and the device sends that integer back.
DEGREES = ("37.7749", math.floor(37.7749 / 1e-7), 0)
# Any PIN in the profile means the FIXED_PIN pairing mode (1), and the other sample is RANDOM_PIN (0).
PAIRING_MODE = ("123456", 1, 0)


def _enum(prefix: str, name: str, number: int, other: int) -> tuple:
    return (f"meshtastic_{prefix}_{name}", number, other)


# Each form field of the mapping table: its section, its protobuf field path, and its sample.
# The enum numbers come from `meshtastic/config.proto` at protobufs v2.7.8, the version in core 2.6.7.
MAPPING = [
    ("device_role", "config.device", "role", _enum("Config_DeviceConfig_Role", "CLIENT_MUTE", 1, 0)),
    (
        "device_rebroadcast_mode",
        "config.device",
        "rebroadcastMode",
        _enum("Config_DeviceConfig_RebroadcastMode", "LOCAL_ONLY", 2, 0),
    ),
    ("node_info_broadcast_secs", "config.device", "nodeInfoBroadcastSecs", UINT),
    ("tz_string", "config.device", "tzdef", TZ),
    ("button_pin", "config.device", "buttonGpio", UINT),
    ("gps_mode", "config.position", "gpsMode", _enum("Config_PositionConfig_GpsMode", "NOT_PRESENT", 2, 1)),
    ("gps_update_interval", "config.position", "gpsUpdateInterval", UINT),
    ("position_broadcast_interval", "config.position", "positionBroadcastSecs", UINT),
    ("fixed_position", "config.position", "fixedPosition", BOOL),
    ("smart_position_enabled", "config.position", "positionBroadcastSmartEnabled", BOOL),
    ("fixed_lat", "position", "latitudeI", DEGREES),
    ("fixed_lon", "position", "longitudeI", DEGREES),
    ("lora_region", "config.lora", "region", _enum("Config_LoRaConfig_RegionCode", "EU_868", 3, 1)),
    ("lora_modem_preset", "config.lora", "modemPreset", _enum("Config_LoRaConfig_ModemPreset", "MEDIUM_FAST", 4, 0)),
    ("lora_channel_num", "config.lora", "channelNum", UINT),
    ("lora_ignore_mqtt", "config.lora", "ignoreMqtt", BOOL),
    ("lora_tx_power", "config.lora", "txPower", UINT),
    ("lora_tx_disabled", "config.lora", "txEnabled", NOT_BOOL),
    ("lora_config_ok_to_mqtt", "config.lora", "configOkToMqtt", BOOL),
    ("network_protocols", "config.network", "enabledProtocols", UINT),
    ("network_ipv6_enabled", "config.network", "ipv6Enabled", BOOL),
    ("wifi_enabled", "config.network", "wifiEnabled", BOOL),
    ("wifi_ssid", "config.network", "wifiSsid", TEXT),
    ("wifi_psk", "config.network", "wifiPsk", SECRET),
    ("mqtt_enabled", "moduleConfig.mqtt", "enabled", BOOL),
    ("mqtt_address", "moduleConfig.mqtt", "address", TEXT),
    ("mqtt_root_topic", "moduleConfig.mqtt", "root", TEXT),
    ("mqtt_username", "moduleConfig.mqtt", "username", TEXT),
    ("mqtt_password", "moduleConfig.mqtt", "password", SECRET),
    ("mqtt_encryption_enabled", "moduleConfig.mqtt", "encryptionEnabled", BOOL),
    ("mqtt_tls_enabled", "moduleConfig.mqtt", "tlsEnabled", BOOL),
    ("telemetry_device_interval", "moduleConfig.telemetry", "deviceUpdateInterval", UINT),
    ("telemetry_environment_enabled", "moduleConfig.telemetry", "environmentMeasurementEnabled", BOOL),
    ("telemetry_environment_interval", "moduleConfig.telemetry", "environmentUpdateInterval", UINT),
    ("telemetry_environment_screen_enabled", "moduleConfig.telemetry", "environmentScreenEnabled", BOOL),
    ("telemetry_air_quality_enabled", "moduleConfig.telemetry", "airQualityEnabled", BOOL),
    ("telemetry_air_quality_interval", "moduleConfig.telemetry", "airQualityInterval", UINT),
    ("bluetooth_fixed_pin", "config.bluetooth", "fixedPin", UINT),
    ("bluetooth_fixed_pin", "config.bluetooth", "mode", PAIRING_MODE),
    *((f"admin_key_{n}", "config.security", f"adminKey.{n}", ADMIN_KEY) for n in range(3)),
    *(
        row
        for n in range(8)
        for row in (
            (f"channel_{n}[name]", f"channels.{n}", "settings.name", TEXT),
            (f"channel_{n}[psk]", f"channels.{n}", "settings.psk", PSK),
            (f"channel_{n}[precision]", f"channels.{n}", "settings.moduleSettings.positionPrecision", UINT),
            (f"channel_{n}[uplink_enabled]", f"channels.{n}", "settings.uplinkEnabled", BOOL),
            (f"channel_{n}[downlink_enabled]", f"channels.{n}", "settings.downlinkEnabled", BOOL),
            (f"channel_{n}[muted]", f"channels.{n}", "settings.moduleSettings.isClientMuted", BOOL),
        )
    ),
    ("owner_long_name", "owner", "longName", TEXT),
    ("owner_short_name", "owner", "shortName", TEXT),
]

# The form switches that a mapped field needs, all on, and every channel slot counted.
SWITCHES_ON = {
    "lora_enabled": "true",
    "gps_enabled": "true",
    "network_enabled": "true",
    "wifi_enabled": "true",
    "mqtt_enabled": "true",
    "button_pin_enabled": "true",
    "fixed_position": "true",
    "channels_to_write": "8",
}

# The script finds the table row of `form`, finds its protobuf field in the core 2.6.7 schemas, and counts the
# diff rows of `form` for a device value equal to the profile value and for one that differs.
MAPPING_JS = """async ([form, path, raw, same, other, switches]) => {
  const page = await import('/static/js/push-config.js');
  const core = await import('@meshtastic/core');
  const row = page.FIELD_MAP.find(candidate => candidate.form === form && (path === null || candidate.field === path));
  if (!row) {
    return null;
  }
  const roots = {
    config: core.Protobuf.Config.ConfigSchema,
    moduleConfig: core.Protobuf.ModuleConfig.ModuleConfigSchema,
    channels: core.Protobuf.Channel.ChannelSchema,
    owner: core.Protobuf.Mesh.UserSchema,
    position: core.Protobuf.Mesh.PositionSchema,
  };
  const [root, ...rest] = row.section.split('.');
  let schema = roots[root];
  let found = true;
  [...rest, ...row.field.split('.')].filter(part => !/^[0-9]+$/.test(part)).forEach(part => {
    const field = schema && schema.fields.find(candidate => candidate.localName === part);
    found = found && Boolean(field);
    schema = field && field.message;
  });
  const revive = value => JSON.parse(JSON.stringify(value),
    (key, item) => (item && item.$bytes ? new Uint8Array(item.$bytes) : item));
  const deviceWith = value => {
    const read = {};
    const parts = [...row.section.split('.'), ...row.field.split('.')];
    let target = read;
    parts.slice(0, -1).forEach(part => { target = target[part] = target[part] || {}; });
    target[parts[parts.length - 1]] = revive(value);
    return read;
  };
  const config = { ...switches, [form]: raw };
  const context = { protobuf: core.Protobuf, tzTable: page.tzTable };
  const count = read => page.diffSections(read, config, context)
    .flatMap(section => section.rows).filter(diff => diff.form === form && diff.path === row.field).length;
  return { section: row.section, field: row.field, found, same: count(deviceWith(same)),
           other: count(deviceWith(other)) };
}"""


@pytest.fixture(scope="module")
def table_page(browser, live_server: str):
    page = browser.new_page()
    open_push_config(page, live_server)
    yield page
    page.close()


@pytest.mark.parametrize(("form", "section", "field", "sample"), MAPPING, ids=[row[0] for row in MAPPING])
def test_mapping_table_maps_the_form_field_to_a_core_protobuf_field(table_page, form, section, field, sample):
    raw, same, other = sample
    # The page moves the admin keys of the profile to the first slots, so each lower slot holds a key too.
    lower_keys = {f"admin_key_{n}": raw for n in range(int(form[-1]))} if form.startswith("admin_key_") else {}

    result = table_page.evaluate(MAPPING_JS, [form, field, raw, same, other, {**SWITCHES_ON, **lower_keys}])

    assert result == {"section": section, "field": field, "found": True, "same": 0, "other": 1}


def test_mapping_table_holds_only_the_tested_form_fields(table_page):
    rows = table_page.evaluate(
        "async () => (await import('/static/js/push-config.js')).FIELD_MAP.map(r => [r.form, r.field])"
    )

    assert sorted(rows) == sorted([row[0], row[2]] for row in MAPPING)


@pytest.mark.parametrize(
    ("form", "sample", "switches"),
    [
        ("lora_region", _enum("Config_LoRaConfig_RegionCode", "EU_868", 3, 1), {"lora_enabled": "false"}),
        ("gps_mode", _enum("Config_PositionConfig_GpsMode", "NOT_PRESENT", 2, 1), {"gps_enabled": "false"}),
        ("wifi_ssid", TEXT, {"wifi_enabled": "false"}),
        ("mqtt_address", TEXT, {"network_enabled": "false"}),
        ("button_pin", UINT, {"button_pin_enabled": "false"}),
        ("fixed_lat", DEGREES, {"fixed_position": "false"}),
        ("channel_1[name]", TEXT, {"channels_to_write": "1"}),
    ],
)
def test_field_behind_a_form_switch_that_is_off_gives_no_diff_row(table_page, form, sample, switches):
    raw, _same, other = sample

    result = table_page.evaluate(MAPPING_JS, [form, None, raw, other, other, {**SWITCHES_ON, **switches}])

    assert (result["same"], result["other"]) == (0, 0)


def test_enum_value_that_core_does_not_name_gives_no_diff_row(table_page):
    # Push cannot send a value name with no number, so the diff shows no row for it.
    raw = "meshtastic_Config_DeviceConfig_Role_NEWER_ROLE"

    result = table_page.evaluate(MAPPING_JS, ["device_role", None, raw, 0, 1, SWITCHES_ON])

    assert (result["same"], result["other"]) == (0, 0)


# The form fields that only switch other fields on or count the channel slots. They hold no device value.
FORM_SWITCHES = {"lora_enabled", "gps_enabled", "network_enabled", "button_pin_enabled", "channels_to_write"}
TABLES_JS = """async () => {
  const page = await import('/static/js/push-config.js');
  return { mapped: page.FIELD_MAP.map(row => row.form), notPushed: page.NOT_PUSHED.map(row => row.form) };
}"""


def test_every_profile_form_field_is_mapped_not_pushed_or_a_form_switch(table_page):
    tables = table_page.evaluate(TABLES_JS)
    channel_forms = {f"channel_{n}[{name}]" for n in range(8) for name in ChannelConfig.model_fields}
    plain = set(ConfigRequest.model_fields) - {f"channel_{n}" for n in range(8)} - {"preview_only"}

    assert set(tables["mapped"]).isdisjoint(tables["notPushed"])
    assert set(tables["mapped"]) | set(tables["notPushed"]) | FORM_SWITCHES == plain | channel_forms


# Each Not pushed value whose reason is that core has no field, with the schema and the field that core lacks.
NO_CORE_FIELD_JS = """async () => {
  const core = await import('@meshtastic/core');
  const fields = schema => schema.fields.map(field => field.localName);
  return {
    telemetry: fields(core.Protobuf.ModuleConfig.ModuleConfig_TelemetryConfigSchema),
    security: fields(core.Protobuf.Config.Config_SecurityConfigSchema),
    moduleConfig: fields(core.Protobuf.ModuleConfig.ModuleConfigSchema),
  };
}"""


def test_not_pushed_values_with_no_core_field_have_no_field_in_core_2_6_7(table_page):
    fields = table_page.evaluate(NO_CORE_FIELD_JS)
    reasons = table_page.evaluate(
        "async () => Object.fromEntries((await import('/static/js/push-config.js')).NOT_PUSHED"
        ".map(row => [row.form, row.reason]))"
    )

    no_field = sorted(form for form, reason in reasons.items() if reason == "Core 2.6.7 has no field for this value.")
    assert no_field == sorted(
        ["security_packet_signature_policy", "telemetry_air_quality_screen_enabled"]
        + [form for form in ConfigRequest.model_fields if form.startswith("mesh_beacon_")]
    )
    assert "airQualityScreenEnabled" not in fields["telemetry"]
    assert "airQualityEnabled" in fields["telemetry"]
    assert "packetSignaturePolicy" not in fields["security"]
    assert "meshBeacon" not in fields["moduleConfig"]


def test_device_with_the_profile_pin_and_the_random_pin_mode_shows_a_pairing_mode_row(page: Page, live_server: str):
    read = {**DEVICE, "config": {**DEVICE["config"], "bluetooth": {"enabled": True, "mode": 0, "fixedPin": 123456}}}
    create_profile(page, live_server, "Pin mode check", {"bluetooth_fixed_pin": "123456"})

    connect(page, live_server, "Pin mode check", read=read)

    assert {s["section"]: s["rows"] for s in diff_sections(page)} == {
        "config.bluetooth": [["mode", "RANDOM_PIN", "FIXED_PIN"]],
    }


def test_profile_warning_shows_on_the_page(page: Page, live_server: str):
    page.goto(f"{live_server}/admin")
    page.fill("#adminKey", ADMIN_PASSWORD)
    with page.expect_response(lambda response: urlsplit(response.url).path == "/admin/login"):
        page.click("button[type=submit]:has-text('Sign in')")
    page.goto(f"{live_server}/")
    csrf = {"X-CSRF-Token": page.get_attribute("#saveProfileBtn", "data-csrf-token")}
    config = {"network_enabled": "true", "wifi_enabled": "true", "wifi_psk": WIFI_PSK}
    create_profile(page, live_server, "Warning check", config, headers=csrf)
    # The profile stores secrets, so a page with no admin session gets the warning and no secret.
    page.context.clear_cookies()

    connect(page, live_server, "Warning check")

    expect(page.locator("#push-profile-warning")).to_have_text("Sign in as admin to load the stored secrets.")
