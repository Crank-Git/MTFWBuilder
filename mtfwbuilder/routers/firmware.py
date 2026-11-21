"""Firmware version routes: the installed firmware sources, and the install and the removal of firmware versions."""

import logging

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sse_starlette.sse import EventSourceResponse

from mtfwbuilder.auth import require_admin
from mtfwbuilder.database import list_firmware_sources
from mtfwbuilder.models import FirmwareSource, InstallRequest
from mtfwbuilder.services import firmware_updater

logger = logging.getLogger("mtfwbuilder.firmware_versions")

router = APIRouter(prefix="/api/v1/firmware", tags=["firmware versions"])


@router.get("", response_model=list[FirmwareSource])
async def list_installed(request: Request):
    """Return the installed firmware versions, the newest installed first."""
    return await list_firmware_sources(request.app.state.settings.database_path)


@router.post("/install", status_code=202, dependencies=[Depends(require_admin)])
async def install(body: InstallRequest, request: Request):
    """Start an install by channel or by exact tag, and return its install id and progress stream URL.

    Returns:
    - 422 for a tag that is not a firmware version.
    - 409 when the firmware version is installed, or an install of it has not ended.
    - 502 when GitHub gives no firmware version for the channel.
    """
    installs: firmware_updater.FirmwareInstalls = request.app.state.firmware_installs
    try:
        started = await installs.start(channel=body.channel, tag=body.tag)
    except firmware_updater.FirmwareVersionInstalled as e:
        raise HTTPException(status_code=409, detail=f"Firmware version {e} is installed") from e
    except firmware_updater.InstallActive as e:
        raise HTTPException(status_code=409, detail=f"An install of {e.install.version} has not ended") from e
    except (firmware_updater.ReleaseNotFound, requests.RequestException) as e:
        logger.error(f"No firmware version for channel {body.channel}: {e}")
        raise HTTPException(status_code=502, detail="Cannot get the firmware version list from GitHub") from e
    return {"install_id": started.install_id, "progress_url": f"/api/v1/firmware/install/{started.install_id}/progress"}


@router.get("/install/{install_id}/progress")
async def install_progress(install_id: str, request: Request):
    """Return the progress stream of the install: the current install status, then each change."""
    started = request.app.state.firmware_installs.get(install_id)
    if started is None:
        raise HTTPException(status_code=404, detail="Install not found")
    return EventSourceResponse(firmware_updater.install_events(started))


@router.delete("/{version}", status_code=204, dependencies=[Depends(require_admin)])
async def remove(version: str, request: Request):
    """Delete the firmware source of the firmware version and its record.

    Returns 404 when the firmware version is not installed, and 409 while a build of it is queued or running.
    """
    try:
        await request.app.state.firmware_installs.remove(version)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Firmware version not installed") from e
    except firmware_updater.FirmwareVersionInUse as e:
        raise HTTPException(
            status_code=409, detail="A build uses this firmware version. Try again when it ends."
        ) from e
    return Response(status_code=204)
