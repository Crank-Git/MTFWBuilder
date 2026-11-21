"""Tests for the Meshtastic enums module against the fixture protobufs (FR-config-coverage-1 to -4)."""

import re
from pathlib import Path

import pytest

from mtfwbuilder import meshtastic_enums as enums

CONFIG_PROTO = Path(__file__).resolve().parent / "fixtures" / "protobufs" / "meshtastic" / "config.proto"

# Each module enum, with the message and the enum name that hold it in `config.proto`.
MODULE_ENUMS = [
    (enums.REGIONS, "LoRaConfig", "RegionCode"),
    (enums.MODEM_PRESETS, "LoRaConfig", "ModemPreset"),
    (enums.ROLES, "DeviceConfig", "Role"),
    (enums.REBROADCAST_MODES, "DeviceConfig", "RebroadcastMode"),
    (enums.GPS_MODES, "PositionConfig", "GpsMode"),
    (enums.PACKET_SIGNATURE_POLICIES, "SecurityConfig", "PacketSignaturePolicy"),
]

_VALUE = re.compile(r"^\s*([A-Z][A-Z0-9_]*) = \d+(\s*\[(?:[^\[\]]|\[[^\]]*\])*\])?", re.M)


def proto_enum(message: str, name: str) -> dict[str, tuple[str, bool]]:
    """Return each value of `Config.<message>.<name>` in the fixture, with its label and its deprecated flag."""
    text = re.sub(r"/\*.*?\*/", "", CONFIG_PROTO.read_text(), flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    block = re.search(rf"\n  message {message} \{{(.*?)\n  \}}", text, re.S)[1]
    body = re.search(rf"enum {name} \{{(.*?)\n    \}}", block, re.S)[1]
    return {
        value: (re.search(r'label: "([^"]*)"', options)[1], "deprecated = true" in options)
        for value, options in _VALUE.findall(body)
    }


def test_fixture_is_config_proto_of_protobufs_v2_8_3():
    assert enums.PROTOBUFS_VERSION == "v2.8.3"
    assert CONFIG_PROTO.read_text().startswith('syntax = "proto3";\n\npackage meshtastic;')


@pytest.mark.parametrize(("enum", "message", "name"), MODULE_ENUMS)
def test_enum_has_the_c_prefix_of_its_proto_enum(enum, message, name):
    assert enum.c_prefix == f"meshtastic_Config_{message}_{name}_"


@pytest.mark.parametrize(("enum", "message", "name"), MODULE_ENUMS)
def test_enum_labels_match_the_proto_labels(enum, message, name):
    proto = proto_enum(message, name)
    assert {value: proto[value][0] for value in enum.labels} == enum.labels


def test_regions_are_all_proto_regions_except_unset():
    assert list(enums.REGIONS.labels) == [v for v in proto_enum("LoRaConfig", "RegionCode") if v != "UNSET"]
    assert len(enums.REGIONS.labels) == 37


def test_modem_presets_are_all_proto_modem_presets():
    assert list(enums.MODEM_PRESETS.labels) == list(proto_enum("LoRaConfig", "ModemPreset"))
    assert len(enums.MODEM_PRESETS.labels) == 17


@pytest.mark.parametrize(
    ("enum", "message", "name"),
    [
        (enums.REBROADCAST_MODES, "DeviceConfig", "RebroadcastMode"),
        (enums.GPS_MODES, "PositionConfig", "GpsMode"),
        (enums.PACKET_SIGNATURE_POLICIES, "SecurityConfig", "PacketSignaturePolicy"),
    ],
)
def test_enum_has_every_proto_value(enum, message, name):
    assert list(enum.labels) == list(proto_enum(message, name))


def test_roles_with_router_and_deprecated_roles_are_all_proto_roles():
    proto = proto_enum("DeviceConfig", "Role")
    assert set(enums.ROLES.labels) | set(enums.ROUTER_ROLES) | set(enums.DEPRECATED_ROLES) == set(proto)
    assert set(enums.ROLES.labels).isdisjoint(set(enums.ROUTER_ROLES) | set(enums.DEPRECATED_ROLES))


def test_deprecated_roles_are_the_proto_roles_marked_deprecated():
    proto = proto_enum("DeviceConfig", "Role")
    assert set(enums.DEPRECATED_ROLES) == {value for value, (_, deprecated) in proto.items() if deprecated}


def test_router_roles_are_router_router_late_and_lost_and_found():
    assert set(enums.ROUTER_ROLES) == {"ROUTER", "ROUTER_LATE", "LOST_AND_FOUND"}


def test_roles_list_client_base_and_not_repeater_or_router():
    assert "CLIENT_BASE" in enums.ROLES.labels
    assert "REPEATER" not in enums.ROLES.labels
    assert "ROUTER" not in enums.ROLES.labels


@pytest.mark.parametrize("value", ["TRACKER", "meshtastic_Config_DeviceConfig_Role_TRACKER"])
def test_c_value_adds_the_c_prefix_to_a_bare_value_and_keeps_a_prefixed_value(value):
    assert enums.ROLES.c_value(value) == "meshtastic_Config_DeviceConfig_Role_TRACKER"


@pytest.mark.parametrize("value", ["ROUTER", "meshtastic_Config_LoRaConfig_RegionCode_US", "tracker", ""])
def test_c_value_rejects_a_value_that_the_enum_does_not_offer(value):
    with pytest.raises(ValueError, match="is not a Role value"):
        enums.ROLES.c_value(value)
