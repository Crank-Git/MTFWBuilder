"""Decode a Meshtastic channel URL into its channels and LoRa settings.

The URL format is `Node.getURL` and `Node.setURL` in `meshtastic/node.py` of the `meshtastic` package 2.7.11:
the fragment is the URL-safe base64 form, without padding, of a `ChannelSet` protobuf.
"""

import base64
import binascii
from urllib.parse import urlsplit

from google.protobuf.message import DecodeError
from meshtastic.protobuf import apponly_pb2

from mtfwbuilder.meshtastic_enums import MODEM_PRESETS, REGIONS
from mtfwbuilder.models import CHANNEL_SLOTS, ChannelUrlImport, ImportedChannel, ImportedLora

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


def _channel(index: int, settings) -> ImportedChannel:
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
