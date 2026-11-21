"""Meshtastic enum values that the config form offers, from `meshtastic/config.proto` at protobufs `v2.8.3`.

`tests/fixtures/protobufs/meshtastic/config.proto` is a copy of that file, and a test checks this module against it.
Each label is the `enum_value_metadata` label of the value in the proto file.
"""

from dataclasses import dataclass

PROTOBUFS_VERSION = "v2.8.3"


@dataclass(frozen=True)
class MeshtasticEnum:
    """The values of one protobufs enum, with the C prefix that nanopb puts before each value name."""

    name: str
    c_prefix: str
    labels: dict[str, str]

    def options(self) -> list[tuple[str, str]]:
        """Return a `(prefixed value, label)` pair for each value, for a form `select`."""
        return [(self.c_prefix + value, label) for value, label in self.labels.items()]

    def c_value(self, value: str) -> str:
        """Return a value with its C prefix, for example `meshtastic_Config_DeviceConfig_Role_TRACKER`.

        Args:
            value: The bare value name (`TRACKER`) or the value with its C prefix.

        Raises:
            ValueError: The enum does not offer the value.
        """
        bare = value.removeprefix(self.c_prefix)
        if bare not in self.labels:
            raise ValueError(f"{value!r} is not a {self.name} value that the form offers")
        return self.c_prefix + bare


# `UNSET` is absent: an empty region field writes no key, and the firmware then keeps `UNSET`.
REGIONS = MeshtasticEnum(
    "RegionCode",
    "meshtastic_Config_LoRaConfig_RegionCode_",
    {
        "US": "United States",
        "EU_433": "European Union 433MHz",
        "EU_868": "European Union 868MHz",
        "CN": "China",
        "JP": "Japan",
        "ANZ": "Australia / Brazil / New Zealand",
        "KR": "Korea",
        "TW": "Taiwan",
        "RU": "Russia",
        "IN": "India",
        "NZ_865": "New Zealand 865MHz",
        "TH": "Thailand",
        "LORA_24": "2.4 GHz",
        "UA_433": "Ukraine 433MHz",
        "UA_868": "Ukraine 868MHz",
        "MY_433": "Malaysia 433MHz",
        "MY_919": "Malaysia 919MHz",
        "SG_923": "Singapore 923MHz",
        "PH_433": "Philippines 433MHz",
        "PH_868": "Philippines 868MHz",
        "PH_915": "Philippines 915MHz",
        "ANZ_433": "Australia / New Zealand 433MHz",
        "KZ_433": "Kazakhstan 433MHz",
        "KZ_863": "Kazakhstan 863MHz",
        "NP_865": "Nepal 865MHz",
        "BR_902": "Brazil 902MHz",
        "ITU1_2M": "ITU Region 1 / Amateur 2m",
        "ITU2_2M": "ITU Region 2 / Amateur 2m",
        "EU_866": "European Union 866MHz",
        "EU_874": "European Union 874MHz",
        "EU_917": "European Union 917MHz",
        "EU_N_868": "European Union 868MHz (Narrow)",
        "ITU3_2M": "ITU Region 3 / Amateur 2m",
        "ITU1_70CM": "ITU Region 1 / Amateur 70cm",
        "ITU2_70CM": "ITU Region 2 / Amateur 70cm",
        "ITU3_70CM": "ITU Region 3 / Amateur 70cm",
        "ITU2_125CM": "ITU Region 2 / Amateur 1.25m",
    },
)

MODEM_PRESETS = MeshtasticEnum(
    "ModemPreset",
    "meshtastic_Config_LoRaConfig_ModemPreset_",
    {
        "LONG_FAST": "Long Range - Fast",
        "LONG_SLOW": "Long Range - Slow",
        "VERY_LONG_SLOW": "Very Long Range - Slow",
        "MEDIUM_SLOW": "Medium Range - Slow",
        "MEDIUM_FAST": "Medium Range - Fast",
        "SHORT_SLOW": "Short Range - Slow",
        "SHORT_FAST": "Short Range - Fast",
        "LONG_MODERATE": "Long Range - Moderate",
        "SHORT_TURBO": "Short Range - Turbo",
        "LONG_TURBO": "Long Range - Turbo",
        "LITE_FAST": "Lite - Fast",
        "LITE_SLOW": "Lite - Slow",
        "NARROW_FAST": "Narrow - Fast",
        "NARROW_SLOW": "Narrow - Slow",
        "TINY_FAST": "Tiny - Fast",
        "TINY_SLOW": "Tiny - Slow",
        "MEDIUM_TURBO": "Medium Range - Turbo",
    },
)

# Upstream `src/mesh/NodeDB.cpp` replaces these roles with `CLIENT` when they come from a userPrefs key.
ROUTER_ROLES = ("ROUTER", "ROUTER_LATE", "LOST_AND_FOUND")

# The proto file marks these roles `deprecated = true`.
DEPRECATED_ROLES = ("ROUTER_CLIENT", "REPEATER")

ROUTER_ROLE_MESSAGE = "Router roles cannot be set as a factory default. Use Push config after flashing."

ROLES = MeshtasticEnum(
    "Role",
    "meshtastic_Config_DeviceConfig_Role_",
    {
        "CLIENT": "Client",
        "CLIENT_MUTE": "Client Mute",
        "TRACKER": "Tracker",
        "SENSOR": "Sensor",
        "TAK": "TAK",
        "CLIENT_HIDDEN": "Client Hidden",
        "TAK_TRACKER": "TAK Tracker",
        "CLIENT_BASE": "Client Base",
    },
)

REBROADCAST_MODES = MeshtasticEnum(
    "RebroadcastMode",
    "meshtastic_Config_DeviceConfig_RebroadcastMode_",
    {
        "ALL": "All",
        "ALL_SKIP_DECODING": "All Skip Decoding",
        "LOCAL_ONLY": "Local Only",
        "KNOWN_ONLY": "Known Only",
        "NONE": "None",
        "CORE_PORTNUMS_ONLY": "Core Portnums Only",
    },
)

GPS_MODES = MeshtasticEnum(
    "GpsMode",
    "meshtastic_Config_PositionConfig_GpsMode_",
    {
        "DISABLED": "Disabled",
        "ENABLED": "Enabled",
        "NOT_PRESENT": "Not Present",
    },
)

PACKET_SIGNATURE_POLICIES = MeshtasticEnum(
    "PacketSignaturePolicy",
    "meshtastic_Config_SecurityConfig_PacketSignaturePolicy_",
    {
        "PACKET_SIGNATURE_POLICY_COMPATIBLE": "Compatible - No Signing",
        "PACKET_SIGNATURE_POLICY_BALANCED": "Balanced - Sign, Accept Unsigned",
        "PACKET_SIGNATURE_POLICY_STRICT": "Strict - Require Signatures",
    },
)

# The C name of each value that the form offers, so a check can find an enum value in the generator output.
ENUM_C_VALUES = frozenset(
    value
    for enum in (REGIONS, MODEM_PRESETS, ROLES, REBROADCAST_MODES, GPS_MODES, PACKET_SIGNATURE_POLICIES)
    for value, _label in enum.options()
)
