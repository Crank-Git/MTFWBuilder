"""Build artifact cleanup, and the scrub of the firmware source after a build."""

import logging
import os
import shutil
import time
from pathlib import Path

from mtfwbuilder.config import Settings

logger = logging.getLogger("mtfwbuilder.cleanup")


async def cleanup_build_directory(build_dir: Path) -> None:
    """Remove a build directory and all its contents."""
    if build_dir.exists():
        shutil.rmtree(str(build_dir), ignore_errors=True)
        logger.info(f"Cleaned up build directory: {build_dir}")


def cleanup_old_builds(settings: Settings) -> int:
    """Remove build directories older than max age. Returns count removed."""
    temp_dir = settings.temp_dir
    if not temp_dir.exists():
        return 0

    removed = 0
    current_time = time.time()
    max_age = settings.build_max_age_seconds

    for item in os.listdir(temp_dir):
        if not item.startswith("build_"):
            continue

        build_path = temp_dir / item
        if not build_path.is_dir():
            continue

        try:
            # Extract timestamp from directory name (build_<timestamp>_<pid>)
            parts = item.replace("build_", "").split("_")
            timestamp = int(parts[0])
            if current_time - timestamp > max_age:
                shutil.rmtree(str(build_path), ignore_errors=True)
                removed += 1
                logger.info(f"Removed old build directory: {item}")
        except (ValueError, IndexError):
            # Can't parse timestamp — remove if modified long ago
            mtime = build_path.stat().st_mtime
            if current_time - mtime > max_age:
                shutil.rmtree(str(build_path), ignore_errors=True)
                removed += 1

    if removed:
        logger.info(f"Periodic cleanup: removed {removed} old build directories")
    return removed


def scrub_firmware_source(firmware_dir: Path, env_name: str) -> None:
    """Delete `userPrefs.jsonc` and `.pio/build/<env_name>/` from the firmware source.

    Both hold the secrets of the last build: the file holds the config, and the object files hold the PSK.
    The build cache in `.pio/build_cache` stays. Raises `OSError` when a delete fails.
    """
    (firmware_dir / "userPrefs.jsonc").unlink(missing_ok=True)
    env_build = firmware_dir / ".pio" / "build" / env_name
    if env_build.exists():
        shutil.rmtree(env_build)
    logger.debug(f"Scrubbed the firmware source for {env_name}")
