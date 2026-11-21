"""Channel share API routes — /api/v1/import/channel-url."""

from fastapi import APIRouter
from fastapi.exceptions import RequestValidationError

from mtfwbuilder.models import ChannelUrlImport, ChannelUrlRequest
from mtfwbuilder.routers.config_generator import field_error
from mtfwbuilder.services.channel_share import INVALID_URL_MESSAGE, InvalidChannelUrl, decode_channel_url

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
