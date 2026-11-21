"""Fleet runs: one build per fleet device, fed to the build queue one at a time (feature fleet-builds, FR 15 to 18).

A fleet run holds its "must re-enter" secrets and the userPrefs file of each build in memory only. The process loses
them on a restart, so an unfinished fleet run then shows `partial`, and Retry failed needs the secrets again.
The process also drops the held secrets when the fleet run ends `complete`, ends by an internal error, or ended
`build_max_age_seconds` ago, and when its fleet is deleted.

A route names a fleet run by its random public id. The integer row id stays inside the database and this process.
"""

import asyncio
import csv
import dataclasses
import io
import logging
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import aiosqlite
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from mtfwbuilder.config import Settings
from mtfwbuilder.database import list_variants
from mtfwbuilder.models import ConfigRequest
from mtfwbuilder.routers.config_generator import field_error, generate_for_version
from mtfwbuilder.services import fleets, profiles
from mtfwbuilder.services.build_service import (
    BuildContext,
    BuildQueue,
    BuildQueueFull,
    DeviceVariant,
    FirmwareVersionNotInstalled,
    generate_build_id,
    installed_firmware_source,
)
from mtfwbuilder.services.userprefs_keys import KeyScan, find_key_scan

logger = logging.getLogger("mtfwbuilder.fleet_runs")

# The `ConfigRequest` field that each fleet device field overrides. The generator writes the userPrefs keys.
OVERRIDES = {
    "long_name": "owner_long_name",
    "short_name": "owner_short_name",
    "role": "device_role",
    "lat": "fixed_lat",
    "lon": "fixed_lon",
    "alt": "fixed_alt",
    "admin_key": "admin_key_0",
}
# The generator writes `USERPREFS_FIXED_GPS_*` only when `gps_enabled` and `fixed_position` are both true.
FIXED_POSITION_FIELDS = ("lat", "lon", "alt")
# With `gps_enabled`, the generator also writes these fields. A profile with GPS off must not get them from a position.
GPS_ONLY_FIELDS = ("gps_mode", "gps_update_interval", "position_broadcast_interval", "smart_position_enabled")
# A fleet device without a build in the fleet run waits for the fleet run to give it to the build queue.
NOT_SUBMITTED = "queued"
UNREADABLE_ADMIN_KEY = "the admin key does not decrypt with the current secret_key, so enter it again"
FLEET_RUNNING = "The fleet has a running fleet run."
# Single builds can fill the build queue during a fleet run, so the next build of the fleet run waits this long.
QUEUE_FULL_WAIT_SECONDS = 1.0

DEVICE_ROWS = """
SELECT frb.fleet_device_id, frb.build_id, fd.label, fd.variant, b.variant AS build_variant, b.status,
       b.error_message
FROM fleet_run_builds frb
LEFT JOIN fleet_devices fd ON fd.id = frb.fleet_device_id
LEFT JOIN builds b ON b.id = frb.build_id
WHERE frb.fleet_run_id = ?
ORDER BY fd.position, frb.fleet_device_id
"""
# The zip has a folder for each fleet device label, so a label never makes a path outside its folder.
UNSAFE_FOLDER_CHARACTERS = re.compile(r"[^\w.-]")
# Windows opens a device, not a folder, for these names with any extension.
WINDOWS_RESERVED_NAME = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?", re.IGNORECASE)
# A spreadsheet runs a cell that starts with one of these as a formula, so the manifest puts a quote before it.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
MANIFEST_COLUMNS = (
    "label",
    "folder",
    "variant",
    "firmware_version",
    "build_id",
    "status",
    "artifact_names",
    "config_hash",
)
ZIP_CHUNK_BYTES = 1024 * 1024


class FleetNotFound(Exception):
    """No fleet has the id."""


class FleetRunNotFound(Exception):
    """No fleet run has the id."""


class FleetRunConflict(Exception):
    """The fleet run cannot retry now. The message names the reason."""


@dataclass(frozen=True)
class _Job:
    """One build of a fleet run. `config_content` can hold secrets, so a job stays in memory."""

    fleet_device_id: int
    variant: DeviceVariant
    config_content: str


@dataclass
class _Held:
    """The "must re-enter" secrets of one fleet run, held for Retry failed until `expiry` drops them."""

    fleet_id: int
    secrets: dict[str, str] = dataclasses.field(repr=False)
    expiry: asyncio.TimerHandle | None = None


def _invalid(*errors: tuple[tuple, str]) -> RequestValidationError:
    return RequestValidationError([field_error(location, message) for location, message in errors])


def _device_fields(base: dict[str, Any], device: dict[str, Any]) -> dict[str, Any]:
    """Return the config form fields of one fleet device: the profile fields with the fleet device overrides."""
    fields = dict(base)
    for name, field in OVERRIDES.items():
        if device[name] is not None:
            fields[field] = str(device[name])
    if any(device[name] is not None for name in FIXED_POSITION_FIELDS):
        if base.get("gps_enabled") != "true":
            for name in GPS_ONLY_FIELDS:
                fields.pop(name, None)
        fields.update(gps_enabled="true", fixed_position="true")
    return fields


def _config_errors(fields: dict[str, Any], scan: KeyScan | None) -> tuple[str | None, list[tuple[tuple, str]]]:
    """Return the userPrefs file of `fields`, or None and the errors of `ConfigRequest` and the firmware version."""
    try:
        return generate_for_version(ConfigRequest.model_validate(fields), scan), []
    except ValidationError as e:
        return None, [(tuple(error["loc"]), error["msg"]) for error in e.errors(include_url=False)]
    except RequestValidationError as e:
        # `generate_for_version` gives each location with "body" first.
        return None, [(tuple(error["loc"][1:]), error["msg"]) for error in e.errors()]


def _job(
    device: dict[str, Any], base: dict[str, Any], variants: dict[str, dict], scan: KeyScan | None, version: str
) -> tuple[_Job | None, list[tuple[tuple, str]]]:
    """Return the job of one fleet device, or None and the errors with locations inside the fleet device."""
    fields = _device_fields(base, device)
    # A profile can hold the variant of its config, and the fleet device variant overrides it.
    profile_variant = fields.pop("variant", None)
    entry = variants.get(device["variant"] or profile_variant or "")
    errors = [] if entry else [(("variant",), f"Variant not in {version}")]
    if device["has_admin_key"] and device["admin_key"] is None:
        errors.append((("admin_key",), UNREADABLE_ADMIN_KEY))
    content, config_errors = _config_errors(fields, scan)
    errors += config_errors
    if errors:
        return None, errors
    variant = DeviceVariant(
        id=entry["env"],
        name=entry["display_name"],
        manufacturer=entry["manufacturer"],
        architecture=entry["architecture"],
    )
    return _Job(device["id"], variant, content), []


def _check_secrets(must_reenter: list[str], entered: dict[str, str]) -> None:
    """Raise 422 errors that name each missing "must re-enter" secret and each name that is not one of them."""
    errors = [
        (("secrets", name), "enter this secret for the fleet run") for name in must_reenter if not entered.get(name)
    ]
    errors += [
        (("secrets", name), "the profile does not ask for this secret") for name in entered if name not in must_reenter
    ]
    if errors:
        raise _invalid(*errors)


async def _base_fields(
    database_path: Path, profile_id: int, profile_secret_key: str | None, entered: dict[str, str]
) -> dict[str, Any]:
    """Return the config form fields of the profile with the "must re-enter" secrets of `entered`.

    `profile_secret_key` None gives the profile without its stored secrets, so each one is "must re-enter".
    Raises a 422 `RequestValidationError` for a missing profile, a missing secret or a secret that is not asked for.
    """
    profile = await profiles.get_profile(database_path, profile_id, profile_secret_key)
    if profile is None:
        raise _invalid((("profile_id",), f"profile {profile_id} does not exist"))
    _check_secrets(profile["must_reenter"], entered)
    return {**profile["config"], **entered}


async def _jobs(
    database_path: Path, fleet: dict[str, Any], base: dict[str, Any], version: str, device_ids: set[int] | None = None
) -> list[_Job]:
    """Return one job for each fleet device in `device_ids`, or for each fleet device when it is None.

    `base` holds the config form fields of the profile. Raises a 422 `RequestValidationError` that names each
    error before a build starts (FR-fleet-builds-13).
    """
    scan = await find_key_scan(database_path, version)
    variants = {entry["env"]: entry for entry in await list_variants(database_path, version)}
    jobs, errors = [], []
    for index, device in enumerate(fleet["devices"]):
        if device_ids is not None and device["id"] not in device_ids:
            continue
        job, device_errors = _job(device, base, variants, scan, version)
        jobs.append(job)
        errors += [(("devices", index, *location), message) for location, message in device_errors]
    if errors:
        raise _invalid(*errors)
    if not jobs:
        raise _invalid((("devices",), "the fleet holds no fleet device"))
    return jobs


async def _firmware_version(settings: Settings, version: str | None) -> str:
    """Return the installed firmware version of `version`, or of the default firmware source when it is None."""
    source = await installed_firmware_source(settings, version)
    if source is None:
        message = f"firmware version {version} is not installed" if version else "no firmware version is installed"
        raise _invalid((("firmware_version",), message))
    return source[0]


def _ended_status(statuses: list[str]) -> str:
    """Return the status of an ended fleet run from the build status of each fleet device (FR-fleet-builds-17).

    The status is `complete` when every build is complete, `partial` when one or more is, and `failed` when none is.
    """
    if all(status == "complete" for status in statuses):
        return "complete"
    return "partial" if "complete" in statuses else "failed"


def _stopped_status(statuses: list[str]) -> str:
    """Return the status of a fleet run that its row shows as running, but that no process runs.

    The process stopped during the fleet run. The status is `partial`, so Retry failed continues it. When every
    fleet device has a complete build, the process stopped after the last build and the status is `complete`.
    """
    return "complete" if statuses and all(status == "complete" for status in statuses) else "partial"


async def _wait_until_ended(ctx: BuildContext) -> None:
    while True:
        # Hold the event before the read, so a change between the read and the wait is not lost.
        changed = ctx.changed
        if ctx.ended:
            return
        await changed.wait()


def _folder(label: str, used: set[str]) -> str:
    """Return a zip folder name for `label` that `used` does not hold, and add it to `used`.

    A folder name starts with no dot, so the folder is not hidden and no `..` path leaves the zip. Windows removes
    a trailing dot, opens a device for a reserved name, and ignores case, so the name is unique there too.
    """
    name = UNSAFE_FOLDER_CHARACTERS.sub("_", label).rstrip(".")
    if not name or name.startswith(".") or WINDOWS_RESERVED_NAME.fullmatch(name):
        name = f"_{name}"
    while name.casefold() in used:
        name = f"{name}_"
    used.add(name.casefold())
    return name


def _manifest_cell(value: str) -> str:
    """Return the manifest cell of `value`, with a quote before a value that a spreadsheet runs as a formula."""
    return f"'{value}" if value.startswith(FORMULA_STARTS) else value


async def _build_records(database_path: Path, build_ids: list[str]) -> dict[str, aiosqlite.Row]:
    """Return the `builds` row of each build id, so an artifact is found after the build queue forgets its build."""
    async with aiosqlite.connect(database_path) as db:
        db.row_factory = aiosqlite.Row
        placeholders = ", ".join("?" for _ in build_ids)
        cursor = await db.execute(
            f"SELECT id, config_hash, firmware_path FROM builds WHERE id IN ({placeholders})", build_ids
        )
        return {row["id"]: row for row in await cursor.fetchall()}


def _write_zip(file: IO[bytes], entries: list[tuple[dict[str, str], list[Path]]]) -> None:
    """Write the artifacts of each entry in its folder, and `manifest.csv` with the row of each entry."""
    manifest = io.StringIO()
    writer = csv.DictWriter(manifest, MANIFEST_COLUMNS, lineterminator="\n")
    writer.writeheader()
    with zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as archive:
        for row, paths in entries:
            for path in paths:
                archive.write(path, f"{row['folder']}/{path.name}")
            writer.writerow({name: _manifest_cell(value) for name, value in row.items()})
        archive.writestr("manifest.csv", manifest.getvalue())


class FleetRuns:
    """Run the fleet runs of this process, and hold their "must re-enter" secrets in memory."""

    def __init__(self, settings: Settings, build_queue: BuildQueue):
        self.settings = settings
        self.build_queue = build_queue
        # Each key is the row id of a fleet run. A route gives the public id, and `_load` finds the row id.
        self._tasks: dict[int, asyncio.Task] = {}
        self._task_fleets: dict[int, int] = {}
        self._held: dict[int, _Held] = {}
        # A start and a retry read the state of the fleet runs before they launch one, so only one runs at a time.
        self._lock = asyncio.Lock()

    def running(self, run_id: int) -> bool:
        """Return True while this process feeds the builds of the fleet run with the row id to the build queue."""
        return run_id in self._tasks

    def runs_fleet(self, fleet_id: int) -> bool:
        """Return True while this process runs a fleet run of the fleet."""
        return fleet_id in self._task_fleets.values()

    def forget_fleet(self, fleet_id: int) -> None:
        """Drop the held secrets of each fleet run of the fleet, because a fleet delete removes its fleet runs."""
        for run_id in [run_id for run_id, held in self._held.items() if held.fleet_id == fleet_id]:
            self._forget(run_id)

    async def start(self, fleet_id: int, profile_secret_key: str | None, entered: dict[str, str]) -> str:
        """Return the public id of a new fleet run of the fleet, after its first build goes to the build queue.

        `entered` holds the "must re-enter" secrets of the profile. `profile_secret_key` None gives the profile
        without its stored secrets. The server decrypts the admin key of each fleet device for the build only.

        Raises:
        - `FleetNotFound` when no fleet has the id.
        - `FleetRunConflict` when a fleet run of the fleet runs.
        - `RequestValidationError` with each error of the fleet, the profile or the secrets.
        - `BuildQueueFull` when the build queue holds `max_queue_size` builds.
        """
        async with self._lock:
            fleet = await fleets.get_fleet(self.settings.database_path, fleet_id, self.settings.secret_key)
            if fleet is None:
                raise FleetNotFound(fleet_id)
            if self.runs_fleet(fleet_id):
                raise FleetRunConflict(FLEET_RUNNING)
            version = await _firmware_version(self.settings, fleet["firmware_version"])
            base = await _base_fields(self.settings.database_path, fleet["profile_id"], profile_secret_key, entered)
            jobs = await _jobs(self.settings.database_path, fleet, base, version)
            if self.build_queue.is_full():
                raise BuildQueueFull
            public_id = generate_build_id()
            async with aiosqlite.connect(self.settings.database_path) as db:
                cursor = await db.execute(
                    "INSERT INTO fleet_runs (public_id, fleet_id, profile_id, firmware_version, status) "
                    "VALUES (?, ?, ?, ?, 'running')",
                    (public_id, fleet_id, fleet["profile_id"], version),
                )
                run_id = cursor.lastrowid
                await db.executemany(
                    "INSERT INTO fleet_run_builds (fleet_run_id, fleet_device_id) VALUES (?, ?)",
                    [(run_id, job.fleet_device_id) for job in jobs],
                )
                await db.commit()
            self._launch(run_id, fleet_id, version, jobs, entered)
            return public_id

    async def retry(self, public_id: str, profile_secret_key: str | None, entered: dict[str, str]) -> None:
        """Give each fleet device of the fleet run without a complete build to the build queue again (FR 18).

        The retry uses the profile of the fleet run. The secrets are the held secrets, with `entered` over them.

        Raises:
        - `FleetRunNotFound` when no fleet run has the public id.
        - `FleetRunConflict` in these cases:
          - a fleet run of the fleet runs.
          - the fleet run has no failed fleet device.
          - the fleet devices of the fleet run changed.
        - `RequestValidationError` with each error of the fleet, the profile or the secrets.
        - `BuildQueueFull` when the build queue holds `max_queue_size` builds.
        """
        async with self._lock:
            loaded = await self._load("public_id", public_id)
            if loaded is None:
                raise FleetRunNotFound(public_id)
            run_id, run, profile_id = loaded
            retry_ids = self._retry_ids(run_id, run)
            fleet = await fleets.get_fleet(self.settings.database_path, run["fleet_id"], self.settings.secret_key)
            # A removed fleet device has no row, so the fleet run cannot build it again.
            if retry_ids - {device["id"] for device in fleet["devices"]}:
                raise FleetRunConflict("The fleet devices changed after this fleet run. Start a new fleet run.")
            version = await _firmware_version(self.settings, run["firmware_version"])
            held = self._held.get(run_id)
            secrets = {**(held.secrets if held else {}), **entered}
            # A fleet run from before the `profile_id` column uses the profile of its fleet.
            profile_id = fleet["profile_id"] if profile_id is None else profile_id
            base = await _base_fields(self.settings.database_path, profile_id, profile_secret_key, secrets)
            jobs = await _jobs(self.settings.database_path, fleet, base, version, retry_ids)
            if self.build_queue.is_full():
                raise BuildQueueFull
            self._launch(run_id, run["fleet_id"], version, jobs, secrets)

    def _retry_ids(self, run_id: int, run: dict[str, Any]) -> set[int]:
        if self.running(run_id):
            raise FleetRunConflict("The fleet run is running.")
        if self.runs_fleet(run["fleet_id"]):
            raise FleetRunConflict(FLEET_RUNNING)
        retry_ids = {device["fleet_device_id"] for device in run["devices"] if device["status"] != "complete"}
        if not retry_ids:
            raise FleetRunConflict("The fleet run has no failed fleet device.")
        return retry_ids

    async def get(self, public_id: str) -> dict[str, Any] | None:
        """Return the fleet run with the build status of each fleet device, or None when no fleet run has the id.

        A fleet run that its row shows as running, but that this process does not run, stopped with the process.
        It shows `partial`, so Retry failed continues it, or `complete` when each fleet device has a complete build.
        """
        loaded = await self._load("public_id", public_id)
        return loaded[1] if loaded else None

    async def _load(self, column: str, value: str | int) -> tuple[int, dict[str, Any], int | None] | None:
        """Return the row id, the fleet run and the profile id of the fleet run, or None when no fleet run matches.

        `column` is `public_id` for the id that a route gives, or `id` for the row id of this process.
        """
        async with aiosqlite.connect(self.settings.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(f"SELECT * FROM fleet_runs WHERE {column} = ?", (value,))
            row = await cursor.fetchone()
            if row is None:
                return None
            cursor = await db.execute(DEVICE_ROWS, (row["id"],))
            devices = [self._device(device) for device in await cursor.fetchall()]
        status = row["status"]
        if self.running(row["id"]):
            status = "running"
        elif status == "running":
            status = _stopped_status([device["status"] for device in devices])
        run = {
            "id": row["public_id"],
            "fleet_id": row["fleet_id"],
            "firmware_version": row["firmware_version"],
            "status": status,
            "created_at": row["created_at"],
            "devices": devices,
        }
        return row["id"], run, row["profile_id"]

    def _device(self, row: aiosqlite.Row) -> dict[str, Any]:
        # The build queue holds the current build status, and the `builds` row gets each change after it.
        ctx = self.build_queue.get(row["build_id"]) if row["build_id"] else None
        if ctx is not None:
            variant, status, error = ctx.variant.id, ctx.status, ctx.error or None
        else:
            variant, status, error = row["build_variant"] or row["variant"], row["status"], row["error_message"]
        return {
            "fleet_device_id": row["fleet_device_id"],
            "label": row["label"],
            "variant": variant,
            "build_id": row["build_id"],
            "status": status or NOT_SUBMITTED,
            "error": error,
        }

    def _launch(self, run_id: int, fleet_id: int, version: str, jobs: list[_Job], secrets: dict[str, str]) -> None:
        self._forget(run_id)
        if secrets:
            self._held[run_id] = _Held(fleet_id, secrets)
        task = asyncio.create_task(self._feed(run_id, version, jobs))
        self._tasks[run_id] = task
        self._task_fleets[run_id] = fleet_id
        task.add_done_callback(lambda done: self._ended(run_id, done))

    def _forget(self, run_id: int) -> None:
        held = self._held.pop(run_id, None)
        if held is not None and held.expiry is not None:
            held.expiry.cancel()

    def _ended(self, run_id: int, task: asyncio.Task) -> None:
        self._tasks.pop(run_id, None)
        self._task_fleets.pop(run_id, None)
        if not task.cancelled() and task.exception() is not None:
            # The row still shows `running`, so the fleet run shows `partial` and Retry failed continues it.
            logger.error(f"Fleet run {run_id} stopped because of an internal error", exc_info=task.exception())
            self._forget(run_id)
        elif (held := self._held.get(run_id)) is not None:
            # A held secret is for Retry failed only, so it stays no longer than the builds of the fleet run.
            held.expiry = asyncio.get_running_loop().call_later(
                self.settings.build_max_age_seconds, self._forget, run_id
            )

    async def _submit(self, run_id: int, version: str, job: _Job) -> BuildContext:
        """Return the build of the job, after a wait while single builds fill the build queue."""
        while True:
            try:
                return await self.build_queue.submit(job.variant, job.config_content, version, fleet_run_id=run_id)
            except BuildQueueFull:
                await asyncio.sleep(QUEUE_FULL_WAIT_SECONDS)

    async def _feed(self, run_id: int, version: str, jobs: list[_Job]) -> None:
        """Give each job to the build queue after the build of the previous job ends (FR-fleet-builds-15)."""
        database_path = self.settings.database_path
        async with aiosqlite.connect(database_path) as db:
            # A retried fleet device shows `queued` until its new build starts.
            await db.execute("UPDATE fleet_runs SET status = 'running' WHERE id = ?", (run_id,))
            await db.executemany(
                "UPDATE fleet_run_builds SET build_id = NULL WHERE fleet_run_id = ? AND fleet_device_id = ?",
                [(run_id, job.fleet_device_id) for job in jobs],
            )
            await db.commit()
        for job in jobs:
            try:
                ctx = await self._submit(run_id, version, job)
            except FirmwareVersionNotInstalled:
                logger.warning(f"Fleet run {run_id} stops, because firmware version {version} is not installed")
                break
            async with aiosqlite.connect(database_path) as db:
                await db.execute(
                    "UPDATE fleet_run_builds SET build_id = ? WHERE fleet_run_id = ? AND fleet_device_id = ?",
                    (ctx.build_id, run_id, job.fleet_device_id),
                )
                await db.commit()
            # A failed build does not stop the fleet run (FR-fleet-builds-17).
            await _wait_until_ended(ctx)
        _, run, _ = await self._load("id", run_id)
        status = _ended_status([device["status"] for device in run["devices"]])
        async with aiosqlite.connect(database_path) as db:
            await db.execute("UPDATE fleet_runs SET status = ? WHERE id = ?", (status, run_id))
            await db.commit()
        if status == "complete":
            # A complete fleet run has nothing to retry, so the process keeps its secrets no longer.
            self._forget(run_id)
        logger.info(f"Fleet run {run_id} ended {status}")

    async def archive(self, public_id: str) -> IO[bytes]:
        """Return a temporary file that holds the zip of the fleet run, read from its start (FR-fleet-builds-19, -20).

        The zip has a folder with the artifacts of each fleet device with a complete build, and `manifest.csv`.
        `manifest.csv` has one row for each fleet device. It holds no value of a config, so it holds no secret.
        The file is in `temp_dir`, so the zip never holds all the artifacts in memory. A close deletes the file.

        Raises:
        - `FleetRunNotFound` when no fleet run has the public id.
        - `FleetRunConflict` while the fleet run runs.
        """
        run = await self.get(public_id)
        if run is None:
            raise FleetRunNotFound(public_id)
        if run["status"] == "running":
            raise FleetRunConflict("The fleet run is running.")
        build_ids = [device["build_id"] for device in run["devices"] if device["build_id"]]
        records = await _build_records(self.settings.database_path, build_ids)
        used: set[str] = set()
        entries = [self._zip_entry(run, device, records.get(device["build_id"]), used) for device in run["devices"]]
        self.settings.temp_dir.mkdir(parents=True, exist_ok=True)
        # The response sends the file after this call returns, so the caller closes it.
        file = tempfile.TemporaryFile(dir=self.settings.temp_dir)  # noqa: SIM115
        try:
            await asyncio.to_thread(_write_zip, file, entries)
        except BaseException:
            # The caller gets no file to close, so the half-written zip is removed here.
            file.close()
            raise
        file.seek(0)
        return file

    def _zip_entry(
        self, run: dict[str, Any], device: dict[str, Any], record: aiosqlite.Row | None, used: set[str]
    ) -> tuple[dict[str, str], list[Path]]:
        """Return the manifest row and the artifact paths of one fleet device of the fleet run."""
        ctx = self.build_queue.get(device["build_id"]) if device["build_id"] else None
        paths, config_hash = [], record["config_hash"] if record else None
        if ctx is not None:
            paths, config_hash = [ctx.build_dir / artifact.name for artifact in ctx.artifacts], ctx.config_hash
        elif record is not None and record["firmware_path"]:
            # After a restart, the `builds` row names only the primary artifact of the build.
            paths = [Path(record["firmware_path"])]
        # The cleanup removes the build directory of an old build, so a missing artifact stays out of the zip.
        paths = [path for path in paths if path.is_file()] if device["status"] == "complete" else []
        label = device["label"] or f"fleet-device-{device['fleet_device_id']}"
        row = {
            "label": label,
            "folder": _folder(label, used) if paths else "",
            "variant": device["variant"] or "",
            "firmware_version": run["firmware_version"] or "",
            "build_id": device["build_id"] or "",
            "status": device["status"],
            "artifact_names": ";".join(path.name for path in paths),
            "config_hash": config_hash or "",
        }
        return row, paths

    async def stop(self) -> None:
        """Cancel the fleet runs of this process. Each one then shows `partial`."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
