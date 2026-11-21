"""Firmware version routes: the installed and the upstream firmware versions, and their install and removal.

An htmx request from the Firmware sources section of the admin page gets an HTML fragment.
Every other request gets JSON.
"""

import asyncio
import json
import logging

import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sse_starlette.sse import EventSourceResponse

from mtfwbuilder.auth import require_admin, require_csrf
from mtfwbuilder.database import list_firmware_sources
from mtfwbuilder.htmx import fragment_or_json, is_htmx
from mtfwbuilder.models import AvailableFirmwareVersions, FirmwareSource, InstallRequest, InstallStatus
from mtfwbuilder.services import firmware_updater
from mtfwbuilder.services.firmware_catalog import CHANNELS, CatalogUnavailable

logger = logging.getLogger("mtfwbuilder.firmware_versions")

router = APIRouter(prefix="/api/v1/firmware", tags=["firmware versions"])


def install_context(install: firmware_updater.Install) -> dict:
    """Return the template context of the install panel."""
    return {"install": install, "progress_url": f"/api/v1/firmware/install/{install.install_id}/progress"}


def _refusal(request: Request, status_code: int, detail: str, cause: Exception) -> Response:
    """Return the message as an alert in the message area of the section for htmx, else raise `HTTPException`."""
    if not is_htmx(request):
        raise HTTPException(status_code=status_code, detail=detail) from cause
    response = request.app.state.templates.TemplateResponse(
        request, "partials/admin_result.html", {"success": False, "error": detail}, status_code=status_code
    )
    response.headers["HX-Retarget"] = "#firmware-message"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


async def _revoked_warning(request: Request, tag: str) -> str:
    """Return a warning when the upstream firmware list marks the tag as revoked, else an empty string.

    The warning does not stop the install, so an unreachable upstream gives no warning and the install continues.
    """
    try:
        available = await asyncio.to_thread(request.app.state.firmware_catalog.available)
    except CatalogUnavailable as e:
        logger.warning(f"No revoked check for {tag}: {e}")
        return ""
    if any(entry["tag"] == tag and entry["revoked"] for channel in CHANNELS for entry in available[channel]):
        return f"The upstream firmware list marks {tag} as revoked."
    return ""


@router.get("", response_model=list[FirmwareSource])
async def list_installed(request: Request):
    """Return the installed firmware versions, the newest installed first."""
    return await list_firmware_sources(request.app.state.settings.database_path)


@router.get("/available", response_model=AvailableFirmwareVersions, dependencies=[Depends(require_admin)])
async def available(request: Request):
    """Return the upstream stable and alpha firmware versions, from api.meshtastic.org or else from GitHub.

    Returns 502 when both upstreams fail.
    """
    try:
        result = await asyncio.to_thread(request.app.state.firmware_catalog.available)
    except CatalogUnavailable as e:
        logger.error(f"No upstream firmware list: {e}")
        detail = "Cannot reach api.meshtastic.org or GitHub."
        if not is_htmx(request):
            raise HTTPException(status_code=502, detail=detail) from e
        return request.app.state.templates.TemplateResponse(
            request, "partials/firmware_available.html", {"error": detail}, status_code=502
        )
    data = AvailableFirmwareVersions(**result)
    return fragment_or_json(request, "partials/firmware_available.html", {"available": result}, data)


@router.post("/install", status_code=202, dependencies=[Depends(require_admin), Depends(require_csrf)])
async def install(body: InstallRequest, request: Request):
    """Start an install by channel or by exact tag, and return its install id, progress stream URL and warning.

    The warning is not empty when the install is by exact tag and the upstream firmware list marks the tag as revoked.

    Returns:
    - 422 for a tag that is not a firmware version.
    - 409 when the firmware version is installed, or an install of it has not ended.
    - 502 when the upstream firmware list gives no firmware version for the channel.
    """
    installs: firmware_updater.FirmwareInstalls = request.app.state.firmware_installs
    try:
        started = await installs.start(channel=body.channel, tag=body.tag)
    except firmware_updater.FirmwareVersionInstalled as e:
        return _refusal(request, 409, f"Firmware version {e} is installed", e)
    except firmware_updater.InstallActive as e:
        return _refusal(request, 409, f"An install of {e.install.version} has not ended", e)
    except (firmware_updater.ReleaseNotFound, requests.RequestException) as e:
        logger.error(f"No firmware version for channel {body.channel}: {e}")
        return _refusal(request, 502, "Cannot get the firmware version from the upstream firmware list", e)
    if body.tag is not None:
        started.warning = await _revoked_warning(request, body.tag)
    context = install_context(started)
    data = {"install_id": started.install_id, "progress_url": context["progress_url"], "warning": started.warning}
    response = fragment_or_json(request, "partials/firmware_install.html", context, data, status_code=202)
    # The controls put an error message in the message area, so a started install goes to the top of the panels.
    response.headers["HX-Retarget"] = "#firmware-installs"
    response.headers["HX-Reswap"] = "afterbegin"
    return response


@router.get("/install/{install_id}", dependencies=[Depends(require_admin)])
async def install_status(install_id: str, request: Request) -> Response:
    """Return the install status of the install, as JSON or as the install panel for htmx.

    The panel calls this route on each `status` event of the install progress stream. For an install that has
    not ended, the fragment holds only the install state. For an ended install, the fragment is the whole panel
    without the progress stream. After a ready install, the fragment also refreshes the firmware source list.
    Returns 404 for an unknown install.
    """
    started = request.app.state.firmware_installs.get(install_id)
    if started is None:
        raise HTTPException(status_code=404, detail="Install not found")
    data = InstallStatus(**json.loads(started.status_event()))
    context = install_context(started)
    if not started.ended:
        return fragment_or_json(request, "partials/install_state.html", context, data)
    if started.status == "ready":
        context["sources"] = await list_firmware_sources(request.app.state.settings.database_path)
        context["refresh_sources"] = True
    response = fragment_or_json(request, "partials/firmware_install.html", context, data)
    # A swap of the element that holds the progress stream closes the stream, so the browser does not reconnect.
    response.headers["HX-Retarget"] = f"#install-{install_id}"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


@router.get("/install/{install_id}/progress", dependencies=[Depends(require_admin)])
async def install_progress(install_id: str, request: Request):
    """Return the progress stream of the install: the current install status, then each change."""
    started = request.app.state.firmware_installs.get(install_id)
    if started is None:
        raise HTTPException(status_code=404, detail="Install not found")
    return EventSourceResponse(firmware_updater.install_events(started))


@router.delete("/{version}", status_code=204, dependencies=[Depends(require_admin), Depends(require_csrf)])
async def remove(version: str, request: Request):
    """Delete the firmware source of the firmware version and its record.

    An htmx request gets the firmware source list after the removal, because htmx swaps no 204 response.
    Returns 404 when the firmware version is not installed, and 409 while a build of it is queued or running.
    """
    try:
        await request.app.state.firmware_installs.remove(version)
    except KeyError as e:
        return _refusal(request, 404, "Firmware version not installed", e)
    except firmware_updater.FirmwareVersionInUse as e:
        return _refusal(request, 409, "A build uses this firmware version. Try again when it ends.", e)
    if is_htmx(request):
        sources = await list_firmware_sources(request.app.state.settings.database_path)
        return request.app.state.templates.TemplateResponse(
            request, "partials/firmware_sources.html", {"sources": sources}
        )
    return Response(status_code=204)
