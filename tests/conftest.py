"""Shared test fixtures."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def variants_path():
    """Path to the real variants.yaml file."""
    return Path(__file__).resolve().parent.parent / "devices" / "variants.yaml"


@pytest.fixture
def temp_dir():
    """Temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_config_data():
    """Minimal valid configuration data."""
    return {
        "device_name": "TestNode",
        "owner_short_name": "TST",
        "owner_long_name": "Test Node",
        "channels_to_write": "1",
        "channel_0[name]": "Primary",
        "channel_0[psk]": "deadbeef" * 8,
    }
