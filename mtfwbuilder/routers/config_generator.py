"""Config generator API routes — /api/v1/generate, /api/v1/preview, /api/v1/download."""

import asyncio
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from pydantic import ValidationError

from mtfwbuilder.database import find_firmware_source
from mtfwbuilder.htmx import fragment_or_json
from mtfwbuilder.models import ConfigRequest, FilePreviewResponse, PreviewResponse, UserPrefsKeys
from mtfwbuilder.services.jsonc_generator import DEFAULT_FIRMWARE_KEYS, generate_jsonc, version_errors
from mtfwbuilder.services.userprefs_keys import KeyScan, ensure_key_scan, find_key_scan

logger = logging.getLogger("mtfwbuilder.config_generator")

router = APIRouter(prefix="/api/v1", tags=["config"])

MAX_UPLOAD_SIZE = 65536  # 64KB

# Without a key scan, the generator writes the gated keys of `DEFAULT_FIRMWARE_KEYS`, the keys of v2.7.26 only.
NOTE_HEADER = "X-Userprefs-Note"
_LEFT_OUT = "keys that v2.7.26 does not read are left out."


def field_error(location: tuple[str, ...], message: str) -> dict:
    """Return one 422 error item for the body field at `location`."""
    return {"type": "value_error", "loc": ("body", *location), "msg": message}


async def key_scan(request: Request, version: str | None) -> KeyScan | None:
    """Return the key scan of `version`, or of the default firmware version when `version` is empty.

    Raises:
        RequestValidationError: `version` is not an installed firmware version.
    """
    database_path = request.app.state.settings.database_path
    if version and await find_firmware_source(database_path, version) is None:
        message = f"firmware version {version} is not installed"
        raise RequestValidationError([field_error(("firmware_version",), message)])
    return await find_key_scan(database_path, version or None)


def generate_for_version(config: ConfigRequest, scan: KeyScan | None) -> str:
    """Return the userPrefs file of `config` for the firmware version of `scan`.

    Raises:
        RequestValidationError: The firmware version rejects a field (`version_errors`).
    """
    data = config.generator_input()
    errors = version_errors(data, scan)
    if errors:
        raise RequestValidationError([field_error(location, message) for location, message in errors])
    return generate_jsonc(data, firmware_keys=scan.keys if scan is not None else DEFAULT_FIRMWARE_KEYS)


def _chosen_version(config: ConfigRequest) -> str | None:
    # `firmware_version` is not a userPrefs field, so `ConfigRequest` keeps it as an extra field.
    return (config.model_extra or {}).get("firmware_version")


async def _userprefs_file(request: Request, config: ConfigRequest) -> tuple[str, dict[str, str]]:
    """Return the userPrefs file of `config`, and the response headers that name the left-out keys.

    The headers are empty when the chosen firmware version has a key scan.

    Raises:
        RequestValidationError: The firmware version is not installed, or it rejects a field.
    """
    version = _chosen_version(config)
    scan = await key_scan(request, version)
    content = generate_for_version(config, scan)
    if scan is not None:
        return content, {}
    record = await find_firmware_source(request.app.state.settings.database_path, version)
    if record is None:
        return content, {NOTE_HEADER: f"No firmware version is installed; {_LEFT_OUT}"}
    return content, {NOTE_HEADER: f"Firmware version {record['version']} has no key scan; {_LEFT_OUT}"}


@router.post("/generate")
async def generate(config: ConfigRequest, request: Request, response: Response) -> PreviewResponse:
    """Generate JSONC configuration from form data, for the firmware version in the field `firmware_version`.

    Without a key scan of the firmware version, the header `X-Userprefs-Note` names the left-out keys.
    """
    content, headers = await _userprefs_file(request, config)
    response.headers.update(headers)
    return PreviewResponse(success=True, content=content)


@router.get("/userprefs/keys", responses={404: {"description": "Firmware version not installed"}})
async def userprefs_keys(request: Request, version: str | None = None) -> UserPrefsKeys:
    """Return the userPrefs keys and the enum values that the firmware version reads, from a scan of its source.

    Without `version`, the result is for the default firmware version. Returns 404 when the firmware version is not
    installed, or when no firmware version is installed.
    """
    record = await find_firmware_source(request.app.state.settings.database_path, version)
    if record is None:
        raise HTTPException(status_code=404, detail="Firmware version not installed")
    scan = await asyncio.to_thread(ensure_key_scan, Path(record["path"]))
    return UserPrefsKeys(
        version=record["version"],
        keys=None if scan is None else sorted(scan.keys),
        enum_values=None if scan is None or scan.enum_values is None else sorted(scan.enum_values),
    )


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

    The body is a JSON `ConfigRequest` or a form. The optional field `firmware_version` names an installed firmware
    version, and the default firmware version applies without it. A 422 with field errors answers:
    - an invalid body.
    - a firmware version that is not installed.
    - a field that the firmware version does not read.

    Without a key scan of the firmware version, the header `X-Userprefs-Note` names the left-out keys.
    """
    content, headers = await _userprefs_file(request, await _read_config(request))
    context = {"content": content, "note": headers.get(NOTE_HEADER)}
    response = fragment_or_json(
        request, "partials/preview.html", context, PreviewResponse(success=True, content=content)
    )
    response.headers.update(headers)
    return response


@router.post("/download", responses={422: {"description": "Validation Error"}}, openapi_extra=_CONFIG_BODY)
async def download(request: Request) -> Response:
    """Download the generated JSONC configuration file.

    The body is a JSON `ConfigRequest` or a form. The optional field `firmware_version` names an installed firmware
    version, and the default firmware version applies without it. A 422 with field errors answers:
    - an invalid body.
    - a firmware version that is not installed.
    - a field that the firmware version does not read.

    Without a key scan of the firmware version, the header `X-Userprefs-Note` names the left-out keys.
    """
    content, headers = await _userprefs_file(request, await _read_config(request))
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=userPrefs.jsonc", **headers},
    )


@router.post("/preview-userprefs", responses={413: {"description": "The file is larger than 64 KB"}})
async def preview_userprefs(userPrefs: Annotated[UploadFile, File()]) -> FilePreviewResponse:
    """Return the content, the line count and the key checks of an uploaded userPrefs.jsonc file.

    Raises:
        HTTPException: The file is larger than 64 KB (413), as for `POST /api/v1/import/cli-yaml`.
        RequestValidationError: The file has no name, or is not UTF-8 text (422).
    """
    if not userPrefs.filename:
        raise RequestValidationError([field_error(("userPrefs",), "No file selected")])
    contents = await userPrefs.read(MAX_UPLOAD_SIZE + 1)
    if len(contents) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_UPLOAD_SIZE // 1024} KB.")
    try:
        file_content = contents.decode("utf-8")
    except UnicodeDecodeError:
        raise RequestValidationError([field_error(("userPrefs",), "File is not valid UTF-8 text")]) from None

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
