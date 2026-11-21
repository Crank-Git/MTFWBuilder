"""Firmware builder API routes — build, download, SSE progress."""

import json
import logging
import re
import shutil

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from mtfwbuilder.rate_limit import limiter
from mtfwbuilder.services import build_service
from mtfwbuilder.services.firmware_updater import get_firmware_version
from mtfwbuilder.services.jsonc_generator import generate_jsonc

logger = logging.getLogger("mtfwbuilder.firmware_routes")

router = APIRouter(prefix="/api/v1", tags=["firmware"])

# The same 64 KB limit as the preview-userprefs route.
MAX_UPLOAD_SIZE = 65536


@router.post("/build-firmware", status_code=202)
@limiter.limit("5/minute")
async def start_build(request: Request):
    """Put a build in the build queue, and return its build id and progress stream URL.

    Returns 400 for a bad form, and 503 when the build queue is full.
    """
    registry = request.app.state.device_registry
    form = await request.form()

    variant_id = form.get("variant")
    if not variant_id:
        raise HTTPException(status_code=400, detail="No device variant selected")
    if not registry.exists(variant_id):
        raise HTTPException(status_code=400, detail=f"Unknown device variant: {variant_id}")
    variant = registry.get(variant_id)

    if form.get("config_source", "upload") == "current":
        config_content = _config_from_json(form.get("config_json") or form.get("stored_config"))
    else:
        config_content = await _config_from_upload(form.get("userPrefs"))

    try:
        ctx = request.app.state.build_queue.submit(variant, config_content)
    except build_service.BuildQueueFull as e:
        raise HTTPException(status_code=503, detail="Build queue is full. Try again later.") from e

    return {"build_id": ctx.build_id, "progress_url": f"/api/v1/build-progress/{ctx.build_id}"}


def _config_from_json(config_json: str | None) -> str:
    """Return the userPrefs file for the config JSON of the form, or raise HTTP 400."""
    if not config_json:
        raise HTTPException(status_code=400, detail="No configuration data provided")
    try:
        config_data = json.loads(config_json)
        if not isinstance(config_data, dict):
            raise TypeError("the configuration is not a JSON object")
        return generate_jsonc(config_data)
    # The generator raises ValueError or TypeError for a value that is not a number or a string.
    except (ValueError, TypeError, AttributeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid configuration: {e}") from e


async def _config_from_upload(upload) -> str:
    """Return the text of the uploaded userPrefs file, or raise HTTP 400."""
    if upload is None:
        raise HTTPException(status_code=400, detail="No userPrefs file uploaded")
    if not hasattr(upload, "read"):
        return str(upload)
    raw = await upload.read()
    if len(raw) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail="File too large (max 64KB)")
    return raw.decode("utf-8")


@router.get("/build-progress/{build_id}")
async def build_progress(build_id: str, request: Request):
    """Return the progress stream of the build: the current build status, its changes, and new log lines."""
    ctx = request.app.state.build_queue.get(build_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Build not found")
    return EventSourceResponse(build_service.progress_events(ctx))


@router.post("/builds/{build_id}/cancel")
async def cancel_build(build_id: str, request: Request):
    """Cancel a queued or running build. Returns 404 for an unknown build, and 409 for an ended build."""
    try:
        ctx = request.app.state.build_queue.cancel(build_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Build not found") from e
    except build_service.BuildEnded as e:
        raise HTTPException(status_code=409, detail="Build has already ended") from e
    return {"build_id": ctx.build_id, "status": ctx.status}


@router.get("/download-firmware/{build_id}")
async def download_firmware(  # noqa: C901, PLR0912  # TODO(#19): The artifact routes replace this route.
    build_id: str, request: Request, variant: str = "unknown", filename: str = ""
):
    """Download the built firmware file."""
    settings = request.app.state.settings
    registry = request.app.state.device_registry

    build_dir = (settings.temp_dir / build_id).resolve()
    if not str(build_dir).startswith(str(settings.temp_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid build ID")
    if not build_dir.is_dir():
        raise HTTPException(status_code=404, detail="Build not found or expired")

    # Determine firmware format
    if registry.exists(variant):
        device = registry.get(variant)
        fmt = device.firmware_format
        has_factory = device.has_factory_binary
    else:
        fmt = "bin"
        has_factory = False

    # For ESP32, prefer factory binary
    firmware_filename = f"firmware.{fmt}"
    firmware_path = build_dir / firmware_filename

    if has_factory:
        factory_path = build_dir / "firmware.factory.bin"
        if factory_path.exists():
            firmware_path = factory_path
            firmware_filename = "firmware.factory.bin"

    if not firmware_path.exists():
        raise HTTPException(status_code=404, detail="Firmware file not found")

    # Build download filename
    if filename:
        download_name = re.sub(r"[^\w\-\.]", "_", filename)
        if firmware_filename == "firmware.factory.bin":
            if not download_name.lower().endswith(".factory.bin"):
                download_name = download_name.replace(".bin", "") + ".factory.bin"
        elif not download_name.lower().endswith(f".{fmt}"):
            download_name += f".{fmt}"
    elif firmware_filename == "firmware.factory.bin":
        download_name = f"meshtastic_{variant}_firmware.factory.bin"
    else:
        download_name = f"meshtastic_{variant}_firmware.{fmt}"

    response = FileResponse(
        path=str(firmware_path),
        filename=download_name,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

    # Starlette awaits `background`, so a plain lambda raises after the file is sent.
    response.background = BackgroundTask(_sync_cleanup, build_dir)
    return response


def _sync_cleanup(build_dir):
    """Synchronous cleanup for FileResponse background task."""
    try:
        if build_dir.exists():
            shutil.rmtree(str(build_dir), ignore_errors=True)
            logger.info(f"Post-download cleanup: {build_dir}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


@router.get("/system-info")
async def system_info(request: Request):
    """Get firmware version and system status."""
    settings = request.app.state.settings
    info = get_firmware_version(settings)
    return {"success": True, **info}
