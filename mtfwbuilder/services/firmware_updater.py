"""Firmware source updater — downloads latest Meshtastic firmware from GitHub.

Migrated from utils/firmware_updater.py. Shell=True removed, logging replaces print().
"""

import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import requests

from mtfwbuilder.config import Settings

logger = logging.getLogger("mtfwbuilder.firmware_updater")


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
        firmware_dir = settings.firmware_dir
        base_dir = settings.base_dir

        # Get latest release
        release_info = get_latest_release_info()
        if not release_info:
            return False

        # Save version info
        version_file = base_dir / "firmware_version.txt"
        version_file.write_text(
            f"Version: {release_info['version']}\nUpdated: {datetime.now().isoformat()}\n"
        )

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
            zf.extractall(temp_dir)

        extracted_dir = os.path.join(temp_dir, top_dir)

        # Replace existing firmware directory
        if firmware_dir.exists():
            logger.info(f"Removing existing firmware: {firmware_dir}")
            shutil.rmtree(str(firmware_dir))

        logger.info(f"Moving firmware to {firmware_dir}")
        shutil.move(extracted_dir, str(firmware_dir))

        # Set up PlatformIO (no shell=True)
        logger.info("Installing PlatformIO dependencies...")
        result = subprocess.run(
            ["pio", "pkg", "install"],
            cwd=str(firmware_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"PlatformIO setup failed: {result.stderr}")
            return False

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
