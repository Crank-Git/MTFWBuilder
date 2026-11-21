"""Fleet API routes: create, list, load, change and delete a fleet (feature fleet-builds, FR 10, 11 and 14)."""

from fastapi import APIRouter, HTTPException, Request, Response

from mtfwbuilder.models import Fleet, FleetDevice, FleetRequest, FleetUpdate
from mtfwbuilder.routers.profiles import NO_STORE, is_admin
from mtfwbuilder.services import fleets

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


async def _load(request: Request, response: Response, fleet_id: int) -> dict:
    """Return the fleet, with the admin keys only for an admin session (ADR 0007)."""
    settings = request.app.state.settings
    secret_key = settings.secret_key if is_admin(request) else None
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


@router.delete("/fleets/{fleet_id}", status_code=204)
async def delete_fleet(fleet_id: int, request: Request) -> Response:
    """Delete the fleet with its fleet devices, or return 404 when no fleet has the id."""
    if not await fleets.delete_fleet(request.app.state.settings.database_path, fleet_id):
        raise _not_found(fleet_id)
    return Response(status_code=204)
