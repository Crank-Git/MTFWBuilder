"""Build artifact cleanup, and the scrub of the firmware source after a build."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from collections.abc import Callable, Collection
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # config.py imports this module to scrub an old firmware source at startup.
    from mtfwbuilder.config import Settings

logger = logging.getLogger("mtfwbuilder.cleanup")
# `generate_build_id` gives 32 lower-case hex characters, and `temp_dir` can hold other directories.
BUILD_ID = re.compile(r"[0-9a-f]{32}")


def cleanup_old_builds(settings: Settings, keep: Collection[str] = ()) -> int:
    """Return the number of build directories removed, after each one older than `build_max_age_seconds` is removed.

    A build directory is `<temp_dir>/<build_id>` and holds the artifacts of one build. Its age is the age of
    its last change. `keep` holds the build ids of the builds that have not ended, and their directories stay.
    A directory whose name is not a build id stays.
    """
    temp_dir = settings.temp_dir
    if not temp_dir.exists():
        return 0

    oldest = time.time() - settings.build_max_age_seconds
    removed = 0
    for build_path in temp_dir.iterdir():
        if not BUILD_ID.fullmatch(build_path.name) or build_path.name in keep or not build_path.is_dir():
            continue
        if build_path.stat().st_mtime > oldest:
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


def scrub_sources_after_restart(firmware_dir: Path) -> None:
    """Run `scrub_after_restart` on each firmware source under `firmware_dir`.

    Each firmware source is `<firmware_dir>/<tag>/`. When the startup import cannot move an old firmware source,
    the old firmware source is `<firmware_dir>` itself. Raises `OSError` when a delete fails.
    """
    if (firmware_dir / "platformio.ini").is_file():
        scrub_after_restart(firmware_dir)
    elif firmware_dir.is_dir():
        for source_dir in firmware_dir.iterdir():
            if source_dir.is_dir():
                scrub_after_restart(source_dir)


def scrub_after_restart(firmware_dir: Path) -> None:
    """Delete `userPrefs.jsonc`, `.pio/build_cache` and each `.pio/build/<env>/` directory from the firmware source.

    The scrub also covers each build worker copy `<firmware_dir>/.worker-<n>/`. A build scrubs its own env
    directory when it ends, so an env directory at startup is from a build that a restart stopped.
    Raises `OSError` when a delete fails.
    """
    for copy in firmware_dir.glob(".worker-*"):
        if copy.is_dir():
            _scrub_every_env(copy)
    _scrub_every_env(firmware_dir)


def _scrub_every_env(firmware_dir: Path) -> None:
    _delete_shared_secrets(firmware_dir)
    pio_build = firmware_dir / ".pio" / "build"
    if pio_build.is_dir():
        for env_build in pio_build.iterdir():
            if env_build.is_dir():
                scrub_firmware_source(firmware_dir, env_build.name)


def scrub_firmware_source(firmware_dir: Path, env_name: str) -> None:
    """Delete `userPrefs.jsonc`, `.pio/build_cache` and `.pio/build/<env_name>/` from the firmware source.

    All three hold the secrets of the last build: the file holds the config, and the object files hold the PSK.
    Raises `OSError` when a delete fails.
    """
    _delete_shared_secrets(firmware_dir)
    env_build = firmware_dir / ".pio" / "build" / env_name
    if env_build.exists():
        shutil.rmtree(env_build)
    logger.debug(f"Scrubbed the firmware source for {env_name}")


def _delete_shared_secrets(firmware_dir: Path) -> None:
    (firmware_dir / "userPrefs.jsonc").unlink(missing_ok=True)
    # A build cache from an older version holds object files compiled with the `-D USERPREFS_*` flags.
    build_cache = firmware_dir / ".pio" / "build_cache"
    if build_cache.exists():
        shutil.rmtree(build_cache)
