"""Device variant registry loaded from YAML."""

from dataclasses import dataclass
from pathlib import Path

import yaml

# Architecture → firmware format mapping
FIRMWARE_FORMATS = {
    "esp32": "bin",
    "nrf52": "uf2",
    "rp2040": "uf2",
    "stm32": "bin",
}


@dataclass(frozen=True)
class DeviceVariant:
    """A supported Meshtastic device variant."""

    id: str
    name: str
    manufacturer: str
    architecture: str
    pio_platform: str = ""

    @property
    def firmware_format(self) -> str:
        """Derive firmware format from architecture."""
        return FIRMWARE_FORMATS[self.architecture]

    @property
    def has_factory_binary(self) -> bool:
        """ESP32 devices produce both firmware.bin and firmware.factory.bin."""
        return self.architecture == "esp32"


class DeviceRegistry:
    """Load and query device variants from YAML."""

    def __init__(self, variants_path: Path):
        self._variants: dict[str, DeviceVariant] = {}
        self._by_manufacturer: dict[str, list[DeviceVariant]] = {}
        self._load(variants_path)

    def _load(self, path: Path) -> None:
        with open(path) as f:
            entries = yaml.safe_load(f)

        if not entries:
            raise ValueError(f"No variants found in {path}")

        seen_ids: set[str] = set()
        for entry in entries:
            # Validate required fields
            for field in ("id", "name", "manufacturer", "architecture"):
                if field not in entry:
                    raise ValueError(f"Variant missing required field '{field}': {entry}")

            vid = entry["id"]
            arch = entry["architecture"]

            if vid in seen_ids:
                raise ValueError(f"Duplicate variant ID: {vid}")
            if arch not in FIRMWARE_FORMATS:
                raise ValueError(f"Unknown architecture '{arch}' for variant '{vid}'")

            seen_ids.add(vid)
            variant = DeviceVariant(
                id=vid,
                name=entry["name"],
                manufacturer=entry["manufacturer"],
                architecture=arch,
                pio_platform=entry.get("pio_platform", ""),
            )
            self._variants[vid] = variant

            if variant.manufacturer not in self._by_manufacturer:
                self._by_manufacturer[variant.manufacturer] = []
            self._by_manufacturer[variant.manufacturer].append(variant)

    def get(self, variant_id: str) -> DeviceVariant:
        """Look up a variant by ID. Raises KeyError if not found."""
        if variant_id not in self._variants:
            raise KeyError(f"Unknown device variant: {variant_id}")
        return self._variants[variant_id]

    def exists(self, variant_id: str) -> bool:
        """Check if a variant ID is valid."""
        return variant_id in self._variants

    @property
    def all_variants(self) -> list[DeviceVariant]:
        """All variants in insertion order."""
        return list(self._variants.values())

    @property
    def by_manufacturer(self) -> dict[str, list[DeviceVariant]]:
        """Variants grouped by manufacturer."""
        return dict(self._by_manufacturer)

    @property
    def count(self) -> int:
        return len(self._variants)
