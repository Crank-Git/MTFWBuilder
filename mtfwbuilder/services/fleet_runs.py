"""Fleet runs: one build per fleet device, fed to the build queue one at a time (feature fleet-builds, FR 15 to 18).

A fleet run holds its "must re-enter" secrets and the userPrefs file of each build in memory only. The process loses
them on a restart, so an unfinished fleet run then shows `partial`, and Retry failed needs the secrets again.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    DeviceVariant,
    FirmwareVersionNotInstalled,
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
# A fleet device without a build in the fleet run waits for the fleet run to give it to the build queue.
NOT_SUBMITTED = "queued"
UNREADABLE_ADMIN_KEY = "the admin key does not decrypt with the current secret_key, so enter it again"

DEVICE_ROWS = """
SELECT frb.fleet_device_id, frb.build_id, fd.label, fd.variant, b.variant AS build_variant, b.status,
       b.error_message
FROM fleet_run_builds frb
LEFT JOIN fleet_devices fd ON fd.id = frb.fleet_device_id
LEFT JOIN builds b ON b.id = frb.build_id
WHERE frb.fleet_run_id = ?
ORDER BY frb.fleet_device_id
"""


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


def _invalid(*errors: tuple[tuple, str]) -> RequestValidationError:
    return RequestValidationError([field_error(location, message) for location, message in errors])


def _device_fields(base: dict[str, Any], device: dict[str, Any]) -> dict[str, Any]:
    """Return the config form fields of one fleet device: the profile fields with the fleet device overrides."""
    fields = dict(base)
    for name, field in OVERRIDES.items():
        if device[name] is not None:
            fields[field] = str(device[name])
    if any(device[name] is not None for name in FIXED_POSITION_FIELDS):
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
    if all(status == "complete" for status in statuses):
        return "complete"
    return "partial" if "complete" in statuses else "failed"


async def _wait_until_ended(ctx: BuildContext) -> None:
    while True:
        # Hold the event before the read, so a change between the read and the wait is not lost.
        changed = ctx.changed
        if ctx.ended:
            return
        await changed.wait()


class FleetRuns:
    """Run the fleet runs of this process, and hold their "must re-enter" secrets in memory."""

    def __init__(self, settings: Settings, build_queue: BuildQueue):
        self.settings = settings
        self.build_queue = build_queue
        self._tasks: dict[int, asyncio.Task] = {}
        self._secrets: dict[int, dict[str, str]] = {}

    def running(self, run_id: int) -> bool:
        """Return True while this process feeds the builds of the fleet run to the build queue."""
        return run_id in self._tasks

    async def start(self, fleet_id: int, profile_secret_key: str | None, entered: dict[str, str]) -> int:
        """Return the id of a new fleet run of the fleet, after its first build goes to the build queue.

        `entered` holds the "must re-enter" secrets of the profile. `profile_secret_key` None gives the profile
        without its stored secrets. The server decrypts the admin key of each fleet device for the build only.

        Raises:
        - `FleetNotFound` when no fleet has the id.
        - `RequestValidationError` with each error of the fleet, the profile or the secrets.
        """
        fleet = await fleets.get_fleet(self.settings.database_path, fleet_id, self.settings.secret_key)
        if fleet is None:
            raise FleetNotFound(fleet_id)
        version = await _firmware_version(self.settings, fleet["firmware_version"])
        base = await _base_fields(self.settings.database_path, fleet["profile_id"], profile_secret_key, entered)
        jobs = await _jobs(self.settings.database_path, fleet, base, version)
        async with aiosqlite.connect(self.settings.database_path) as db:
            cursor = await db.execute(
                "INSERT INTO fleet_runs (fleet_id, firmware_version, status) VALUES (?, ?, 'running')",
                (fleet_id, version),
            )
            run_id = cursor.lastrowid
            await db.executemany(
                "INSERT INTO fleet_run_builds (fleet_run_id, fleet_device_id) VALUES (?, ?)",
                [(run_id, job.fleet_device_id) for job in jobs],
            )
            await db.commit()
        self._launch(run_id, version, jobs, entered)
        return run_id

    async def retry(self, run_id: int, profile_secret_key: str | None, entered: dict[str, str]) -> None:
        """Give each fleet device of the fleet run without a complete build to the build queue again (FR 18).

        Without `entered`, the retry uses the secrets that this process holds for the fleet run.

        Raises:
        - `FleetRunNotFound` when no fleet run has the id.
        - `FleetRunConflict` when the fleet run runs, has no failed fleet device, or its fleet devices changed.
        - `RequestValidationError` with each error of the fleet, the profile or the secrets.
        """
        run = await self.get(run_id)
        if run is None:
            raise FleetRunNotFound(run_id)
        retry_ids = self._retry_ids(run)
        fleet = await fleets.get_fleet(self.settings.database_path, run["fleet_id"], self.settings.secret_key)
        # A change of the fleet devices gives each one a new id, so the old fleet run cannot find them.
        if retry_ids - {device["id"] for device in fleet["devices"]}:
            raise FleetRunConflict("The fleet devices changed after this fleet run. Start a new fleet run.")
        version = await _firmware_version(self.settings, run["firmware_version"])
        secrets = entered or self._secrets.get(run_id, {})
        base = await _base_fields(self.settings.database_path, fleet["profile_id"], profile_secret_key, secrets)
        jobs = await _jobs(self.settings.database_path, fleet, base, version, retry_ids)
        # Another retry can start during the reads, so the check runs again with no wait before the launch.
        self._retry_ids(run)
        self._launch(run_id, version, jobs, secrets)

    def _retry_ids(self, run: dict[str, Any]) -> set[int]:
        if self.running(run["id"]):
            raise FleetRunConflict("The fleet run is running.")
        retry_ids = {device["fleet_device_id"] for device in run["devices"] if device["status"] != "complete"}
        if not retry_ids:
            raise FleetRunConflict("The fleet run has no failed fleet device.")
        return retry_ids

    async def get(self, run_id: int) -> dict[str, Any] | None:
        """Return the fleet run with the build status of each fleet device, or None when no fleet run has the id.

        A fleet run that its row shows as running, but that this process does not run, stopped with the process.
        It shows `partial`, so Retry failed continues it.
        """
        async with aiosqlite.connect(self.settings.database_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM fleet_runs WHERE id = ?", (run_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            cursor = await db.execute(DEVICE_ROWS, (run_id,))
            devices = [self._device(device) for device in await cursor.fetchall()]
        status = row["status"]
        if self.running(run_id):
            status = "running"
        elif status == "running":
            status = "partial"
        return {
            "id": row["id"],
            "fleet_id": row["fleet_id"],
            "firmware_version": row["firmware_version"],
            "status": status,
            "created_at": row["created_at"],
            "devices": devices,
        }

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

    def _launch(self, run_id: int, version: str, jobs: list[_Job], secrets: dict[str, str]) -> None:
        if secrets:
            self._secrets[run_id] = secrets
        task = asyncio.create_task(self._feed(run_id, version, jobs))
        self._tasks[run_id] = task
        task.add_done_callback(lambda done: self._ended(run_id, done))

    def _ended(self, run_id: int, task: asyncio.Task) -> None:
        self._tasks.pop(run_id, None)
        if not task.cancelled() and task.exception() is not None:
            # The row still shows `running`, so the fleet run shows `partial` and Retry failed continues it.
            logger.error(f"Fleet run {run_id} stopped because of an internal error", exc_info=task.exception())

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
                ctx = await self.build_queue.submit(job.variant, job.config_content, version, fleet_run_id=run_id)
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
        run = await self.get(run_id)
        status = _ended_status([device["status"] for device in run["devices"]])
        async with aiosqlite.connect(database_path) as db:
            await db.execute("UPDATE fleet_runs SET status = ? WHERE id = ?", (status, run_id))
            await db.commit()
        if status == "complete":
            # A complete fleet run has nothing to retry, so the process keeps its secrets no longer.
            self._secrets.pop(run_id, None)
        logger.info(f"Fleet run {run_id} ended {status}")

    async def stop(self) -> None:
        """Cancel the fleet runs of this process. Each one then shows `partial`."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
