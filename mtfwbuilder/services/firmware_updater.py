"""Firmware source updater: download the latest Meshtastic firmware source from GitHub.

Each firmware source is the directory `<firmware_dir>/<tag>/`, and the `firmware_sources` table holds its record.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import requests

from mtfwbuilder.config import Settings
from mtfwbuilder.database import record_firmware_source

logger = logging.getLogger("mtfwbuilder.firmware_updater")

# The tag is also a directory name, so this pattern keeps a path separator out of it.
TAG = re.compile(r"v\d+\.\d+\.\d+\.[0-9a-f]{7}")


def get_latest_release_info() -> dict | None:
    """Get latest Meshtastic firmware release info from GitHub API."""
    try:
        logger.info("Fetching latest release info from GitHub...")
        api_url = "https://api.github.com/repos/meshtastic/firmware/releases/latest"
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()

        data = response.json()
        version = data["tag_name"]
        published = data["published_at"]

        logger.info(f"Latest release: {version} (published: {published})")
        return {
            "version": version,
            "published_date": published,
            "release_data": data,
        }
    except Exception as e:
        logger.error(f"Error fetching release info: {e}")
        return None


def update_firmware(settings: Settings) -> bool:
    """Download and set up the latest firmware source.

    Returns True on success, False on failure.
    """
    logger.info("Starting firmware update process...")
    temp_dir = tempfile.mkdtemp()

    try:
        base_dir = settings.base_dir

        # Get latest release
        release_info = get_latest_release_info()
        if not release_info:
            return False
        version = release_info["version"]
        if not TAG.fullmatch(version):
            logger.error(f"Release tag {version!r} is not a firmware version")
            return False
        source_dir = settings.firmware_dir / version

        # Save version info
        version_file = base_dir / "firmware_version.txt"
        version_file.write_text(f"Version: {release_info['version']}\nUpdated: {datetime.now().isoformat()}\n")

        # Download source zip
        source_url = release_info["release_data"]["zipball_url"]
        logger.info(f"Downloading source from {source_url}...")

        zip_path = os.path.join(temp_dir, "firmware_source.zip")
        resp = requests.get(source_url, stream=True, timeout=120)
        resp.raise_for_status()

        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # Extract
        logger.info("Extracting firmware source...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            top_dir = zf.namelist()[0].split("/")[0]
            # Validate zip entries to prevent zip-slip attacks
            for entry in zf.namelist():
                target = os.path.realpath(os.path.join(temp_dir, entry))
                if not target.startswith(os.path.realpath(temp_dir)):
                    raise ValueError(f"Zip entry would escape target directory: {entry}")
            zf.extractall(temp_dir)

        extracted_dir = os.path.join(temp_dir, top_dir)

        # Replace the directory of this firmware version only.
        if source_dir.exists():
            logger.info(f"Removing existing firmware: {source_dir}")
            shutil.rmtree(str(source_dir))

        logger.info(f"Moving firmware to {source_dir}")
        settings.firmware_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(extracted_dir, str(source_dir))

        # Set up PlatformIO (no shell=True)
        logger.info("Installing PlatformIO dependencies...")
        result = subprocess.run(
            ["pio", "pkg", "install"],
            cwd=str(source_dir),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            logger.error(f"PlatformIO setup failed: {result.stderr}")
            return False

        # `releases/latest` excludes prereleases, so the release is the newest stable firmware version.
        record_firmware_source(settings.database_path, _source_record(source_dir, "stable", datetime.now(UTC)))
        logger.info("Firmware update completed successfully!")
        return True

    except Exception as e:
        logger.error(f"Firmware update error: {e}")
        return False

    finally:
        logger.info(f"Cleaning up temp directory: {temp_dir}")
        shutil.rmtree(temp_dir, ignore_errors=True)


def get_firmware_version(settings: Settings) -> dict:
    """Read current firmware version info."""
    version_file = settings.base_dir / "firmware_version.txt"
    if not version_file.exists():
        return {"version": "Not installed", "last_updated": "Never"}

    lines = version_file.read_text().strip().splitlines()
    version = "Unknown"
    updated = "Unknown"
    for line in lines:
        if line.startswith("Version:"):
            version = line.replace("Version:", "").strip()
        elif line.startswith("Updated:"):
            updated = line.replace("Updated:", "").strip()

    return {"version": version, "last_updated": updated}


def import_old_firmware_source(settings: Settings) -> str | None:
    """Return the firmware version of the old firmware source, after it moves to `<firmware_dir>/<tag>/`.

    Before feature 09, the firmware source was `<firmware_dir>` itself, and `firmware_version.txt` held its
    firmware version. The import also writes the record of the firmware source. The `channel` is `stable` when
    the tag is the newest stable firmware version on GitHub, else `pinned`.

    Returns None, and changes nothing, in these cases:
    - `<firmware_dir>` holds no old firmware source.
    - The version file holds no tag.
    - `<firmware_dir>/<tag>/` exists.
    """
    firmware_dir = settings.firmware_dir
    if not (firmware_dir / "platformio.ini").is_file():
        return None
    info = get_firmware_version(settings)
    version = info["version"]
    if not TAG.fullmatch(version):
        logger.warning(f"The old firmware source stays in {firmware_dir}: {version!r} is not a firmware version")
        return None
    # An update after a skipped import installs `<tag>/` inside the old tree, and a move would nest it.
    if (firmware_dir / version).exists():
        logger.warning(f"The old firmware source stays in {firmware_dir}: {firmware_dir / version} exists")
        return None
    latest = get_latest_release_info()
    channel = "stable" if latest is not None and latest["version"] == version else "pinned"

    # Two renames in one parent directory move the tree without a copy.
    staging = firmware_dir.with_name(f"{firmware_dir.name}.import")
    firmware_dir.rename(staging)
    firmware_dir.mkdir()
    source_dir = firmware_dir / version
    staging.rename(source_dir)

    record_firmware_source(settings.database_path, _source_record(source_dir, channel, _installed_at(info)))
    logger.info(f"Imported the old firmware source as {source_dir}")
    return version


def _installed_at(info: dict) -> datetime:
    """Return the update time from the version file, or now when the file holds no ISO 8601 time."""
    try:
        return datetime.fromisoformat(info["last_updated"]).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def _source_record(source_dir: Path, channel: str, installed_at: datetime) -> dict:
    return {
        "version": source_dir.name,
        "channel": channel,
        "path": str(source_dir),
        "installed_at": installed_at.isoformat(timespec="seconds"),
        "size_bytes": _disk_size(source_dir),
        "status": "ready",
    }


def _disk_size(directory: Path) -> int:
    """Return the sum of the file sizes under `directory`, without a symlink target."""
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            total += os.lstat(os.path.join(root, name)).st_size
    return total
