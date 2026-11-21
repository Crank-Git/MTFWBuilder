"""Config generator API routes — /api/v1/generate, /api/v1/preview, /api/v1/download."""

import logging
from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import ValidationError

from mtfwbuilder.htmx import fragment_or_json
from mtfwbuilder.models import ConfigRequest, FilePreviewResponse, PreviewResponse
from mtfwbuilder.services.jsonc_generator import generate_jsonc

logger = logging.getLogger("mtfwbuilder.config_generator")

router = APIRouter(prefix="/api/v1", tags=["config"])

MAX_UPLOAD_SIZE = 65536  # 64KB


@router.post("/generate")
async def generate(config: ConfigRequest) -> PreviewResponse:
    """Generate JSONC configuration from form data."""
    return PreviewResponse(success=True, content=generate_jsonc(config.generator_input()))


async def _read_config(request: Request) -> ConfigRequest:
    """Return the `ConfigRequest` of a JSON body or of a form body.

    A form holds the config fields, or one field `config` that holds the config as JSON.
    The config form posts the fields themselves, so it works without JavaScript.

    Raises:
        RequestValidationError: The body is not a valid `ConfigRequest`.
    """
    try:
        if "application/json" in request.headers.get("content-type", ""):
            return ConfigRequest.model_validate_json(await request.body())
        form = await request.form()
        if "config" in form:
            return ConfigRequest.model_validate_json(form["config"])
        return ConfigRequest.model_validate(dict(form))
    except ValidationError as e:
        errors = [{**error, "loc": ("body", *error["loc"])} for error in e.errors(include_url=False)]
        raise RequestValidationError(errors) from e


# The routes read the body themselves to take a form too, so the schema of the JSON body is named here.
_CONFIG_BODY = {
    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ConfigRequest"}}}}
}


@router.post(
    "/preview",
    response_model=PreviewResponse,
    responses={422: {"description": "Validation Error"}},
    openapi_extra=_CONFIG_BODY,
)
async def preview(request: Request) -> Response:
    """Return the userPrefs file as an HTML fragment for htmx, else as a JSON `PreviewResponse`.

    The body is a JSON `ConfigRequest` or a form. An invalid body returns 422 with field errors.
    """
    content = generate_jsonc((await _read_config(request)).generator_input())
    return fragment_or_json(
        request, "partials/preview.html", {"content": content}, PreviewResponse(success=True, content=content)
    )


@router.post("/download", responses={422: {"description": "Validation Error"}}, openapi_extra=_CONFIG_BODY)
async def download(request: Request) -> Response:
    """Download the generated JSONC configuration file.

    The body is a JSON `ConfigRequest` or a form. An invalid body returns 422 with field errors.
    """
    config = await _read_config(request)
    return Response(
        content=generate_jsonc(config.generator_input()),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=userPrefs.jsonc"},
    )


@router.post("/preview-userprefs")
async def preview_userprefs(userPrefs: Annotated[UploadFile, File()]) -> FilePreviewResponse:
    """Preview an uploaded userPrefs.jsonc file content."""
    try:
        if not userPrefs.filename:
            return FilePreviewResponse(success=False, error="No file selected")

        # Validate file size
        contents = await userPrefs.read()
        if len(contents) > MAX_UPLOAD_SIZE:
            return FilePreviewResponse(success=False, error=f"File too large (max {MAX_UPLOAD_SIZE // 1024}KB)")

        # Validate content type
        file_content = contents.decode("utf-8")

        # Try to parse as JSON to validate
        config_lines = [
            line.strip() for line in file_content.split("\n") if line.strip() and not line.strip().startswith("//")
        ]

        checks = {
            "owner_name": "USERPREFS_OWNER_SHORT_NAME" in file_content
            or "USERPREFS_CONFIG_OWNER_SHORT_NAME" in file_content,
            "channel_name": "USERPREFS_CHANNEL_0_NAME" in file_content,
            "channel_psk": "USERPREFS_CHANNEL_0_PSK" in file_content,
            "region": "USERPREFS_CONFIG_LORA_REGION" in file_content,
            "device_role": "USERPREFS_CONFIG_DEVICE_ROLE" in file_content,
        }

        return FilePreviewResponse(
            success=True,
            content=file_content,
            line_count=len(config_lines),
            checks=checks,
            filename=userPrefs.filename,
        )
    except UnicodeDecodeError:
        return FilePreviewResponse(success=False, error="File is not valid UTF-8 text")
    except Exception as e:
        logger.error(f"File preview error: {e}")
        return FilePreviewResponse(success=False, error=str(e))
