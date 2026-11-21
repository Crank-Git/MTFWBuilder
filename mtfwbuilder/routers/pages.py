"""HTML page routes — serves Jinja2 templates."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from mtfwbuilder import meshtastic_enums
from mtfwbuilder.routers.firmware_builder import panel_context

router = APIRouter(tags=["pages"])


@router.get("/")
async def index(request: Request, build: str | None = None):
    """Render the build page: the config form, the variant picker, the Build control and the progress panel.

    Args:
        build: The build id of the build that the progress panel shows (FR-frontend-htmx-10).
            An unknown build id shows "Build not found."
    """
    templates = request.app.state.templates
    manufacturers: dict[str, list] = {}
    for variant in request.app.state.device_registry.all_variants:
        manufacturers.setdefault(variant.manufacturer, []).append(variant)
    context = {
        "title": "Build",
        "manufacturers": manufacturers,
        "build_id": build,
        "region_options": meshtastic_enums.REGIONS.options(),
        "modem_preset_options": meshtastic_enums.MODEM_PRESETS.options(),
        "role_options": meshtastic_enums.ROLES.options(),
        "gps_mode_options": meshtastic_enums.GPS_MODES.options(),
        "router_role_message": meshtastic_enums.ROUTER_ROLE_MESSAGE,
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
