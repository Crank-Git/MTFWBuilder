"""Tests for the enum controls of the config form and their validation (FR-config-coverage-2 to -4a)."""

from html.parser import HTMLParser

import pytest
from httpx import ASGITransport, AsyncClient

ROUTER_ROLE_MESSAGE = "Router roles cannot be set as a factory default. Use Push config after flashing."


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class _SelectParser(HTMLParser):
    """Collect the option values of each `select` element, by the `name` of the select."""

    def __init__(self):
        super().__init__()
        self.options: dict[str, list[str]] = {}
        self._select: str | None = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select":
            self._select = attrs.get("name")
            self.options[self._select] = []
        elif tag == "option" and self._select:
            self.options[self._select].append(attrs.get("value", ""))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


async def form_options(client) -> tuple[dict[str, list[str]], str]:
    """Return the option values of each select on the build page, and the page HTML."""
    resp = await client.get("/")
    assert resp.status_code == 200
    parser = _SelectParser()
    parser.feed(resp.text)
    return parser.options, resp.text


def error_messages(resp, field: str) -> list[str]:
    """Return the messages of the field errors at `field` in a 422 response."""
    return [err["msg"] for err in resp.json()["detail"] if err["loc"][1:] == [field]]


@pytest.mark.asyncio
async def test_region_control_has_37_options(client):
    options, _ = await form_options(client)
    assert len(options["lora_region"]) == 37
    assert all(v.startswith("meshtastic_Config_LoRaConfig_RegionCode_") for v in options["lora_region"])


@pytest.mark.asyncio
async def test_modem_preset_control_has_17_options(client):
    options, _ = await form_options(client)
    assert len(options["lora_modem_preset"]) == 17
    assert all(v.startswith("meshtastic_Config_LoRaConfig_ModemPreset_") for v in options["lora_modem_preset"])


@pytest.mark.asyncio
async def test_gps_mode_control_has_the_3_gps_modes(client):
    options, _ = await form_options(client)
    assert options["gps_mode"] == [
        "meshtastic_Config_PositionConfig_GpsMode_DISABLED",
        "meshtastic_Config_PositionConfig_GpsMode_ENABLED",
        "meshtastic_Config_PositionConfig_GpsMode_NOT_PRESENT",
    ]


@pytest.mark.asyncio
async def test_role_control_lists_client_base_and_not_repeater_or_router(client):
    options, _ = await form_options(client)
    roles = options["device_role"]
    assert "meshtastic_Config_DeviceConfig_Role_CLIENT_BASE" in roles
    assert "meshtastic_Config_DeviceConfig_Role_REPEATER" not in roles
    assert "meshtastic_Config_DeviceConfig_Role_ROUTER" not in roles


@pytest.mark.asyncio
async def test_role_control_has_an_empty_option_that_writes_no_key(client):
    options, _ = await form_options(client)
    assert options["device_role"][0] == ""


@pytest.mark.asyncio
async def test_form_shows_the_router_role_message(client):
    _, html = await form_options(client)
    assert ROUTER_ROLE_MESSAGE in html


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        "ROUTER",
        "meshtastic_Config_DeviceConfig_Role_ROUTER",
        "meshtastic_Config_DeviceConfig_Role_ROUTER_LATE",
        "meshtastic_Config_DeviceConfig_Role_LOST_AND_FOUND",
    ],
)
async def test_config_with_a_router_role_returns_422_with_the_router_role_message(client, role):
    resp = await client.post("/api/v1/preview", json={"device_role": role})
    assert resp.status_code == 422
    assert any(ROUTER_ROLE_MESSAGE in msg for msg in error_messages(resp, "device_role"))


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["REPEATER", "meshtastic_Config_DeviceConfig_Role_ROUTER_CLIENT", "BOSS"])
async def test_config_with_a_role_the_form_does_not_offer_returns_422(client, role):
    resp = await client.post("/api/v1/preview", json={"device_role": role})
    assert resp.status_code == 422
    assert error_messages(resp, "device_role")


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["", "CLIENT_BASE", "meshtastic_Config_DeviceConfig_Role_TRACKER"])
async def test_config_with_an_offered_role_or_no_role_returns_200(client, role):
    resp = await client.post("/api/v1/preview", json={"device_role": role})
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lora_region", "UNSET"),
        ("lora_region", "meshtastic_Config_LoRaConfig_RegionCode_XX"),
        ("lora_modem_preset", "meshtastic_Config_LoRaConfig_ModemPreset_ULTRA_FAST"),
        ("gps_mode", "meshtastic_Config_PositionConfig_GpsMode_ON"),
    ],
)
async def test_config_with_an_enum_value_that_protobufs_lack_returns_422(client, field, value):
    resp = await client.post("/api/v1/preview", json={field: value})
    assert resp.status_code == 422
    assert error_messages(resp, field)


@pytest.mark.asyncio
async def test_bare_modem_preset_is_written_with_its_c_prefix(client):
    resp = await client.post(
        "/api/v1/preview", json={"lora_enabled": "true", "lora_modem_preset": "SHORT_TURBO", "lora_region": "EU_866"}
    )
    assert resp.status_code == 200
    content = resp.json()["content"]
    assert '"USERPREFS_LORACONFIG_MODEM_PRESET": "meshtastic_Config_LoRaConfig_ModemPreset_SHORT_TURBO"' in content
    assert '"USERPREFS_CONFIG_LORA_REGION": "meshtastic_Config_LoRaConfig_RegionCode_EU_866"' in content
