"""Config generator API routes — /api/v1/generate, /api/v1/preview, /api/v1/download."""

import json
import logging

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse, Response

from mtfwbuilder.models import PreviewResponse, FilePreviewResponse
from mtfwbuilder.services.jsonc_generator import generate_jsonc

logger = logging.getLogger("mtfwbuilder.config_generator")

router = APIRouter(prefix="/api/v1", tags=["config"])

MAX_UPLOAD_SIZE = 65536  # 64KB


@router.post("/generate")
async def generate(request: Request) -> PreviewResponse:
    """Generate JSONC configuration from form data."""
    try:
        form_data = await request.json()
        jsonc_content = generate_jsonc(form_data)

        if form_data.get("preview_only", False):
            return PreviewResponse(success=True, content=jsonc_content)

        return PreviewResponse(success=True, content=jsonc_content)
    except Exception as e:
        logger.error(f"Config generation error: {e}")
        return PreviewResponse(success=False, error=str(e))


@router.post("/preview")
async def preview(request: Request) -> PreviewResponse:
    """Generate a preview of the JSONC configuration."""
    try:
        form_data = await request.json()
        jsonc_content = generate_jsonc(form_data)
        return PreviewResponse(success=True, content=jsonc_content)
    except Exception as e:
        logger.error(f"Preview error: {e}")
        return PreviewResponse(success=False, error=str(e))


@router.post("/download")
async def download(request: Request) -> Response:
    """Download the generated JSONC configuration file."""
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            form_data = await request.json()
        else:
            form = await request.form()
            config_str = form.get("config", "{}")
            form_data = json.loads(config_str)

        jsonc_content = generate_jsonc(form_data)

        return Response(
            content=jsonc_content,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=userPrefs.jsonc"},
        )
    except Exception as e:
        logger.error(f"Download error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/preview-userprefs")
async def preview_userprefs(userPrefs: UploadFile = File(...)) -> FilePreviewResponse:
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
            line.strip()
            for line in file_content.split("\n")
            if line.strip() and not line.strip().startswith("//")
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
