"""Firmware builds: the build queue, the build workers and the progress stream.

A build runs in a build worker, so it continues when no progress stream is open.
The build keeps its build status and log lines in memory for the progress stream.
"""

import asyncio
import glob
import json
import logging
import multiprocessing
import os
import secrets
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from mtfwbuilder.config import Settings
from mtfwbuilder.models import BuildStatus
from mtfwbuilder.services.cleanup_service import scrub_firmware_source
from mtfwbuilder.services.device_registry import DeviceVariant
from mtfwbuilder.services.secret_mask import secret_masker

logger = logging.getLogger("mtfwbuilder.build")

FIRMWARE_SOURCE_MISSING = "Firmware source not installed. An admin must install a firmware version."
ENDED_STATUSES = frozenset({"complete", "failed", "cancelled"})
# The progress number of each build status that pio output shows.
STEP_PROGRESS = {"compiling": 10, "linking": 70, "packaging": 90}


class BuildQueueFull(Exception):
    """The build queue holds `max_queue_size` builds."""


class BuildEnded(Exception):
    """The build has a build status of `complete`, `failed` or `cancelled`."""


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
    status: str = "queued"
    message: str = ""
    progress: int = 0
    error: str = ""
    download_url: str = ""
    created_at: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    mask: Callable[[str], str] = field(init=False, repr=False)

    def __post_init__(self):
        self.build_dir = self.settings.temp_dir / self.build_id
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.mask = secret_masker(self.config_content)

    @property
    def ended(self) -> bool:
        return self.status in ENDED_STATUSES

    def set_status(self, status: str, message: str = "", error: str = "", download_url: str = "") -> None:
        """Set the build status and wake each progress stream; an ended build keeps its build status."""
        if self.ended:
            return
        self.status = status
        self.message = message
        self.error = error
        self.download_url = download_url
        self.progress = 100 if status == "complete" else STEP_PROGRESS.get(status, self.progress)
        self._notify()

    def add_log(self, line: str) -> str:
        """Return the line with each secret replaced by `***`, after it is stored and sent."""
        masked = self.mask(line)
        self.build_log.append(masked)
        self._notify()
        return masked

    def status_event(self) -> str:
        """Return the JSON of the current build status for the progress stream."""
        return BuildStatus(
            status=self.status,
            message=self.message,
            progress=self.progress,
            download_url=self.download_url,
            error=self.error,
        ).model_dump_json()

    def _notify(self) -> None:
        # A waiter holds the old event, so a set-and-replace wakes each waiter once.
        self.changed.set()
        self.changed = asyncio.Event()


class BuildQueue:
    """Hold the builds of this process and run them in build workers, in queue order."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._queue: asyncio.Queue[BuildContext] = asyncio.Queue(maxsize=settings.max_queue_size)
        self._builds: dict[str, BuildContext] = {}
        self._workers: list[asyncio.Task] = []

    def get(self, build_id: str) -> BuildContext | None:
        return self._builds.get(build_id)

    def submit(self, variant: DeviceVariant, config_content: str) -> BuildContext:
        """Return a new build, after it is put in the build queue.

        Raises `BuildQueueFull` when the build queue holds `max_queue_size` builds. A build
        for a missing firmware source fails at once and does not go in the build queue.
        """
        if self._queue.full():
            raise BuildQueueFull
        self._forget_old_builds()
        ctx = BuildContext(generate_build_id(), variant, config_content, self.settings)
        self._builds[ctx.build_id] = ctx
        if self.settings.firmware_dir.exists():
            self._queue.put_nowait(ctx)
        else:
            ctx.set_status("failed", error=FIRMWARE_SOURCE_MISSING)
        return ctx

    def cancel(self, build_id: str) -> BuildContext:
        """Return the build, after it changes to `cancelled`.

        Raises `KeyError` for an unknown build id, and `BuildEnded` for a build that has ended.
        """
        ctx = self._builds[build_id]
        if ctx.ended:
            raise BuildEnded
        ctx.set_status("cancelled", message="Build cancelled.")
        if ctx.task is not None:
            ctx.task.cancel()
        return ctx

    def start(self) -> None:
        """Start the build workers in the running event loop."""
        if self.settings.build_workers > 1:
            # TODO(#88): Copy the firmware source per build worker and start `build_workers` of them.
            logger.warning("MTFW_BUILD_WORKERS > 1 needs one firmware source per build worker; one build worker runs")
        self._workers = [asyncio.create_task(self._work())]

    async def stop(self) -> None:
        """Stop the build workers, and cancel the builds that run."""
        tasks = self._workers + [c.task for c in self._builds.values() if c.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers = []

    async def _work(self) -> None:
        while True:
            ctx = await self._queue.get()
            # A build cancelled while it waits stays in the queue until a build worker takes it.
            if not ctx.ended:
                ctx.task = asyncio.create_task(run_build(ctx))
                # `wait` does not raise when the build task is cancelled, so the build worker continues.
                await asyncio.wait({ctx.task})
                if not ctx.task.cancelled() and ctx.task.exception() is not None:
                    # Without an ended build status, the progress stream of the build never ends.
                    logger.error(f"Build {ctx.build_id} raised an error", exc_info=ctx.task.exception())
                    ctx.set_status("failed", error="The build stopped because of an internal error.")
            self._queue.task_done()

    def _forget_old_builds(self) -> None:
        oldest = time.monotonic() - self.settings.build_max_age_seconds
        for build_id in [k for k, c in self._builds.items() if c.ended and c.created_at < oldest]:
            del self._builds[build_id]


async def progress_events(ctx: BuildContext):
    """Yield the SSE events of the progress stream for one build.

    The first event is the current build status. Then the stream sends each change of the
    build status and each new log line. The stream ends after the build ends. A closed
    stream has no effect on the build.
    """
    sent_status = None
    log_index = len(ctx.build_log)
    while True:
        # Hold the event before the read, so a change during a yield is not lost.
        changed = ctx.changed
        status = ctx.status_event()
        if status != sent_status:
            sent_status = status
            yield {"event": "status", "data": status}
        for line in ctx.build_log[log_index:]:
            yield {"event": "log", "data": json.dumps({"line": line})}
        log_index = len(ctx.build_log)
        if ctx.ended:
            return
        await changed.wait()


async def run_build(ctx: BuildContext) -> None:
    """Run one build to an ended build status, and scrub the firmware source before the end event."""
    if not ctx.settings.firmware_dir.exists():
        ctx.set_status("failed", error=FIRMWARE_SOURCE_MISSING)
        return
    ctx.set_status("compiling", message=f"Building firmware for {ctx.variant.name}...")
    try:
        status, fields = await _compile(ctx)
    finally:
        # A cancel arrives as `CancelledError`, and an internal error as any exception, so both pass here.
        scrub_firmware_source(ctx.settings.firmware_dir, ctx.variant.id)
    ctx.set_status(status, **fields)


async def _compile(ctx: BuildContext) -> tuple[str, dict[str, str]]:
    """Return the ended build status and its `set_status` fields, after pio runs and the artifacts are copied."""
    try:
        async with asyncio.timeout(ctx.settings.build_timeout_seconds):
            exit_code = await _run_pio_build(ctx)
        if exit_code != 0:
            return "failed", {
                "message": "Build failed. See build log for details.",
                "error": _error_summary(ctx, exit_code),
            }
        _extract_firmware(ctx)
    except TimeoutError:
        error_msg = f"Build timed out after {ctx.settings.build_timeout_seconds // 60} minutes"
        logger.error(f"Build {ctx.build_id}: {error_msg}")
        return "failed", {"error": error_msg}
    except OSError as e:
        # A missing pio executable, a missing artifact and a full disk all raise OSError.
        logger.error(f"Build {ctx.build_id} failed: {e}")
        return "failed", {"error": str(e)}
    download_url = f"/api/v1/download-firmware/{ctx.build_id}?variant={ctx.variant.id}"
    return "complete", {"message": "Build complete!", "download_url": download_url}


def _error_summary(ctx: BuildContext, exit_code: int) -> str:
    tail = ctx.build_log[-20:]
    # The first `error:` line names the cause; the later `*** [...] Error 1` line only repeats it.
    causes = [line for line in tail if "error:" in line.lower()]
    other_errors = [line for line in tail if "error" in line.lower()]
    if causes:
        summary = causes[0]
    elif other_errors:
        summary = other_errors[-1]
    else:
        summary = f"PlatformIO exited with code {exit_code}"
    logger.error(f"Build {ctx.build_id} failed: {summary}")
    return summary


async def _run_pio_build(ctx: BuildContext) -> int:
    """Return the exit code of `pio run` for the build, after each output line goes to the build log.

    A cancel of the build kills the pio process.
    """
    firmware_dir = ctx.settings.firmware_dir
    # Upstream `bin/platformio-custom.py` reads only `<PROJECT_DIR>/userPrefs.jsonc`.
    (firmware_dir / "userPrefs.jsonc").write_text(ctx.config_content)

    logger.info(f"Build {ctx.build_id}: config written, starting PIO for {ctx.variant.id}")

    # Build command — no shell=True, no --silent, no custom build flags
    jobs = min(multiprocessing.cpu_count(), 8)
    cmd = [
        "pio",
        "run",
        "-e",
        ctx.variant.id,
        "--jobs",
        str(jobs),
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

    try:
        async for raw_line in process.stdout:
            line = ctx.add_log(raw_line.decode("utf-8", errors="replace").rstrip())
            status = _parse_progress(line)
            # Only the exit code of pio decides `complete` or `failed`, so one build sends one end event.
            if status in STEP_PROGRESS and status != ctx.status:
                ctx.set_status(status, message=line[:200])
        return await process.wait()
    except asyncio.CancelledError:
        # A cancel or a timeout must not leave pio to write into the firmware source.
        process.kill()
        await process.wait()
        raise


def _parse_progress(line: str) -> str | None:
    """Parse a PlatformIO output line for progress milestones."""
    lower = line.lower()
    if "compiling" in lower or "building .pio" in lower:
        return "compiling"
    if "linking .pio" in lower:
        return "linking"
    if "checking size" in lower or "building firmware image" in lower:
        return "packaging"
    if "[success]" in lower:
        return "complete"
    if "[failed]" in lower or (lower.strip().startswith("error") and ":" in lower):
        return "failed"
    return None


def _find_artifact(pio_build_dir: Path, env_name: str, extension: str) -> Path | None:
    """Return the upstream `firmware-<env>-<version>.<extension>` file, or None when pio wrote none.

    Upstream `bin/platformio-pre.py` sets `PROGNAME = firmware-<env>-<version>` (meshtastic/firmware PR #8248).
    """
    pattern = str(pio_build_dir / f"firmware-{glob.escape(env_name)}-*.{extension}")
    # The `.bin` pattern also matches the `.factory.bin` file of ESP32.
    matches = sorted(p for p in glob.glob(pattern) if extension == "factory.bin" or not p.endswith(".factory.bin"))
    return Path(matches[0]) if matches else None


def _extract_firmware(ctx: BuildContext) -> None:
    """Copy the primary artifact, and the ESP32 factory artifact, to the build directory.

    The copies keep the names `firmware.<format>` and `firmware.factory.bin` that the download route reads.
    Raises `FileNotFoundError` when pio wrote no primary artifact.
    """
    fmt = ctx.variant.firmware_format
    pio_build_dir = ctx.settings.firmware_dir / ".pio" / "build" / ctx.variant.id

    source_path = _find_artifact(pio_build_dir, ctx.variant.id, fmt)
    if source_path is None:
        raise FileNotFoundError(f"Build completed but no firmware-{ctx.variant.id}-*.{fmt} found in {pio_build_dir}")

    firmware_filename = f"firmware.{fmt}"
    dest = ctx.build_dir / firmware_filename
    shutil.copy2(str(source_path), str(dest))
    ctx.firmware_path = dest
    logger.info(f"Build {ctx.build_id}: firmware copied to {dest}")

    if ctx.variant.has_factory_binary:
        factory_source = _find_artifact(pio_build_dir, ctx.variant.id, "factory.bin")
        if factory_source is not None:
            factory_dest = ctx.build_dir / "firmware.factory.bin"
            shutil.copy2(str(factory_source), str(factory_dest))
            ctx.factory_path = factory_dest
            logger.info(f"Build {ctx.build_id}: factory binary copied")


def generate_build_id() -> str:
    """Return a new build id of 32 hex characters."""
    return secrets.token_hex(16)
