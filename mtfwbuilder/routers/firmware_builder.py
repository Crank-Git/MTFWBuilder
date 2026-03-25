"""Firmware builder API routes — build, download, SSE progress."""

import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from mtfwbuilder.models import BuildStatus
from mtfwbuilder.services import build_service
from mtfwbuilder.services.cleanup_service import cleanup_build_directory
from mtfwbuilder.services.jsonc_generator import generate_jsonc

logger = logging.getLogger("mtfwbuilder.firmware_routes")

router = APIRouter(prefix="/api/v1", tags=["firmware"])


@router.post("/build-firmware")
async def start_build(request: Request):
    """Start a firmware build. Returns build_id for SSE progress tracking."""
    settings = request.app.state.settings
    registry = request.app.state.device_registry

    form = await request.form()
    variant_id = form.get("variant")
    config_source = form.get("config_source", "upload")
    custom_filename = form.get("custom_filename", "").strip()

    if not variant_id:
        raise HTTPException(status_code=400, detail="No device variant selected")

    if not registry.exists(variant_id):
        raise HTTPException(status_code=400, detail=f"Unknown device variant: {variant_id}")

    variant = registry.get(variant_id)

    # Get config content
    if config_source == "current":
        config_json = form.get("config_json") or form.get("stored_config")
        if not config_json:
            raise HTTPException(status_code=400, detail="No configuration data provided")
        try:
            config_data = json.loads(config_json)
            config_content = generate_jsonc(config_data)
        except (json.JSONDecodeError, Exception) as e:
            raise HTTPException(status_code=400, detail=f"Invalid configuration: {e}")
    else:
        upload = form.get("userPrefs")
        if upload is None:
            raise HTTPException(status_code=400, detail="No userPrefs file uploaded")
        if hasattr(upload, "read"):
            raw = await upload.read()
            config_content = raw.decode("utf-8")
        else:
            config_content = str(upload)

    # Create build context
    build_id = build_service.generate_build_id()
    ctx = build_service.BuildContext(
        build_id=build_id,
        variant=variant,
        config_content=config_content,
        settings=settings,
    )

    # Store build context for SSE endpoint
    if not hasattr(request.app.state, "active_builds"):
        request.app.state.active_builds = {}
    request.app.state.active_builds[build_id] = ctx

    return {
        "success": True,
        "build_id": build_id,
        "message": f"Build queued for {variant.name}",
        "progress_url": f"/api/v1/build-progress/{build_id}",
    }


@router.get("/build-progress/{build_id}")
async def build_progress(build_id: str, request: Request):
    """SSE endpoint for real-time build progress."""
    active_builds = getattr(request.app.state, "active_builds", {})
    ctx = active_builds.get(build_id)

    if ctx is None:
        raise HTTPException(status_code=404, detail="Build not found")

    async def event_stream():
        try:
            async for progress in build_service.build_firmware(ctx):
                data = BuildStatus(
                    status=progress.status,
                    message=progress.message,
                    download_url=progress.download_url,
                    error=progress.error,
                ).model_dump_json()
                yield {"event": "status", "data": data}

            # Send build log as final event
            yield {
                "event": "log",
                "data": json.dumps({"log": "\n".join(ctx.build_log[-100:])}),
            }
        except Exception as e:
            logger.error(f"Build {build_id} stream error: {e}")
            yield {
                "event": "status",
                "data": BuildStatus(status="failed", error=str(e)).model_dump_json(),
            }
        finally:
            # Remove from active builds
            active_builds.pop(build_id, None)

    return EventSourceResponse(event_stream())


@router.get("/download-firmware/{build_id}")
async def download_firmware(build_id: str, request: Request, variant: str = "unknown", filename: str = ""):
    """Download the built firmware file."""
    settings = request.app.state.settings
    registry = request.app.state.device_registry

    build_dir = settings.temp_dir / build_id
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
    else:
        if firmware_filename == "firmware.factory.bin":
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

    # Schedule cleanup after response is sent (via background task)
    response.background = lambda: _sync_cleanup(build_dir)
    return response


def _sync_cleanup(build_dir):
    """Synchronous cleanup for FileResponse background task."""
    import shutil

    try:
        if build_dir.exists():
            shutil.rmtree(str(build_dir), ignore_errors=True)
            logger.info(f"Post-download cleanup: {build_dir}")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")


@router.get("/system-info")
async def system_info(request: Request):
    """Get firmware version and system status."""
    from mtfwbuilder.services.firmware_updater import get_firmware_version

    settings = request.app.state.settings
    info = get_firmware_version(settings)
    return {"success": True, **info}
