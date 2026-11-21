"""Build artifact cleanup, and the scrub of the firmware source after a build."""

import asyncio
import logging
import shutil
import time
from collections.abc import Callable, Collection
from pathlib import Path

from mtfwbuilder.config import Settings

logger = logging.getLogger("mtfwbuilder.cleanup")


async def cleanup_build_directory(build_dir: Path) -> None:
    """Remove a build directory and all its contents."""
    if build_dir.exists():
        shutil.rmtree(str(build_dir), ignore_errors=True)
        logger.info(f"Cleaned up build directory: {build_dir}")


def cleanup_old_builds(settings: Settings, keep: Collection[str] = ()) -> int:
    """Return the number of build directories removed, after each one older than `build_max_age_seconds` is removed.

    A build directory is `<temp_dir>/<build_id>` and holds the artifacts of one build. Its age is the age of
    its last change. `keep` holds the build ids of the builds that have not ended, and their directories stay.
    """
    temp_dir = settings.temp_dir
    if not temp_dir.exists():
        return 0

    oldest = time.time() - settings.build_max_age_seconds
    removed = 0
    for build_path in temp_dir.iterdir():
        if build_path.name in keep or not build_path.is_dir() or build_path.stat().st_mtime > oldest:
            continue
        shutil.rmtree(build_path, ignore_errors=True)
        removed += 1
        logger.info(f"Removed old build directory: {build_path.name}")

    if removed:
        logger.info(f"Periodic cleanup: removed {removed} old build directories")
    return removed


async def cleanup_every_interval(settings: Settings, unended_build_ids: Callable[[], Collection[str]]) -> None:
    """Remove the old build directories now, then again each `cleanup_interval_seconds`, until cancelled."""
    while True:
        cleanup_old_builds(settings, keep=unended_build_ids())
        await asyncio.sleep(settings.cleanup_interval_seconds)


def scrub_after_restart(firmware_dir: Path) -> None:
    """Delete `userPrefs.jsonc` and each `.pio/build/<env>/` directory from the firmware source.

    A build scrubs its own env directory when it ends, so an env directory at startup is from a build
    that a restart stopped. Raises `OSError` when a delete fails.
    """
    (firmware_dir / "userPrefs.jsonc").unlink(missing_ok=True)
    pio_build = firmware_dir / ".pio" / "build"
    if pio_build.is_dir():
        for env_build in pio_build.iterdir():
            if env_build.is_dir():
                scrub_firmware_source(firmware_dir, env_build.name)


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
