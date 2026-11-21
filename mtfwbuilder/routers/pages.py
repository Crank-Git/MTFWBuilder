"""HTML page routes — serves Jinja2 templates."""

from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from markupsafe import Markup

from mtfwbuilder import meshtastic_enums
from mtfwbuilder.database import find_firmware_source, list_firmware_sources
from mtfwbuilder.models import CHANNEL_SLOTS
from mtfwbuilder.routers.firmware_builder import panel_context
from mtfwbuilder.services.userprefs_keys import KeyScan, find_key_scan

# A hidden and disabled fieldset or option shows nothing and sends no value with the form.
_ABSENT = Markup(" hidden disabled")

router = APIRouter(tags=["pages"])


@router.get("/")
async def index(request: Request, build: str | None = None):
    """Render the build page: the config form, the build controls and the progress panel.

    The build controls are the variant picker, the firmware version selector and the Build control.
    The selector lists the ready firmware sources, and selects the newest installed stable firmware version.
    The form hides each control and enum value that the default firmware version does not read (FR-config-coverage-16).

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
        **_version_filters(await find_key_scan(database_path)),
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


def _version_filters(scan: KeyScan | None) -> dict[str, Callable[..., Markup]]:
    """Return the template functions that hide the parts of the form that the firmware version of `scan` lacks.

    - `key_attrs(key)` is for the fieldset of the control that writes `key`.
    - `enum_attrs(value)` is for the option of an enum value.
    - `slot_attrs(n)` is for the tab and the channel count option of channel slot `n`.

    Each function gives an empty string when `scan` is None, so the form then shows everything.
    `static/js/main.js` applies the same rules when the operator chooses another firmware version.
    """
    keys = scan.keys if scan is not None else None
    enum_values = scan.enum_values if scan is not None else None

    def key_attrs(key: str) -> Markup:
        return _ABSENT if keys is not None and key not in keys else Markup()

    def enum_attrs(value: str) -> Markup:
        return _ABSENT if enum_values is not None and value not in enum_values else Markup()

    def slot_attrs(slot: int) -> Markup:
        prefix = f"USERPREFS_CHANNEL_{slot}_"
        return _ABSENT if keys is not None and not any(k.startswith(prefix) for k in keys) else Markup()

    return {"key_attrs": key_attrs, "enum_attrs": enum_attrs, "slot_attrs": slot_attrs}
