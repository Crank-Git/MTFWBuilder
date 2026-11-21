"""Firmware builds: the build queue, the build workers and the progress stream.

A build runs in a build worker, so it continues when no progress stream is open.
The build keeps its build status and log lines in memory for the progress stream.
"""

import asyncio
import fnmatch
import glob
import hashlib
import hmac
import json
import logging
import multiprocessing
import os
import secrets
import shutil
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import aiosqlite

from mtfwbuilder.config import Settings
from mtfwbuilder.database import find_firmware_source, init_db, save_build
from mtfwbuilder.models import Artifact, BuildStatus
from mtfwbuilder.services.cleanup_service import scrub_firmware_source
from mtfwbuilder.services.registry_generator import flash_method
from mtfwbuilder.services.secret_mask import secret_masker

logger = logging.getLogger("mtfwbuilder.build")

FIRMWARE_SOURCE_MISSING = "Firmware source not installed. An admin must install a firmware version."
CANCELLED_MESSAGE = "Build cancelled."
ENDED_STATUSES = frozenset({"complete", "failed", "cancelled"})
# A copy of the firmware source records the firmware version that it holds in this file.
COPY_VERSION_FILE = ".worker-version"
# The progress number of each build status that pio output shows.
STEP_PROGRESS = {"compiling": 10, "linking": 70, "packaging": 90}


# The artifact file type of each flash method (FR-generated-registry-14).
FLASH_METHOD_FORMATS = {"web-serial": "bin", "uf2": "uf2", "dfu-zip": "zip", "download": "bin"}


@dataclass(frozen=True)
class DeviceVariant:
    """The registry entry of the variant that a build compiles: its env, display name, manufacturer and architecture."""

    id: str
    name: str
    manufacturer: str
    architecture: str

    @property
    def firmware_format(self) -> str:
        """Return the artifact file type of the architecture: `bin`, `uf2` or `zip`."""
        return FLASH_METHOD_FORMATS[flash_method(self.architecture)]


class BuildQueueFull(Exception):
    """The build queue holds `max_queue_size` builds."""


class BuildEnded(Exception):
    """The build has a build status of `complete`, `failed` or `cancelled`."""


class FirmwareVersionNotInstalled(Exception):
    """The build request names a firmware version that has no ready firmware source."""


@dataclass
class BuildContext:
    """All state for a single firmware build."""

    build_id: str
    variant: DeviceVariant
    config_content: str
    settings: Settings
    build_dir: Path = field(default_factory=Path)
    artifacts: list[Artifact] = field(default_factory=list)
    build_log: list[str] = field(default_factory=list)
    status: str = "queued"
    message: str = ""
    progress: int = 0
    error: str = ""
    download_url: str = ""
    created_at: float = field(default_factory=time.monotonic)
    task: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    firmware_version: str | None = None
    # The build worker that runs the build, so the build uses the firmware source of that build worker.
    worker: int = 0
    # The firmware source or the build worker copy that the build compiles in; `run_build` sets it when it starts.
    source_dir: Path | None = None
    # The build queue sets this to put each build status change in the `builds` table.
    on_change: Callable[["BuildContext"], None] = field(default=lambda _ctx: None, repr=False)
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
        self.on_change(self)
        self._notify()

    def add_log(self, line: str) -> str:
        """Return the line with each secret replaced by `***`, after it is stored and sent."""
        masked = self.mask(line)
        self.build_log.append(masked)
        self._notify()
        return masked

    def record(self) -> dict:
        """Return the `builds` table values of the build, without the build log and without the config."""
        primary = primary_artifact(self.artifacts)
        return {
            "id": self.build_id,
            "variant": self.variant.id,
            "architecture": self.variant.architecture,
            "firmware_format": self.variant.firmware_format,
            "status": self.status,
            "firmware_path": str(self.build_dir / primary.name) if primary else None,
            "error_message": self.error or None,
            # The config holds the secrets, so a hash without a key lets a reader test a guessed PSK.
            "config_hash": hmac.new(
                self.settings.secret_key.encode(), self.config_content.encode(), hashlib.sha256
            ).hexdigest(),
            "firmware_version": self.firmware_version,
        }

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


class FirmwareLock:
    """Let builds hold the firmware source together, and give a firmware update the firmware source alone.

    A build holds a shared hold with `shared()`. A firmware update holds the exclusive hold with
    `exclusive()`, or with `acquire()` and `release()`. The install and the removal of a firmware
    version are firmware updates. A build waits while a firmware update waits, so a stream of builds
    cannot delay an update for ever.
    """

    def __init__(self):
        self._builds = 0
        self._update = False
        self._updates_waiting = 0
        self._changed = asyncio.Event()

    def locked(self) -> bool:
        """Return True while a firmware update holds the exclusive hold."""
        return self._update

    @asynccontextmanager
    async def shared(self):
        """Hold the firmware source for one build, after no firmware update holds or waits for it."""
        await self._wait_until(lambda: not self._update and not self._updates_waiting)
        self._builds += 1
        try:
            yield
        finally:
            self._builds -= 1
            self._notify()

    async def acquire(self) -> None:
        """Take the exclusive hold, after each build releases its shared hold."""
        self._updates_waiting += 1
        try:
            await self._wait_until(lambda: not self._update and not self._builds)
        finally:
            self._updates_waiting -= 1
            # A cancelled update must wake the builds that wait behind it.
            self._notify()
        self._update = True

    def release(self) -> None:
        """Release the exclusive hold; a done callback calls this, so it does not wait."""
        self._update = False
        self._notify()

    @asynccontextmanager
    async def exclusive(self):
        """Hold the exclusive hold for the body of the `async with` block."""
        await self.acquire()
        try:
            yield
        finally:
            self.release()

    async def _wait_until(self, ready: Callable[[], bool]) -> None:
        while not ready():
            await self._changed.wait()

    def _notify(self) -> None:
        # A waiter holds the old event, so a set-and-replace wakes each waiter once.
        self._changed.set()
        self._changed = asyncio.Event()


def worker_firmware_source(source_dir: Path, worker: int) -> Path:
    """Return the directory that build worker `worker` compiles in, for the firmware source `source_dir`.

    Build worker 0 compiles in `source_dir` itself. Each other build worker compiles in a copy at
    `<source_dir>/.worker-<worker>/`. So each copy is in the directory of its firmware version, and a build of
    one firmware version never compiles in a copy of another firmware version. The function makes the copy on
    first use. A copy without its version file is incomplete, so the function makes it again.
    """
    if worker == 0:
        return source_dir
    copy = source_dir / f".worker-{worker}"
    # A copy that the startup import moved from the old layout holds another text, so the function makes it again.
    version = source_dir.name
    version_file = copy / COPY_VERSION_FILE
    if version_file.exists() and version_file.read_text() == version:
        return copy

    def running_build_files(directory: str, names: list[str]) -> set[str]:
        # Build worker 0 can build in the source during the copy, so its userPrefs file and object files hold secrets.
        if Path(directory) == source_dir:
            return {n for n in names if n == "userPrefs.jsonc" or n.startswith(".worker-")}
        if Path(directory) == source_dir / ".pio":
            return {n for n in names if n in ("build", "build_cache")}
        return set()

    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(source_dir, copy, symlinks=True, ignore=running_build_files)
    version_file.write_text(version)
    logger.info(f"Copied the firmware source {version} for build worker {worker}")
    return copy


class BuildQueue:
    """Hold the builds of this process and run them in build workers, in queue order."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._queue: asyncio.Queue[BuildContext] = asyncio.Queue(maxsize=settings.max_queue_size)
        self._builds: dict[str, BuildContext] = {}
        self._workers: list[asyncio.Task] = []
        # Each record is a copy taken at the change, so a later change cannot alter what is written.
        self._records: asyncio.Queue[dict | None] = asyncio.Queue()
        self._recorder: asyncio.Task | None = None
        # Each build holds a shared hold, and a firmware update holds the exclusive hold while it replaces the source.
        self.firmware_lock = FirmwareLock()

    def get(self, build_id: str) -> BuildContext | None:
        return self._builds.get(build_id)

    async def submit(
        self, variant: DeviceVariant, config_content: str, firmware_version: str | None = None
    ) -> BuildContext:
        """Return a new build of `firmware_version`, after it is put in the build queue.

        When `firmware_version` is None, the build uses the newest installed stable firmware version.

        Raises:
        - `FirmwareVersionNotInstalled` when `firmware_version` has no ready firmware source.
        - `BuildQueueFull` when the build queue holds `max_queue_size` builds.

        A default build with no firmware source fails at once and does not go in the build queue.
        """
        # The read is before the queue check, so no other submit can fill the build queue between the two.
        source = await installed_firmware_source(self.settings, firmware_version)
        if source is None and firmware_version is not None:
            raise FirmwareVersionNotInstalled(firmware_version)
        if self._queue.full():
            raise BuildQueueFull
        self._forget_old_builds()
        # A removal refuses a firmware version that a queued build uses, so the build holds its version from submit.
        ctx = BuildContext(
            generate_build_id(),
            variant,
            config_content,
            self.settings,
            firmware_version=source[0] if source else None,
            on_change=self._record,
        )
        self._builds[ctx.build_id] = ctx
        self._record(ctx)
        if source is not None:
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
        if ctx.task is None:
            # A queued build has written nothing to the firmware source, so it ends at once.
            ctx.set_status("cancelled", message=CANCELLED_MESSAGE)
        else:
            # The build worker sends `cancelled` after the build task scrubs the firmware source.
            ctx.task.cancel()
        return ctx

    def start(self) -> None:
        """Start `build_workers` build workers in the running event loop."""
        self._workers = [asyncio.create_task(self._work(worker)) for worker in range(self.settings.build_workers)]
        self._recorder = asyncio.create_task(self._write_records())

    async def stop(self) -> None:
        """Stop the build workers, and cancel the builds that run."""
        tasks = self._workers + [c.task for c in self._builds.values() if c.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._workers = []
        if self._recorder is not None:
            # The recorder writes the records it holds, then ends; an error in a write raises here.
            self._records.put_nowait(None)
            recorder, self._recorder = self._recorder, None
            await recorder

    def unended_build_ids(self) -> set[str]:
        """Return the build ids of the builds that have not ended."""
        return {build_id for build_id, ctx in self._builds.items() if not ctx.ended}

    def unended_firmware_versions(self, default_version: str | None) -> set[str]:
        """Return the firmware versions that the builds that have not ended use.

        A build without a firmware version counts as a build of `default_version`.
        """
        versions = {ctx.firmware_version or default_version for ctx in self._builds.values() if not ctx.ended}
        return versions - {None}

    def _record(self, ctx: BuildContext) -> None:
        self._records.put_nowait(ctx.record())

    async def _write_records(self) -> None:
        # A test app has no lifespan, so the recorder creates the tables itself.
        await init_db(self.settings)
        async with aiosqlite.connect(self.settings.database_path) as db:
            while (record := await self._records.get()) is not None:
                await save_build(db, record)

    async def _work(self, worker: int) -> None:
        while True:
            ctx = await self._queue.get()
            ctx.worker = worker
            async with self.firmware_lock.shared():
                await self._run(ctx)
            self._queue.task_done()

    async def _run(self, ctx: BuildContext) -> None:
        # A build cancelled while it waits stays in the queue until a build worker takes it.
        if ctx.ended:
            return
        ctx.task = asyncio.create_task(run_build(ctx))
        # `wait` does not raise when the build task is cancelled, so the build worker continues.
        await asyncio.wait({ctx.task})
        if ctx.task.cancelled():
            # Only `cancel` cancels the build task alone; `stop` also cancels this build worker, so it never gets here.
            ctx.set_status("cancelled", message=CANCELLED_MESSAGE)
        elif ctx.task.exception() is not None:
            # Without an ended build status, the progress stream of the build never ends.
            logger.error(f"Build {ctx.build_id} raised an error", exc_info=ctx.task.exception())
            ctx.set_status("failed", error="The build stopped because of an internal error.")

    def _forget_old_builds(self) -> None:
        oldest = time.monotonic() - self.settings.build_max_age_seconds
        for build_id in [k for k, c in self._builds.items() if c.ended and c.created_at < oldest]:
            del self._builds[build_id]


async def progress_events(ctx: BuildContext, log_from: int | None = None):
    """Yield the SSE events of the progress stream for one build.

    The first event is the current build status. Then the stream sends each change of the
    build status and each new log line. The stream ends after the build ends. A closed
    stream has no effect on the build.

    `log_from` is the index of the first log line to send. Without it, the stream sends only
    the lines that come after the connect.
    """
    sent_status = None
    log_index = len(ctx.build_log) if log_from is None else log_from
    while True:
        # Hold the event before the read, so a change during a yield is not lost.
        changed = ctx.changed
        status = ctx.status_event()
        if status != sent_status:
            sent_status = status
            # A page closes the progress stream on `complete`, so the artifact list goes first.
            if ctx.status == "complete":
                yield {"event": "artifacts", "data": json.dumps([a.model_dump() for a in ctx.artifacts])}
            yield {"event": "status", "data": status}
        for line in ctx.build_log[log_index:]:
            yield {"event": "log", "data": json.dumps({"line": line})}
        log_index = len(ctx.build_log)
        if ctx.ended:
            return
        await changed.wait()


async def installed_firmware_source(settings: Settings, version: str | None = None) -> tuple[str, Path] | None:
    """Return the firmware version and the directory of the firmware source of `version`.

    When `version` is None, the result is the default firmware source: the newest installed stable one.
    Returns None when no ready firmware source matches, or when the directory of its record is missing.
    """
    record = await find_firmware_source(settings.database_path, version)
    if record is None or not Path(record["path"]).is_dir():
        return None
    return record["version"], Path(record["path"])


async def run_build(ctx: BuildContext) -> None:
    """Run one build to an ended build status, and scrub the firmware source before the end event."""
    # The directory of the firmware source can be gone when the build starts, so the build reads the record again.
    source = await installed_firmware_source(ctx.settings, ctx.firmware_version)
    if source is None:
        ctx.set_status("failed", error=FIRMWARE_SOURCE_MISSING)
        return
    ctx.firmware_version, source_dir = source
    # The copy holds no secret yet, so it stays outside the scrub.
    ctx.source_dir = await asyncio.to_thread(worker_firmware_source, source_dir, ctx.worker)
    ctx.set_status("compiling", message=f"Building firmware for {ctx.variant.name}...")
    try:
        status, fields = await _compile(ctx)
    finally:
        # A cancel arrives as `CancelledError`, and an internal error as any exception, so both pass here.
        scrub_firmware_source(ctx.source_dir, ctx.variant.id)
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
        _collect_artifacts(ctx)
    except TimeoutError:
        error_msg = f"Build timed out after {ctx.settings.build_timeout_seconds // 60} minutes"
        logger.error(f"Build {ctx.build_id}: {error_msg}")
        return "failed", {"error": error_msg}
    except OSError as e:
        # A missing pio executable, a missing artifact and a full disk all raise OSError.
        logger.error(f"Build {ctx.build_id} failed: {e}")
        return "failed", {"error": str(e)}
    download_url = artifact_url(ctx.build_id, primary_artifact(ctx.artifacts).name)
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
    firmware_dir = ctx.source_dir
    # Upstream `bin/platformio-custom.py` reads only `<PROJECT_DIR>/userPrefs.jsonc`.
    (firmware_dir / "userPrefs.jsonc").write_text(ctx.config_content)

    logger.info(f"Build {ctx.build_id}: config written, starting PIO for {ctx.variant.id}")

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
            if status is not None and status != ctx.status:
                ctx.set_status(status, message=line[:200])
        return await process.wait()
    except asyncio.CancelledError:
        # A cancel or a timeout must not leave pio to write into the firmware source.
        process.kill()
        await process.wait()
        raise


def _parse_progress(line: str) -> str | None:
    """Return the build status that a pio output line shows, or None for a line that shows no step.

    Only the exit code of pio gives `complete` or `failed`, so one build sends one end event.
    """
    lower = line.lower()
    if "compiling" in lower or "building .pio" in lower:
        return "compiling"
    if "linking .pio" in lower:
        return "linking"
    if "checking size" in lower or "building firmware image" in lower:
        return "packaging"
    return None


# The artifact kind for each upstream file name pattern; the first match decides the kind.
# Upstream `bin/platformio-pre.py` sets `PROGNAME = firmware-<env>-<version>` (meshtastic/firmware PR #8248).
ARTIFACT_PATTERNS = (
    ("firmware-{env}-*.factory.bin", "factory"),
    ("firmware-{env}-*.bin", "app"),
    ("firmware-{env}-*.uf2", "uf2"),
    ("firmware-{env}-*.hex", "hex"),
    ("firmware-{env}-*.zip", "ota-zip"),
    ("littlefs-{env}-*.bin", "littlefs"),
    ("*.mt.json", "manifest"),
)
# The ESP32 factory artifact is the preferred download, because it does a full install.
PRIMARY_KINDS = ("factory", "uf2", "app", "hex")


def artifact_kind(name: str, env_name: str) -> str | None:
    """Return the artifact kind of the file `name` in `.pio/build/<env_name>/`, or None for another file."""
    env = glob.escape(env_name)
    for pattern, kind in ARTIFACT_PATTERNS:
        if fnmatch.fnmatchcase(name, pattern.format(env=env)):
            return kind
    return None


def primary_artifact(artifacts: list[Artifact]) -> Artifact | None:
    """Return the artifact that a single download sends, or None when the build has no firmware artifact."""
    for kind in PRIMARY_KINDS:
        for artifact in artifacts:
            if artifact.kind == kind:
                return artifact
    return None


def artifact_url(build_id: str, name: str) -> str:
    return f"/api/v1/builds/{build_id}/artifacts/{quote(name)}"


def _collect_artifacts(ctx: BuildContext) -> None:
    """Copy each upstream artifact of the build to the build directory, and set `ctx.artifacts`.

    Raises `FileNotFoundError` when pio wrote no firmware artifact.
    """
    pio_build_dir = ctx.source_dir / ".pio" / "build" / ctx.variant.id
    artifacts = []
    for path in sorted(pio_build_dir.iterdir()):
        kind = artifact_kind(path.name, ctx.variant.id)
        if kind is None or not path.is_file():
            continue
        shutil.copy2(path, ctx.build_dir / path.name)
        artifacts.append(Artifact(name=path.name, size=path.stat().st_size, kind=kind))
    if primary_artifact(artifacts) is None:
        raise FileNotFoundError(f"Build completed but no firmware-{ctx.variant.id}-* artifact found in {pio_build_dir}")
    ctx.artifacts = artifacts
    logger.info(f"Build {ctx.build_id}: {len(artifacts)} artifacts copied to {ctx.build_dir}")


def generate_build_id() -> str:
    """Return a new build id of 32 hex characters."""
    return secrets.token_hex(16)
