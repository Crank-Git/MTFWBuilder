"""HTML page routes — serves Jinja2 templates."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from mtfwbuilder import meshtastic_enums
from mtfwbuilder.database import find_firmware_source, list_firmware_sources
from mtfwbuilder.models import CHANNEL_SLOTS
from mtfwbuilder.routers.firmware_builder import panel_context

router = APIRouter(tags=["pages"])


@router.get("/")
async def index(request: Request, build: str | None = None):
    """Render the build page: the config form, the build controls and the progress panel.

    The build controls are the variant picker, the firmware version selector and the Build control.
    The selector lists the ready firmware sources, and selects the newest installed stable firmware version.

    Args:
        build: The build id of the build that the progress panel shows (FR-frontend-htmx-10).
            An unknown build id shows "Build not found."
    """
    templates = request.app.state.templates
    manufacturers: dict[str, list] = {}
    for variant in request.app.state.device_registry.all_variants:
        manufacturers.setdefault(variant.manufacturer, []).append(variant)
    database_path = request.app.state.settings.database_path
    default_source = await find_firmware_source(database_path)
    context = {
        "title": "Build",
        "manufacturers": manufacturers,
        "build_id": build,
        "region_options": meshtastic_enums.REGIONS.options(),
        "modem_preset_options": meshtastic_enums.MODEM_PRESETS.options(),
        "role_options": meshtastic_enums.ROLES.options(),
        "rebroadcast_mode_options": meshtastic_enums.REBROADCAST_MODES.options(),
        "channel_slots": CHANNEL_SLOTS,
        "gps_mode_options": meshtastic_enums.GPS_MODES.options(),
        "packet_signature_policy_options": meshtastic_enums.PACKET_SIGNATURE_POLICIES.options(),
        "router_role_message": meshtastic_enums.ROUTER_ROLE_MESSAGE,
        "firmware_sources": [s for s in await list_firmware_sources(database_path) if s["status"] == "ready"],
        "default_firmware_version": default_source["version"] if default_source else None,
    }
    ctx = request.app.state.build_queue.get(build) if build else None
    if ctx is not None:
        context.update(panel_context(ctx))
    return templates.TemplateResponse(request, "index.html", context)


@router.get("/firmware-builder")
async def firmware_builder() -> RedirectResponse:
    """Redirect the old firmware builder page to the build page.

    308 keeps old bookmarks working (FR-frontend-htmx-4).
    """
    return RedirectResponse("/", status_code=308)
