"""Firmware version routes: the installed firmware sources."""

from fastapi import APIRouter, Request

from mtfwbuilder.database import list_firmware_sources
from mtfwbuilder.models import FirmwareSource

router = APIRouter(prefix="/api/v1/firmware", tags=["firmware versions"])


@router.get("", response_model=list[FirmwareSource])
async def list_installed(request: Request):
    """Return the installed firmware versions, the newest installed first."""
    return await list_firmware_sources(request.app.state.settings.database_path)
