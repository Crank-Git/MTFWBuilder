"""Firmware builder API routes — build, download, SSE progress."""

import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse

from mtfwbuilder.htmx import fragment_or_json, is_htmx
from mtfwbuilder.models import Artifact, ConfigRequest
from mtfwbuilder.rate_limit import limiter
from mtfwbuilder.services import build_service
from mtfwbuilder.services.firmware_updater import get_firmware_version
from mtfwbuilder.services.jsonc_generator import generate_jsonc

logger = logging.getLogger("mtfwbuilder.firmware_routes")

router = APIRouter(prefix="/api/v1", tags=["firmware"])

# The same 64 KB limit as the preview-userprefs route.
MAX_UPLOAD_SIZE = 65536
QUEUE_FULL_NOTICE = "The build queue is full. Try again in a few minutes."
# The steps of a build before its ended build status, in build order.
RUN_STEPS = ("queued", "compiling", "linking", "packaging")


@router.post("/build-firmware", status_code=202)
@limiter.limit("5/minute")
async def start_build(request: Request) -> Response:
    """Put a build in the build queue, and return its build id and progress stream URL.

    An htmx request gets the progress panel of the build, and the page URL changes to `/?build=<id>`.
    The form gives the config in one of three ways, by `config_source`:

    - `form`: the fields of the config form.
    - `current`: one field `config_json` that holds the config as JSON.
    - `upload` (the default): one field `userPrefs` that holds a userPrefs file.

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
    config_content = await _config_content(form)

    try:
        ctx = request.app.state.build_queue.submit(variant, config_content)
    except build_service.BuildQueueFull as e:
        if is_htmx(request):
            return request.app.state.templates.TemplateResponse(
                request, "partials/build_notice.html", {"notice": QUEUE_FULL_NOTICE}, status_code=503
            )
        raise HTTPException(status_code=503, detail="Build queue is full. Try again later.") from e

    data = {"build_id": ctx.build_id, "progress_url": progress_url(ctx.build_id)}
    response = fragment_or_json(request, "partials/build_panel.html", panel_context(ctx), data, status_code=202)
    # The page URL holds the build id, so a reload of the page shows the same build.
    response.headers["HX-Push-Url"] = f"/?build={ctx.build_id}"
    return response


def progress_url(build_id: str) -> str:
    return f"/api/v1/build-progress/{build_id}"


def panel_context(ctx: build_service.BuildContext) -> dict:
    """Return the template context of the progress panel of one build.

    Each step has the text `done`, `current` or `waiting`, so the panel shows the build status
    without color. A `failed` or `cancelled` build has no record of its last step, so its
    earlier steps have no text.
    """
    if ctx.status in ("failed", "cancelled"):
        steps = [(name, "") for name in RUN_STEPS] + [(ctx.status, "current")]
    else:
        names = [*RUN_STEPS, "complete"]
        index = len(names) if ctx.status == "complete" else names.index(ctx.status)
        steps = [
            (name, "done" if i < index else "current" if i == index else "waiting") for i, name in enumerate(names)
        ]
    primary = build_service.primary_artifact(ctx.artifacts)
    # The factory artifact goes first, because it is the preferred download.
    artifacts = sorted(ctx.artifacts, key=lambda a: a is not primary)
    return {
        "build": ctx,
        "steps": steps,
        "artifacts": [(a, build_service.artifact_url(ctx.build_id, a.name)) for a in artifacts],
        "progress_url": progress_url(ctx.build_id),
        "source_missing": ctx.error == build_service.FIRMWARE_SOURCE_MISSING,
    }


async def _config_content(form) -> str:
    """Return the userPrefs file of the build form, from the source that `config_source` names."""
    source = form.get("config_source", "upload")
    if source == "form":
        fields = {k: v for k, v in form.items() if k not in ("variant", "config_source")}
        return _validated(lambda: ConfigRequest.model_validate(fields))
    if source == "current":
        return _config_from_json(form.get("config_json") or form.get("stored_config"))
    return await _config_from_upload(form.get("userPrefs"))


def _validated(parse) -> str:
    """Return the userPrefs file of the config that `parse` returns.

    Raises a 422 field error when `ConfigRequest` rejects the config.
    """
    try:
        config = parse()
    except ValidationError as e:
        # The same field locations as the download route, so the form puts each error next to its field.
        errors = [{**error, "loc": ("body", *error["loc"])} for error in e.errors(include_url=False)]
        raise RequestValidationError(errors) from e
    return generate_jsonc(config.generator_input())


def _config_from_json(config_json: str | None) -> str:
    """Return the userPrefs file that the generator makes from the config JSON of the form.

    Raises HTTP 400 when the form holds no config, and a 422 field error when `ConfigRequest` rejects it.
    """
    if not config_json:
        raise HTTPException(status_code=400, detail="No configuration data provided")
    return _validated(lambda: ConfigRequest.model_validate_json(config_json))


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


@router.get("/builds/{build_id}")
async def get_build(build_id: str, request: Request) -> Response:
    """Return the build status of the build, as JSON or as the progress panel for htmx.

    The panel calls this route on each `status` event of the progress stream. For a build that
    has not ended, the fragment holds the steps, and the build log as an out-of-band swap.
    For an ended build, the fragment is the whole panel without the progress stream.
    Returns 404 for an unknown build.
    """
    ctx = _get_build(request, build_id)
    data = {"build_id": ctx.build_id, **json.loads(ctx.status_event())}
    if not ctx.ended:
        return fragment_or_json(request, "partials/build_state.html", {**panel_context(ctx), "oob_log": True}, data)
    response = fragment_or_json(request, "partials/build_panel.html", panel_context(ctx), data)
    # A swap of the element that holds the progress stream closes the stream, so the browser does not reconnect.
    response.headers["HX-Retarget"] = "#build-panel"
    response.headers["HX-Reswap"] = "innerHTML"
    return response


@router.post("/builds/{build_id}/cancel")
async def cancel_build(build_id: str, request: Request):
    """Cancel a queued or running build. Returns 404 for an unknown build, and 409 for an ended build."""
    try:
        ctx = request.app.state.build_queue.cancel(build_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Build not found") from e
    except build_service.BuildEnded as e:
        raise HTTPException(status_code=409, detail="Build has already ended") from e
    # A running build changes to `cancelled` after its scrub, so the response gives the accepted result.
    return {"build_id": ctx.build_id, "status": "cancelled"}


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
