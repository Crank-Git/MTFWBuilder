"""Channel share API routes — /api/v1/import/channel-url, /api/v1/import/cli-yaml and /api/v1/export/channel-url."""

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError

from mtfwbuilder.models import ChannelUrlExport, ChannelUrlImport, ChannelUrlRequest, CliYamlImport, ConfigRequest
from mtfwbuilder.routers.config_generator import field_error
from mtfwbuilder.services.channel_share import (
    INVALID_FILE_MESSAGE,
    INVALID_URL_MESSAGE,
    MAX_EXPORT_BYTES,
    InvalidChannelUrl,
    InvalidExportFile,
    decode_channel_url,
    encode_channel_url,
    qr_svg,
    read_cli_export,
)

NO_CHANNEL_MESSAGE = "the config writes no channel, so the channel URL has no channel"

router = APIRouter(prefix="/api/v1", tags=["channel-share"])


@router.post("/import/channel-url")
async def import_channel_url(body: ChannelUrlRequest) -> ChannelUrlImport:
    """Return the channels and the LoRa settings of a channel URL. The server keeps no copy of the URL.

    Raises:
        RequestValidationError: The URL is not a Meshtastic channel URL (422).
    """
    try:
        return decode_channel_url(body.url)
    except InvalidChannelUrl:
        raise RequestValidationError([field_error(("url",), INVALID_URL_MESSAGE)]) from None


@router.post("/import/cli-yaml", responses={413: {"description": "The file is larger than 64 KB"}})
async def import_cli_yaml(file: Annotated[UploadFile, File()]) -> CliYamlImport:
    """Return the form fields of a `meshtastic --export-config` file. The server keeps no copy of the file.

    Raises:
        HTTPException: The file is larger than 64 KB (413).
        RequestValidationError: The file is not a YAML mapping that `yaml.safe_load` reads (422).
    """
    data = await file.read(MAX_EXPORT_BYTES + 1)
    if len(data) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail=f"The file is larger than {MAX_EXPORT_BYTES // 1024} KB.")
    try:
        return read_cli_export(data)
    except InvalidExportFile:
        raise RequestValidationError([field_error(("file",), INVALID_FILE_MESSAGE)]) from None


@router.post("/export/channel-url")
async def export_channel_url(body: ConfigRequest) -> ChannelUrlExport:
    """Return the channel URL of the channels and the LoRa settings in the config, and an SVG QR code of the URL.

    Raises:
        RequestValidationError: The config is not valid, or it writes no channel (422).
    """
    if int(body.channels_to_write) == 0:
        raise RequestValidationError([field_error(("channels_to_write",), NO_CHANNEL_MESSAGE)])
    url = encode_channel_url(body)
    return ChannelUrlExport(url=url, qr_svg=qr_svg(url))
