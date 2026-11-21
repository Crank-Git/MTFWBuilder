"""Fleet API routes: fleets (feature fleet-builds, FR 10, 11 and 14) and fleet runs (FR 15 to 18)."""

from collections.abc import Iterator
from typing import IO

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from mtfwbuilder.auth import require_csrf
from mtfwbuilder.models import (
    Fleet,
    FleetCsv,
    FleetDevice,
    FleetDeviceRows,
    FleetRequest,
    FleetRun,
    FleetRunRequest,
    FleetUpdate,
)
from mtfwbuilder.rate_limit import build_rate_limit, limiter
from mtfwbuilder.routers.firmware_builder import QUEUE_FULL_DETAIL
from mtfwbuilder.routers.profiles import NO_STORE, is_admin
from mtfwbuilder.services import fleets
from mtfwbuilder.services.build_service import BuildQueueFull
from mtfwbuilder.services.fleet_runs import (
    FLEET_RUNNING,
    ZIP_CHUNK_BYTES,
    FleetNotFound,
    FleetRunConflict,
    FleetRunNotFound,
)

router = APIRouter(prefix="/api/v1", tags=["fleets"])


def _not_found(fleet_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Fleet {fleet_id} does not exist")


def _field_error(field: str, msg: str) -> HTTPException:
    return HTTPException(status_code=422, detail=[{"loc": ["body", field], "msg": msg, "type": "value_error"}])


async def _check(request: Request, profile_id: int | None, devices: list[FleetDevice] | None) -> None:
    """Raise 422 when the profile does not exist or the fleet holds more than `max_fleet_size` devices.

    The limit comes from a setting, so the pydantic model cannot hold it (FR-fleet-builds-14).
    """
    settings = request.app.state.settings
    if devices is not None and len(devices) > settings.max_fleet_size:
        raise _field_error("devices", f"a fleet holds at most {settings.max_fleet_size} devices")
    if profile_id is not None and not await fleets.profile_exists(settings.database_path, profile_id):
        raise _field_error("profile_id", f"profile {profile_id} does not exist")


async def _load(request: Request, response: Response, fleet_id: int, admin_keys: bool = True) -> dict:
    """Return the fleet, with the admin keys only for an admin session and `admin_keys` (ADR 0007).

    The fleet editor page never holds an admin key, so the device routes of the page give `admin_keys` False.
    """
    settings = request.app.state.settings
    secret_key = settings.secret_key if admin_keys and is_admin(request) else None
    fleet = await fleets.get_fleet(settings.database_path, fleet_id, secret_key)
    if fleet is None:
        raise _not_found(fleet_id)
    response.headers.update(NO_STORE)
    return fleet


@router.get("/fleets", response_model=list[Fleet])
async def get_fleets(request: Request):
    """Return every fleet in the order of creation, without admin keys, also for an admin session."""
    return await fleets.list_fleets(request.app.state.settings.database_path)


@router.post("/fleets", status_code=201, response_model=Fleet)
async def post_fleet(body: FleetRequest, request: Request, response: Response):
    """Create a fleet from a profile (FR-fleet-builds-10). Returns:

    - 422 with field errors when a fleet device is not valid.
    - 422 when the profile does not exist or the fleet holds more than `MTFW_MAX_FLEET_SIZE` devices.
    """
    await _check(request, body.profile_id, body.devices)
    settings = request.app.state.settings
    fleet_id = await fleets.create_fleet(settings.database_path, settings.secret_key, body)
    return await _load(request, response, fleet_id)


@router.get("/fleets/{fleet_id}", response_model=Fleet)
async def get_fleet(fleet_id: int, request: Request, response: Response):
    """Return the fleet, or 404 when no fleet has the id. Only an admin session gets the admin keys."""
    return await _load(request, response, fleet_id)


@router.put("/fleets/{fleet_id}", response_model=Fleet)
async def put_fleet(fleet_id: int, body: FleetUpdate, request: Request, response: Response):
    """Change the name, the profile, the firmware version or the fleet devices of the fleet.

    A fleet device without an `admin_key` keeps the saved admin key of the fleet device with the same label.
    Returns 404 when no fleet has the id, and 422 for the errors of `POST /api/v1/fleets`.
    """
    await _check(request, body.profile_id, body.devices)
    settings = request.app.state.settings
    if not await fleets.update_fleet(settings.database_path, settings.secret_key, fleet_id, body):
        raise _not_found(fleet_id)
    return await _load(request, response, fleet_id)


def _row_errors(errors: list[dict]) -> HTTPException:
    """Return a 422 with each row error as a field error that also names its row and its field."""
    detail = []
    for error in errors:
        row = [] if error["row"] is None else ["devices", error["row"]]
        loc = ["body", *row, *([error["field"]] if error["field"] else [])]
        detail.append({"loc": loc, "type": "value_error", **error})
    return HTTPException(status_code=422, detail=detail)


async def _checked_devices(request: Request, fleet_id: int, rows: list[dict], append: bool) -> list[FleetDevice]:
    """Return the fleet device of each row, or raise 404 or a 422 with every row error (FR-fleet-builds-13).

    With `append`, the rows go after the saved fleet devices, so the size limit counts both. The size check is
    first, so a request with too many rows gets one error and no row check.
    """
    settings = request.app.state.settings
    fleet = await fleets.get_fleet(settings.database_path, fleet_id, None)
    if fleet is None:
        raise _not_found(fleet_id)
    saved = fleet["devices"] if append else []
    if len(saved) + len(rows) > settings.max_fleet_size:
        raise _row_errors(
            [fleets.row_error(None, "devices", f"a fleet holds at most {settings.max_fleet_size} devices")]
        )
    saved_labels = {device["label"] for device in saved}
    devices, errors = await fleets.check_devices(settings.database_path, fleet["firmware_version"], rows, saved_labels)
    if errors:
        raise _row_errors(errors)
    return devices


@router.put("/fleets/{fleet_id}/devices", response_model=Fleet)
async def put_fleet_devices(fleet_id: int, body: FleetDeviceRows, request: Request, response: Response):
    """Replace every fleet device with the rows of the fleet device table, the first row as row 1.

    A row with an `id` changes the saved fleet device with that id, and a row without one changes the saved fleet
    device with the same label. A row without an `admin_key` keeps the saved admin key. The response holds no admin
    key. Returns 404 when no fleet has the id, and 422 with every row error. A row error saves no row.
    """
    devices = await _checked_devices(request, fleet_id, body.devices, append=False)
    settings = request.app.state.settings
    await fleets.update_fleet(settings.database_path, settings.secret_key, fleet_id, FleetUpdate(devices=devices))
    return await _load(request, response, fleet_id, admin_keys=False)


@router.post("/fleets/{fleet_id}/devices/csv", response_model=Fleet)
async def post_fleet_devices_csv(fleet_id: int, body: FleetCsv, request: Request, response: Response):
    """Add a fleet device for each line of the CSV after its header, after the saved fleet devices (FR-fleet-builds-12).

    The header is `label,variant,long_name,short_name,role,lat,lon,alt,admin_key`, and the line after it is row 1.
    The response holds no admin key. Returns 404 when no fleet has the id, and 422 with every row error. A row
    error saves no row.
    """
    try:
        rows = fleets.read_csv(body.csv)
    except fleets.CsvHeaderError as e:
        raise _row_errors([fleets.row_error(None, "csv", str(e))]) from e
    devices = await _checked_devices(request, fleet_id, rows, append=True)
    settings = request.app.state.settings
    await fleets.add_devices(settings.database_path, settings.secret_key, fleet_id, devices)
    return await _load(request, response, fleet_id, admin_keys=False)


@router.delete("/fleets/{fleet_id}", status_code=204)
async def delete_fleet(fleet_id: int, request: Request) -> Response:
    """Delete the fleet with its fleet devices and fleet runs. Returns:

    - 404 when no fleet has the id.
    - 409 while a fleet run of the fleet runs.
    """
    fleet_runs = request.app.state.fleet_runs
    if fleet_runs.runs_fleet(fleet_id):
        raise HTTPException(status_code=409, detail=FLEET_RUNNING)
    if not await fleets.delete_fleet(request.app.state.settings.database_path, fleet_id):
        raise _not_found(fleet_id)
    fleet_runs.forget_fleet(fleet_id)
    return Response(status_code=204)


def _run_not_found(run_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Fleet run {run_id} does not exist")


def _accepted(run_id: str) -> dict:
    return {"run_id": run_id, "progress_url": f"/api/v1/fleet-runs/{run_id}"}


def _limit_error(error: FleetRunConflict | BuildQueueFull) -> HTTPException:
    """Return the 409 of a fleet run conflict, or the 503 of a full build queue with the text of the build route."""
    if isinstance(error, BuildQueueFull):
        return HTTPException(status_code=503, detail=QUEUE_FULL_DETAIL)
    return HTTPException(status_code=409, detail=str(error))


async def _profile_secret_key(request: Request) -> str | None:
    """Return `secret_key` for an admin session, so only an admin session builds with stored secrets (ADR 0007).

    A build with the stored secrets of an admin is an admin action, so an admin session also needs the CSRF token.

    Raises:
        HTTPException: 403 when an admin session has no valid CSRF token.
    """
    if not is_admin(request):
        return None
    await require_csrf(request)
    return request.app.state.settings.secret_key


@router.post("/fleets/{fleet_id}/runs", status_code=202)
@limiter.limit(build_rate_limit)
async def post_fleet_run(fleet_id: int, request: Request, body: FleetRunRequest | None = None) -> dict:
    """Start a fleet run that makes one build per fleet device (FR-fleet-builds-15), and return its URL.

    The body holds each "must re-enter" secret of the profile once, for the whole fleet run. Returns:

    - 403 when an admin session has no valid CSRF token.
    - 404 when no fleet has the id.
    - 409 while a fleet run of the fleet runs.
    - 422 with field errors, before a build starts, in these cases:
      - a "must re-enter" secret is missing, or the profile does not ask for a secret of the body.
      - a fleet device has no variant, or a variant that is not in the firmware version.
      - a fleet device or the profile gives a config that is not valid.
      - the firmware version is not installed, or the fleet holds no fleet device.
    - 429 when the client is over the build rate limit.
    - 503 when the build queue is full.
    """
    secrets = body.secrets if body else {}
    profile_secret_key = await _profile_secret_key(request)
    try:
        run_id = await request.app.state.fleet_runs.start(fleet_id, profile_secret_key, secrets)
    except FleetNotFound as e:
        raise _not_found(fleet_id) from e
    except (FleetRunConflict, BuildQueueFull) as e:
        raise _limit_error(e) from e
    return _accepted(run_id)


@router.get("/fleet-runs/{run_id}", response_model=FleetRun)
async def get_fleet_run(run_id: str, request: Request):
    """Return the fleet run with the build status of each fleet device, or 404 when no fleet run has the id."""
    run = await request.app.state.fleet_runs.get(run_id)
    if run is None:
        raise _run_not_found(run_id)
    return run


@router.post("/fleet-runs/{run_id}/retry", status_code=202)
@limiter.limit(build_rate_limit)
async def retry_fleet_run(run_id: str, request: Request, body: FleetRunRequest | None = None) -> dict:
    """Give each fleet device of the fleet run without a complete build to the build queue again (FR-fleet-builds-18).

    The process holds the secrets of the fleet run, and a secret of the body replaces the held one. After a restart
    or after `build_max_age_seconds`, the process holds no secrets, so the body gives them again. Returns:

    - 404 when no fleet run has the id.
    - 409 when a fleet run of the fleet runs, the fleet run has no failed fleet device, or its fleet devices changed.
    - 403, 422, 429 and 503 for the errors of `POST /api/v1/fleets/{id}/runs`.
    """
    secrets = body.secrets if body else {}
    profile_secret_key = await _profile_secret_key(request)
    try:
        await request.app.state.fleet_runs.retry(run_id, profile_secret_key, secrets)
    except FleetRunNotFound as e:
        raise _run_not_found(run_id) from e
    except (FleetRunConflict, BuildQueueFull) as e:
        raise _limit_error(e) from e
    return _accepted(run_id)


def _chunks(file: IO[bytes]) -> Iterator[bytes]:
    """Yield the file in chunks, and close it after the last one or when the client goes away."""
    with file:
        while chunk := file.read(ZIP_CHUNK_BYTES):
            yield chunk


@router.get("/fleet-runs/{run_id}/download")
async def download_fleet_run(run_id: str, request: Request) -> StreamingResponse:
    """Send one zip with a folder for each fleet device label and `manifest.csv` (FR-fleet-builds-19 and -20).

    A folder holds the artifacts of a complete build. `manifest.csv` has one row for each fleet device with its
    label, folder, variant, firmware version, build id, build status, artifact names and config hash. It holds no
    secret. Returns 404 when no fleet run has the id, and 409 while the fleet run runs.
    """
    try:
        file = await request.app.state.fleet_runs.archive(run_id)
    except FleetRunNotFound as e:
        raise _run_not_found(run_id) from e
    except FleetRunConflict as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    headers = {"Content-Disposition": f'attachment; filename="fleet-run-{run_id}.zip"', **NO_STORE}
    return StreamingResponse(_chunks(file), media_type="application/zip", headers=headers)
