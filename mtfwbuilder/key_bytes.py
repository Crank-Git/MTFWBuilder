"""Decode a PSK or an admin key, and write it as the `{ 0x.. }` byte array that upstream reads."""

import base64
import binascii
import re

# Upstream copies 32 bytes from each admin key array (`src/mesh/NodeDB.cpp`).
ADMIN_KEY_LENGTH = 32

_HEX = re.compile(r"(?:[0-9a-fA-F]{2})+")
_BYTE_LIST = re.compile(r"\{\s*(0x[0-9a-fA-F]{1,2}(?:\s*,\s*0x[0-9a-fA-F]{1,2})*)?\s*\}")


def decode_key(value: str) -> bytes:
    """Return the bytes of a key in hex, `{ 0x.. }` or base64 form.

    The function tries hex before base64. A base64 key of 16 or 32 bytes ends in `=`, so it never matches hex.

    Raises:
        ValueError: The value is not in one of the three forms.
    """
    key = value.strip()
    if _HEX.fullmatch(key):
        return bytes.fromhex(key)
    byte_list = _BYTE_LIST.fullmatch(key)
    if byte_list:
        return bytes(int(item, 16) for item in byte_list[1].split(",")) if byte_list[1] else b""
    try:
        return base64.b64decode(key, validate=True)
    except binascii.Error:
        raise ValueError("the key is not hex, base64 or a { 0x.. } byte list") from None


def byte_array(data: bytes) -> str:
    """Return `data` as a `{ 0x.., 0x.. }` C array initializer."""
    return "{ " + ", ".join(f"0x{b:02x}" for b in data) + " }"
