"""Tests for device variant registry."""

import tempfile
from pathlib import Path

import pytest
import yaml

from mtfwbuilder.services.device_registry import DeviceRegistry, DeviceVariant


class TestDeviceRegistryLoad:
    """Tests for loading and validating the YAML registry."""

    def test_load_real_variants(self, variants_path):
        """Load the real variants.yaml — must not raise."""
        registry = DeviceRegistry(variants_path)
        assert registry.count > 60

    def test_no_duplicate_ids(self, variants_path):
        """Every variant ID must be unique."""
        registry = DeviceRegistry(variants_path)
        ids = [v.id for v in registry.all_variants]
        assert len(ids) == len(set(ids))

    def test_all_architectures_valid(self, variants_path):
        """Every variant must have a recognized architecture."""
        registry = DeviceRegistry(variants_path)
        valid = {"esp32", "nrf52", "rp2040", "stm32"}
        for v in registry.all_variants:
            assert v.architecture in valid, f"{v.id} has unknown architecture: {v.architecture}"

    def test_all_required_fields(self, variants_path):
        """Every variant must have id, name, manufacturer, architecture."""
        with open(variants_path) as f:
            entries = yaml.safe_load(f)
        for entry in entries:
            for field in ("id", "name", "manufacturer", "architecture"):
                assert field in entry, f"Entry missing '{field}': {entry}"

    def test_empty_yaml_raises(self, temp_dir):
        """Loading an empty YAML file should raise ValueError."""
        empty = temp_dir / "empty.yaml"
        empty.write_text("")
        with pytest.raises(ValueError, match="No variants found"):
            DeviceRegistry(empty)

    def test_missing_field_raises(self, temp_dir):
        """A variant missing a required field should raise ValueError."""
        bad = temp_dir / "bad.yaml"
        bad.write_text(yaml.dump([{"id": "test", "name": "Test"}]))
        with pytest.raises(ValueError, match="missing required field"):
            DeviceRegistry(bad)

    def test_duplicate_id_raises(self, temp_dir):
        """Duplicate variant IDs should raise ValueError."""
        dup = temp_dir / "dup.yaml"
        entries = [
            {"id": "test", "name": "Test 1", "manufacturer": "X", "architecture": "esp32"},
            {"id": "test", "name": "Test 2", "manufacturer": "Y", "architecture": "esp32"},
        ]
        dup.write_text(yaml.dump(entries))
        with pytest.raises(ValueError, match="Duplicate variant ID"):
            DeviceRegistry(dup)

    def test_unknown_architecture_raises(self, temp_dir):
        """An unknown architecture should raise ValueError."""
        bad = temp_dir / "bad_arch.yaml"
        entries = [{"id": "x", "name": "X", "manufacturer": "Y", "architecture": "avr"}]
        bad.write_text(yaml.dump(entries))
        with pytest.raises(ValueError, match="Unknown architecture"):
            DeviceRegistry(bad)


class TestDeviceRegistryLookup:
    """Tests for querying the registry."""

    def test_get_known_variant(self, variants_path):
        """Looking up a known variant returns the correct DeviceVariant."""
        registry = DeviceRegistry(variants_path)
        v = registry.get("tbeam")
        assert v.id == "tbeam"
        assert v.manufacturer == "LILYGO"
        assert v.architecture == "esp32"

    def test_get_unknown_variant_raises(self, variants_path):
        """Looking up an unknown variant raises KeyError."""
        registry = DeviceRegistry(variants_path)
        with pytest.raises(KeyError, match="Unknown device variant"):
            registry.get("nonexistent-device-xyz")

    def test_exists_true(self, variants_path):
        registry = DeviceRegistry(variants_path)
        assert registry.exists("rak4631") is True

    def test_exists_false(self, variants_path):
        registry = DeviceRegistry(variants_path)
        assert registry.exists("not-a-device") is False

    def test_by_manufacturer_groups(self, variants_path):
        """Variants are correctly grouped by manufacturer."""
        registry = DeviceRegistry(variants_path)
        groups = registry.by_manufacturer
        assert "LILYGO" in groups
        assert "RAK" in groups
        lilygo_ids = [v.id for v in groups["LILYGO"]]
        assert "tbeam" in lilygo_ids


class TestFirmwareFormat:
    """Tests for architecture → firmware format derivation."""

    def test_esp32_produces_bin(self, variants_path):
        registry = DeviceRegistry(variants_path)
        v = registry.get("tbeam")
        assert v.firmware_format == "bin"

    def test_nrf52_produces_uf2(self, variants_path):
        registry = DeviceRegistry(variants_path)
        v = registry.get("rak4631")
        assert v.firmware_format == "uf2"

    def test_rp2040_produces_uf2(self, variants_path):
        registry = DeviceRegistry(variants_path)
        v = registry.get("rpipico")
        assert v.firmware_format == "uf2"

    def test_esp32_has_factory_binary(self, variants_path):
        registry = DeviceRegistry(variants_path)
        v = registry.get("tbeam")
        assert v.has_factory_binary is True

    def test_nrf52_no_factory_binary(self, variants_path):
        registry = DeviceRegistry(variants_path)
        v = registry.get("t-echo")
        assert v.has_factory_binary is False

    def test_tracker_t1000e_is_nrf52(self, variants_path):
        """tracker-t1000-e is nRF52, not ESP32 — a common mistake."""
        registry = DeviceRegistry(variants_path)
        v = registry.get("tracker-t1000-e")
        assert v.architecture == "nrf52"
        assert v.firmware_format == "uf2"

    def test_all_formats_are_bin_or_uf2(self, variants_path):
        """Every variant must produce either .bin or .uf2."""
        registry = DeviceRegistry(variants_path)
        for v in registry.all_variants:
            assert v.firmware_format in ("bin", "uf2"), f"{v.id}: {v.firmware_format}"
