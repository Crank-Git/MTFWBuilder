"""Decode and encode a Meshtastic channel URL, and map a `meshtastic --export-config` file to the config form.

The URL format is `Node.getURL` and `Node.setURL` in `meshtastic/node.py` of the `meshtastic` package 2.7.11:
the fragment is the URL-safe base64 form, without padding, of a `ChannelSet` protobuf.
The export of a config encodes the channels and the LoRa settings that the firmware gets from the config.

The export format is `export_config` in `meshtastic/__main__.py` of the same package. Its `config` and
`module_config` parts are `MessageToDict` output: camelCase field names and enum names. An enum value that the
package does not know is a number.
"""

import base64
import binascii
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import segno
import yaml
from google.protobuf.message import DecodeError
from meshtastic.protobuf import apponly_pb2, channel_pb2, config_pb2
from meshtastic.util import camel_to_snake
from pydantic import ValidationError

from mtfwbuilder.key_bytes import decode_key
from mtfwbuilder.meshtastic_enums import MODEM_PRESETS, REGIONS
from mtfwbuilder.models import (
    CHANNEL_SLOTS,
    ChannelConfig,
    ChannelUrlImport,
    CliYamlImport,
    ConfigRequest,
    ImportedChannel,
    ImportedLora,
    flat_channel_keys,
)

INVALID_URL_MESSAGE = "This is not a valid Meshtastic channel URL."

# The enum module lists the values in proto number order, and `meshtastic` 2.7.11 does not know the newest values.
_PRESET_NAMES = list(MODEM_PRESETS.labels)
_REGION_NAMES = ["UNSET", *REGIONS.labels]

# Upstream `setURL` splits on `/#` or on `/?add=true#`, so these two queries are the only URL forms.
_QUERIES = {"": False, "add=true": True}


class InvalidChannelUrl(ValueError):
    """The text is not a Meshtastic channel URL, or its fragment is not a `ChannelSet` with a channel."""


def _enum_name(names: list[str], number: int) -> str | None:
    """Return the proto name of an enum number, or None when protobufs v2.8.3 does not define the number."""
    return names[number] if 0 <= number < len(names) else None


def _channel_set(fragment: str) -> apponly_pb2.ChannelSet:
    """Return the `ChannelSet` that a URL fragment holds.

    Raises:
        InvalidChannelUrl: The fragment is not base64url, not a `ChannelSet`, or holds no channel.
    """
    padded = fragment + "=" * (-len(fragment) % 4)
    try:
        channel_set = apponly_pb2.ChannelSet.FromString(base64.b64decode(padded, altchars=b"-_", validate=True))
    except (binascii.Error, DecodeError):
        # The error text can quote the fragment, and the fragment holds the PSKs.
        raise InvalidChannelUrl from None
    if not channel_set.settings:
        raise InvalidChannelUrl
    return channel_set


def _channel(index: int, settings: channel_pb2.ChannelSettings) -> ImportedChannel:
    """Return the channel at `index` of a URL, with the role that upstream `setURL` gives it."""
    psk = bytes(settings.psk)
    return ImportedChannel(
        index=index,
        name=settings.name,
        psk=base64.b64encode(psk).decode("ascii"),
        # Upstream reads a PSK of 1 byte as the number of a default key, not as key material.
        default_key=psk[0] if len(psk) == 1 else None,
        role="PRIMARY" if index == 0 else "SECONDARY",
        uplink_enabled=settings.uplink_enabled,
        downlink_enabled=settings.downlink_enabled,
        position_precision=settings.module_settings.position_precision,
    )


def decode_channel_url(url: str) -> ChannelUrlImport:
    """Return the channels and the LoRa settings of a channel URL.

    Args:
        url: A `https://meshtastic.org/e/#…` or `https://meshtastic.org/e/?add=true#…` URL.

    Raises:
        InvalidChannelUrl: The URL is not in one of the two forms, or its fragment does not decode.
    """
    parts = urlsplit(url.strip())
    is_channel_url = (parts.scheme, parts.netloc, parts.path) == ("https", "meshtastic.org", "/e/")
    if not is_channel_url or parts.query not in _QUERIES or not parts.fragment:
        raise InvalidChannelUrl
    channel_set = _channel_set(parts.fragment)
    warnings = []
    if len(channel_set.settings) > CHANNEL_SLOTS:
        count = len(channel_set.settings)
        warnings.append(f"The URL has {count} channels. The import takes the first {CHANNEL_SLOTS}.")
    lora = channel_set.lora_config
    return ChannelUrlImport(
        channels=[_channel(index, settings) for index, settings in enumerate(channel_set.settings[:CHANNEL_SLOTS])],
        lora=ImportedLora(
            region=_enum_name(_REGION_NAMES, lora.region),
            modem_preset=_enum_name(_PRESET_NAMES, lora.modem_preset),
            use_preset=lora.use_preset,
            hop_limit=lora.hop_limit,
            tx_enabled=lora.tx_enabled,
            tx_power=lora.tx_power,
            channel_num=lora.channel_num,
            ignore_mqtt=lora.ignore_mqtt,
            config_ok_to_mqtt=lora.config_ok_to_mqtt,
        ),
        add=_QUERIES[parts.query],
        warnings=warnings,
    )


# Upstream `Channels::initDefaultChannel` gives each written channel slot this PSK and this position precision.
_DEFAULT_PSK = b"\x01"
_DEFAULT_POSITION_PRECISION = 13
# Upstream `NodeDB.cpp` sets `HOP_RELIABLE`, and the config form has no hop limit field.
_DEFAULT_HOP_LIMIT = 3


def _settings(channel: ChannelConfig | None) -> channel_pb2.ChannelSettings:
    """Return the settings that the firmware gives a written channel slot with the fields of `channel`."""
    channel = channel or ChannelConfig()
    is_muted = channel.muted == "true"
    precision = int(channel.precision) if channel.precision else _DEFAULT_POSITION_PRECISION
    # An empty `module_settings` message adds two bytes, so the URL of an imported channel changes.
    module_settings = channel_pb2.ModuleSettings(position_precision=precision, is_muted=is_muted)
    return channel_pb2.ChannelSettings(
        name=channel.name or "",
        psk=decode_key(channel.psk or "") or _DEFAULT_PSK,
        uplink_enabled=channel.uplink_enabled == "true",
        downlink_enabled=channel.downlink_enabled == "true",
        module_settings=module_settings if precision or is_muted else None,
    )


def _lora(config: ConfigRequest) -> config_pb2.Config.LoRaConfig:
    """Return the LoRa settings that the firmware gets from the config."""
    lora = config_pb2.Config.LoRaConfig(use_preset=True, hop_limit=_DEFAULT_HOP_LIMIT, tx_enabled=True)
    # The generator writes no LoRa key when the LoRa switch is off, so the firmware keeps its defaults.
    if config.lora_enabled != "true":
        return lora
    if config.lora_region:
        lora.region = _REGION_NAMES.index(config.lora_region.removeprefix(REGIONS.c_prefix))
    if config.lora_modem_preset:
        # The package enum stops at `NARROW_SLOW`, but the field takes any number.
        lora.modem_preset = _PRESET_NAMES.index(config.lora_modem_preset.removeprefix(MODEM_PRESETS.c_prefix))
    lora.channel_num = int(config.lora_channel_num or 0)
    lora.tx_power = int(config.lora_tx_power or 0)
    lora.tx_enabled = config.lora_tx_disabled != "true"
    lora.ignore_mqtt = config.lora_ignore_mqtt == "true"
    lora.config_ok_to_mqtt = config.lora_config_ok_to_mqtt == "true"
    return lora


def encode_channel_url(config: ConfigRequest) -> str:
    """Return the channel URL of the channels and the LoRa settings that a build of the config gives.

    The URL holds the channel slots that `channels_to_write` counts, in the form of upstream `Node.getURL`.
    A field that the config leaves empty takes the firmware default.
    """
    channel_set = apponly_pb2.ChannelSet(
        settings=[_settings(getattr(config, f"channel_{slot}")) for slot in range(int(config.channels_to_write))],
        lora_config=_lora(config),
    )
    fragment = base64.urlsafe_b64encode(channel_set.SerializeToString()).decode("ascii").rstrip("=")
    return f"https://meshtastic.org/e/#{fragment}"


def qr_svg(text: str) -> str:
    """Return an SVG document of a QR code that holds `text`."""
    buffer = io.BytesIO()
    # A phone camera reads a regular QR code, and segno picks a Micro QR code for a short text by default.
    segno.make(text, error="m", micro=False).save(buffer, kind="svg", xmldecl=False, scale=4)
    return buffer.getvalue().decode("utf-8")


MAX_EXPORT_BYTES = 64 * 1024
INVALID_FILE_MESSAGE = "This is not a valid Meshtastic export file."

# Upstream `export_config` writes each admin key as `base64:` and the key in base64.
_BASE64_TAG = "base64:"
# The protobufs give `admin_key` a `max_count` of 3.
_ADMIN_KEY_SLOTS = 3


class InvalidExportFile(ValueError):
    """The file is not UTF-8 YAML that holds a mapping."""


# A converter returns the form fields of one file value, or None when the form has no field for the value.
# The second argument is the section that holds the value, for a field that depends on another field.
Converter = Callable[[Any, dict], dict[str, str] | None]


def _form_value(value: Any) -> str | None:
    """Return the form text of a scalar value, or None for a list or a mapping."""
    # A YAML alias can repeat a list many times, so only a scalar becomes text.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int | float):
        return str(value)
    return None


def _plain(name: str) -> Converter:
    """Return a converter that writes a scalar value unchanged into the form field `name`."""

    def convert(value: Any, _section: dict) -> dict[str, str] | None:
        text = _form_value(value)
        return None if text is None else {name: text}

    return convert


def _inverted(name: str) -> Converter:
    """Return a converter that writes the opposite of a true or false value into the form field `name`."""

    def convert(value: Any, _section: dict) -> dict[str, str] | None:
        return {name: "false" if value else "true"} if isinstance(value, bool) else None

    return convert


def _numbered_enum(name: str, names: list[str]) -> Converter:
    """Return a converter for an enum value that `MessageToDict` writes as a number when the package lacks it."""

    def convert(value: Any, section: dict) -> dict[str, str] | None:
        if isinstance(value, int) and not isinstance(value, bool):
            value = _enum_name(names, value)
        return _plain(name)(value, section)

    return convert


def _admin_keys(value: Any, _section: dict) -> dict[str, str] | None:
    """Return an admin key field for each key of the `adminKey` list, without the `base64:` tag."""
    if not (isinstance(value, list) and len(value) <= _ADMIN_KEY_SLOTS and all(isinstance(k, str) for k in value)):
        return None
    return {f"admin_key_{n}": key.removeprefix(_BASE64_TAG) for n, key in enumerate(value)}


def _button(value: Any, section: dict) -> dict[str, str] | None:
    """Return the button pin field with its switch on."""
    pin = _plain("button_pin")(value, section)
    return None if pin is None else pin | {"button_pin_enabled": "true"}


def _bluetooth_mode(_value: Any, section: dict) -> dict[str, str] | None:
    # The firmware turns on the fixed PIN mode when it reads the PIN key, so the mode needs no field.
    return {} if section.get("mode") == "FIXED_PIN" else None


def _bluetooth_pin(value: Any, section: dict) -> dict[str, str] | None:
    # In another mode the device does not use the PIN, and the PIN key would turn on the fixed PIN mode.
    return _plain("bluetooth_fixed_pin")(value, section) if section.get("mode") == "FIXED_PIN" else None


def _mqtt_enabled(value: Any, section: dict) -> dict[str, str] | None:
    """Return the MQTT switch field, and turn on the network switch when MQTT is on."""
    # The generator writes the MQTT keys only when the network switch is on.
    enabled = _plain("mqtt_enabled")(value, section)
    if enabled is None:
        return None
    return enabled | ({"network_enabled": "true"} if value is True else {})


def _canned_messages(value: Any, _section: dict) -> dict[str, str] | None:
    # Upstream separates the messages with `|`, and the form takes one message on each line.
    return {"canned_messages": value.replace("|", "\n")} if isinstance(value, str) else None


def _location(value: Any, _section: dict) -> dict[str, str] | None:
    """Return the fixed position fields of the `location` mapping: `lat`, `lon` and an optional `alt`."""
    if not isinstance(value, dict) or not {"lat", "lon"} <= set(value) <= {"lat", "lon", "alt"}:
        return None
    coordinates = {f"fixed_{axis}": _form_value(value[axis]) for axis in value}
    if None in coordinates.values():
        return None
    return coordinates | {"fixed_position": "true", "gps_enabled": "true"}


# The converter of each top-level key that has a userPrefs key. Upstream `_profile_from_yaml` also reads `ownerShort`.
_TOP_LEVEL: dict[str, Converter] = {
    "owner": _plain("owner_long_name"),
    "owner_short": _plain("owner_short_name"),
    "ownerShort": _plain("owner_short_name"),
    "canned_messages": _canned_messages,
    "ringtone": _plain("ringtone_rtttl"),
    "location": _location,
}
_CHANNEL_URL_KEYS = ("channel_url", "channelUrl")
_PARTS = ("config", "module_config")

# The converter of each field that has a userPrefs key, by part and section. The names are in snake_case.
_SECTIONS: dict[tuple[str, str], dict[str, Converter]] = {
    ("config", "device"): {
        "role": _plain("device_role"),
        "rebroadcast_mode": _plain("device_rebroadcast_mode"),
        "node_info_broadcast_secs": _plain("node_info_broadcast_secs"),
        "tzdef": _plain("tz_string"),
        "button_gpio": _button,
    },
    ("config", "position"): {
        "gps_mode": _plain("gps_mode"),
        "gps_update_interval": _plain("gps_update_interval"),
        "position_broadcast_secs": _plain("position_broadcast_interval"),
        "position_broadcast_smart_enabled": _plain("smart_position_enabled"),
        "fixed_position": _plain("fixed_position"),
    },
    ("config", "lora"): {
        "region": _numbered_enum("lora_region", _REGION_NAMES),
        "modem_preset": _numbered_enum("lora_modem_preset", _PRESET_NAMES),
        "channel_num": _plain("lora_channel_num"),
        "ignore_mqtt": _plain("lora_ignore_mqtt"),
        "tx_power": _plain("lora_tx_power"),
        "tx_enabled": _inverted("lora_tx_disabled"),
        "config_ok_to_mqtt": _plain("lora_config_ok_to_mqtt"),
    },
    ("config", "network"): {
        "wifi_enabled": _plain("wifi_enabled"),
        "wifi_ssid": _plain("wifi_ssid"),
        "wifi_psk": _plain("wifi_psk"),
        "enabled_protocols": _plain("network_protocols"),
        "ipv6_enabled": _plain("network_ipv6_enabled"),
    },
    ("config", "security"): {
        "admin_key": _admin_keys,
        "is_managed": _plain("security_is_managed"),
        "packet_signature_policy": _plain("security_packet_signature_policy"),
    },
    ("config", "bluetooth"): {"mode": _bluetooth_mode, "fixed_pin": _bluetooth_pin},
    ("module_config", "mqtt"): {
        "enabled": _mqtt_enabled,
        "address": _plain("mqtt_address"),
        "username": _plain("mqtt_username"),
        "password": _plain("mqtt_password"),
        "root": _plain("mqtt_root_topic"),
        "encryption_enabled": _plain("mqtt_encryption_enabled"),
        "tls_enabled": _plain("mqtt_tls_enabled"),
    },
    ("module_config", "telemetry"): {
        "device_update_interval": _plain("telemetry_device_interval"),
        "environment_measurement_enabled": _plain("telemetry_environment_enabled"),
        "environment_update_interval": _plain("telemetry_environment_interval"),
        "environment_screen_enabled": _plain("telemetry_environment_screen_enabled"),
        "air_quality_enabled": _plain("telemetry_air_quality_enabled"),
        "air_quality_interval": _plain("telemetry_air_quality_interval"),
        "air_quality_screen_enabled": _plain("telemetry_air_quality_screen_enabled"),
    },
    ("module_config", "external_notification"): {"nag_timeout": _plain("ringtone_nag_secs")},
}

# The generator writes the fields of these sections only when the section switch of the form is on.
_SECTION_SWITCHES = {
    ("config", "position"): "gps_enabled",
    ("config", "lora"): "lora_enabled",
    ("config", "network"): "network_enabled",
}


def _channel_fields(url: ChannelUrlImport) -> dict[str, str]:
    """Return the channel fields, the channel count and the LoRa enum fields of a decoded channel URL."""
    fields = {"channels_to_write": str(len(url.channels))}
    for channel in url.channels:
        prefix = f"channel_{channel.index}"
        fields[f"{prefix}[name]"] = channel.name
        fields[f"{prefix}[psk]"] = channel.psk
        fields[f"{prefix}[precision]"] = str(channel.position_precision)
        fields[f"{prefix}[uplink_enabled]"] = "true" if channel.uplink_enabled else "false"
        fields[f"{prefix}[downlink_enabled]"] = "true" if channel.downlink_enabled else "false"
    # The form has no `UNSET` region, and an empty region field keeps the firmware default.
    lora = {"lora_region": url.lora.region, "lora_modem_preset": url.lora.modem_preset}
    lora = {name: value for name, value in lora.items() if value not in (None, "UNSET")}
    return fields | lora | ({"lora_enabled": "true"} if lora else {})


@dataclass
class _Import:
    """The form fields of an export file, with the file path that gives each field."""

    config: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    not_applied: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def apply(self, path: str, fields: dict[str, str] | None, switch: dict[str, str]) -> None:
        """Add the form fields of the file value at `path`. When `fields` is None, list the path as not applied.

        Only `fields` record `path` as their source, so the rejection of one value keeps the shared section switch.
        """
        if fields is None:
            self.not_applied.append(path)
            return
        self.config |= fields | (switch if fields else {})
        self.sources |= dict.fromkeys(fields, path)

    def add_channel_url(self, path: str, value: Any) -> None:
        """Add the channel fields of the channel URL at `path`."""
        try:
            url = decode_channel_url(value) if isinstance(value, str) else None
        except InvalidChannelUrl:
            self.not_applied.append(path)
            self.warnings.append(f"{path}: {INVALID_URL_MESSAGE}")
            return
        self.apply(path, None if url is None else _channel_fields(url), {})
        self.warnings += [f"{path}: {warning}" for warning in (url.warnings if url else [])]

    def add_part(self, part: str, sections: Any) -> None:
        """Add the form fields of the `config` or the `module_config` part."""
        if not isinstance(sections, dict):
            self.apply(part, None, {})
            return
        for section, values in sections.items():
            key = (part, camel_to_snake(str(section)))
            converters = _SECTIONS.get(key)
            if converters is None or not isinstance(values, dict):
                self.apply(f"{part}.{section}", None, {})
                continue
            switch = {_SECTION_SWITCHES[key]: "true"} if key in _SECTION_SWITCHES else {}
            for name, value in values.items():
                convert = converters.get(camel_to_snake(str(name)))
                self.apply(f"{part}.{section}.{name}", convert(value, values) if convert else None, switch)

    def result(self) -> CliYamlImport:
        """Return the form fields that the form accepts, in the form value format, and list each rejected path."""
        while True:
            try:
                form = ConfigRequest.model_validate(self.config)
            except ValidationError as error:
                self._reject(error)
                continue
            config = flat_channel_keys(form.model_dump(exclude_unset=True))
            return CliYamlImport(config=config, not_applied=self.not_applied, warnings=self.warnings)

    def _reject(self, error: ValidationError) -> None:
        """Remove every form field of each file value that has a field error, and list its path with the reason."""
        reasons = {}
        for item in error.errors(include_input=False, include_url=False):
            # The form nests each channel field, so the location `("channel_0", "psk")` is the field `channel_0[psk]`.
            location = [str(part) for part in item["loc"]]
            name = location[0] if len(location) == 1 else f"{location[0]}[{location[1]}]"
            reasons.setdefault(self.sources[name], str(item.get("ctx", {}).get("error", item["msg"])))
        for path, reason in reasons.items():
            for name in [name for name, source in self.sources.items() if source == path]:
                del self.config[name], self.sources[name]
            self.not_applied.append(path)
            self.warnings.append(f"{path}: {reason}")


def read_cli_export(data: bytes) -> CliYamlImport:
    """Return the form fields of a `meshtastic --export-config` file, and the file paths that the form cannot take.

    Args:
        data: The file, at most `MAX_EXPORT_BYTES` bytes.

    Raises:
        InvalidExportFile: The file is not UTF-8, `yaml.safe_load` does not read it, or it holds no mapping.
    """
    try:
        export = yaml.safe_load(data.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError, RecursionError):
        # The error text can quote the file, and the file holds the PSKs.
        raise InvalidExportFile from None
    if not isinstance(export, dict):
        raise InvalidExportFile
    result = _Import()
    for key, value in export.items():
        path = str(key)
        if path in _PARTS:
            result.add_part(path, value)
        elif path in _CHANNEL_URL_KEYS:
            result.add_channel_url(path, value)
        else:
            convert = _TOP_LEVEL.get(path)
            result.apply(path, convert(value, export) if convert else None, {})
    return result.result()
