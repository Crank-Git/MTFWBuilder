"""Async firmware build orchestration with PlatformIO.

Serialized builds (Semaphore=1) to prevent shared firmware tree race condition.
Line-by-line stdout streaming for SSE progress. Configurable build timeout.
"""

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from mtfwbuilder.config import Settings
from mtfwbuilder.services.device_registry import DeviceVariant

logger = logging.getLogger("mtfwbuilder.build")

# Serialized: shared firmware tree prevents concurrent builds
_build_semaphore: asyncio.Semaphore | None = None
_build_queue: asyncio.Queue | None = None


def init_build_system(settings: Settings) -> None:
    """Initialize the build semaphore and queue."""
    global _build_semaphore, _build_queue
    _build_semaphore = asyncio.Semaphore(1)
    _build_queue = asyncio.Queue(maxsize=settings.max_queue_size)


@dataclass
class BuildProgress:
    """Progress event sent to SSE clients."""

    status: str  # queued, compiling, linking, complete, failed
    message: str = ""
    progress: int = 0
    download_url: str = ""
    error: str = ""


@dataclass
class BuildContext:
    """All state for a single firmware build."""

    build_id: str
    variant: DeviceVariant
    config_content: str
    settings: Settings
    build_dir: Path = field(default_factory=Path)
    firmware_path: Path | None = None
    factory_path: Path | None = None
    build_log: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.build_dir = self.settings.temp_dir / self.build_id
        self.build_dir.mkdir(parents=True, exist_ok=True)


async def build_firmware(ctx: BuildContext):
    """Run a firmware build, yielding BuildProgress events via async generator.

    Usage:
        async for progress in build_firmware(ctx):
            send_to_client(progress)
    """
    if _build_semaphore is None:
        raise RuntimeError("Build system not initialized — call init_build_system()")

    # Try to acquire build slot
    if _build_semaphore.locked():
        yield BuildProgress(status="queued", message="Waiting for current build to finish...")

    async with _build_semaphore:
        yield BuildProgress(status="compiling", message=f"Building firmware for {ctx.variant.name}...")

        try:
            async with asyncio.timeout(ctx.settings.build_timeout_seconds):
                async for progress in _run_pio_build(ctx):
                    yield progress
        except asyncio.TimeoutError:
            error_msg = f"Build timed out after {ctx.settings.build_timeout_seconds // 60} minutes"
            logger.error(f"Build {ctx.build_id}: {error_msg}")
            yield BuildProgress(status="failed", error=error_msg)
            return

        # Find and copy firmware files
        try:
            await _extract_firmware(ctx)
        except FileNotFoundError as e:
            yield BuildProgress(status="failed", error=str(e))
            return

        download_url = f"/api/v1/download-firmware/{ctx.build_id}?variant={ctx.variant.id}"
        yield BuildProgress(
            status="complete",
            message="Build complete!",
            download_url=download_url,
        )


# Type alias for the async generator
async def _run_pio_build(ctx: BuildContext):
    """Run PlatformIO build as async subprocess, streaming stdout line-by-line."""
    firmware_dir = ctx.settings.firmware_dir

    if not firmware_dir.exists():
        yield BuildProgress(status="failed", error="Firmware source not installed. Use Admin panel to update.")
        return

    # Write userPrefs.jsonc to build locations
    configs_dir = firmware_dir / "configs"
    configs_dir.mkdir(exist_ok=True)
    prefs_path = configs_dir / "userPrefs.jsonc"
    prefs_path.write_text(ctx.config_content)
    (firmware_dir / "userPrefs.jsonc").write_text(ctx.config_content)

    logger.info(f"Build {ctx.build_id}: config written, starting PIO for {ctx.variant.id}")

    # Build command — no shell=True, no --silent, no custom build flags
    import multiprocessing

    jobs = min(multiprocessing.cpu_count(), 8)
    cmd = [
        "pio", "run",
        "-e", ctx.variant.id,
        "--jobs", str(jobs),
        "--disable-auto-clean",
    ]

    env = os.environ.copy()
    # Build cache (preserves incremental builds)
    env["PLATFORMIO_BUILD_CACHE_DIR"] = str(firmware_dir / ".pio" / "build_cache")
    # Disable color output for clean log parsing
    env["PLATFORMIO_FORCE_COLOR"] = "false"
    env["PLATFORMIO_NO_ANSI"] = "1"

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(firmware_dir),
        env=env,
    )

    last_status = ""
    async for raw_line in process.stdout:
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        ctx.build_log.append(line)

        # Detect progress milestones
        status = _parse_progress(line)
        if status and status != last_status:
            last_status = status
            yield BuildProgress(status=status, message=line[:200])

    exit_code = await process.wait()

    if exit_code != 0:
        error_lines = [l for l in ctx.build_log[-20:] if "error" in l.lower()]
        error_summary = error_lines[-1] if error_lines else f"PlatformIO exited with code {exit_code}"
        logger.error(f"Build {ctx.build_id} failed: {error_summary}")
        yield BuildProgress(
            status="failed",
            error=error_summary,
            message="Build failed. See build log for details.",
        )


def _parse_progress(line: str) -> str | None:
    """Parse a PlatformIO output line for progress milestones."""
    lower = line.lower()
    if "compiling" in lower or "building" in lower:
        return "compiling"
    if "linking" in lower:
        return "linking"
    if "checking size" in lower or "building firmware image" in lower:
        return "packaging"
    if "success" in lower:
        return "complete"
    if "error" in lower and "warning" not in lower:
        return "failed"
    return None


async def _extract_firmware(ctx: BuildContext) -> None:
    """Find and copy firmware files to the build directory."""
    firmware_dir = ctx.settings.firmware_dir
    variant_id = ctx.variant.id
    fmt = ctx.variant.firmware_format

    # Check default PIO build output location
    pio_build_dir = firmware_dir / ".pio" / "build" / variant_id
    firmware_filename = f"firmware.{fmt}"
    source_path = pio_build_dir / firmware_filename

    if not source_path.exists():
        raise FileNotFoundError(
            f"Build completed but {firmware_filename} not found at {source_path}"
        )

    # Copy firmware to isolated build directory
    dest = ctx.build_dir / firmware_filename
    shutil.copy2(str(source_path), str(dest))
    ctx.firmware_path = dest
    logger.info(f"Build {ctx.build_id}: firmware copied to {dest}")

    # For ESP32: also copy factory binary
    if ctx.variant.has_factory_binary:
        factory_source = pio_build_dir / "firmware.factory.bin"
        if factory_source.exists():
            factory_dest = ctx.build_dir / "firmware.factory.bin"
            shutil.copy2(str(factory_source), str(factory_dest))
            ctx.factory_path = factory_dest
            logger.info(f"Build {ctx.build_id}: factory binary copied")

    # Clean up sensitive files from firmware tree
    _scrub_firmware_tree(ctx)


def _scrub_firmware_tree(ctx: BuildContext) -> None:
    """Remove userPrefs and firmware files from the shared firmware tree."""
    firmware_dir = ctx.settings.firmware_dir

    for path in [
        firmware_dir / "configs" / "userPrefs.jsonc",
        firmware_dir / "userPrefs.jsonc",
    ]:
        if path.exists():
            path.unlink()

    # Remove firmware files from PIO build dir (contain baked-in PSK)
    variant_build = firmware_dir / ".pio" / "build" / ctx.variant.id
    for pattern in ["firmware.bin", "firmware.uf2", "firmware.factory.bin"]:
        p = variant_build / pattern
        if p.exists():
            p.unlink()

    logger.debug(f"Build {ctx.build_id}: sensitive files scrubbed from firmware tree")


_build_counter = 0


def generate_build_id() -> str:
    """Generate a unique build ID."""
    global _build_counter
    _build_counter += 1
    return f"build_{int(time.time())}_{os.getpid()}_{_build_counter}"
