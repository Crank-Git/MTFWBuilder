"""Firmware builder API routes — build, download, SSE progress."""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from mtfwbuilder.models import Artifact, ConfigRequest
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

    Returns 400 for a bad form, 422 with field errors for an invalid config, and 503 when the
    build queue is full. The route validates the config before the build goes in the build queue.
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
    """Return the userPrefs file that the generator makes from the config JSON of the form.

    Raises HTTP 400 when the form holds no config, and a 422 field error when `ConfigRequest` rejects it.
    """
    if not config_json:
        raise HTTPException(status_code=400, detail="No configuration data provided")
    try:
        config = ConfigRequest.model_validate_json(config_json)
    except ValidationError as e:
        # The same field locations as the download route, so the form puts each error next to its field.
        errors = [{**error, "loc": ("body", *error["loc"])} for error in e.errors(include_url=False)]
        raise RequestValidationError(errors) from e
    return generate_jsonc(config.generator_input())


async def _config_from_upload(upload) -> str:
    """Return the text of the uploaded userPrefs file.

    An uploaded file holds `USERPREFS_*` keys, not `ConfigRequest` fields, so the route checks only
    the size, the UTF-8 encoding and the `//` rule. Raises HTTP 400 for a missing or large file, and
    a 422 field error for `userPrefs` when a check fails.
    """
    if upload is None:
        raise HTTPException(status_code=400, detail="No userPrefs file uploaded")
    if isinstance(upload, str):
        content = upload
    else:
        raw = await upload.read()
        if len(raw) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=400, detail="File too large (max 64KB)")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise _upload_error("the file is not UTF-8 text") from e
    for number, line in enumerate(content.splitlines(), start=1):
        before, double_slash, _ = line.partition("//")
        # The upstream parser cuts each line at the first //, so a // after an odd count of quotes cuts a value.
        if double_slash and before.replace('\\"', "").count('"') % 2:
            raise _upload_error(f"line {number}: a value contains //, which the firmware build reads as a comment")
    return content


def _upload_error(message: str) -> RequestValidationError:
    return RequestValidationError(
        [{"type": "value_error", "loc": ("body", "userPrefs"), "msg": message, "input": None}]
    )


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


@router.get("/builds/{build_id}/artifacts", response_model=list[Artifact])
async def list_artifacts(build_id: str, request: Request):
    """Return the artifact list of the build. Returns 404 for an unknown build."""
    return _get_build(request, build_id).artifacts


@router.get("/builds/{build_id}/artifacts/{name}")
async def download_artifact(build_id: str, name: str, request: Request):
    """Send one artifact of the build. Returns 404 when `name` is not in the artifact list."""
    ctx = _get_build(request, build_id)
    # Only a name from the artifact list reaches the file system, so a path in `name` cannot leave the build.
    path = ctx.build_dir / name
    if name not in {a.name for a in ctx.artifacts} or not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(
        path=str(path),
        filename=name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/download-firmware/{build_id}")
async def download_firmware(build_id: str, request: Request):
    """Redirect to the factory or primary artifact of the build.

    This old route stays for one release. Returns 404 when the build has no firmware artifact.
    """
    primary = build_service.primary_artifact(_get_build(request, build_id).artifacts)
    if primary is None:
        raise HTTPException(status_code=404, detail="Firmware file not found")
    return RedirectResponse(build_service.artifact_url(build_id, primary.name), status_code=308)


def _get_build(request: Request, build_id: str) -> build_service.BuildContext:
    ctx = request.app.state.build_queue.get(build_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail="Build not found")
    return ctx


@router.get("/system-info")
async def system_info(request: Request):
    """Get firmware version and system status."""
    settings = request.app.state.settings
    info = get_firmware_version(settings)
    return {"success": True, **info}
