"""HTML page routes — serves Jinja2 templates."""

from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from markupsafe import Markup

from mtfwbuilder import meshtastic_enums
from mtfwbuilder.auth import (
    CSRF_COOKIE,
    csrf_token,
    get_session_token,
    new_csrf_nonce,
    set_cookie,
    validate_session_token,
)
from mtfwbuilder.database import find_firmware_source, list_firmware_sources, list_variants
from mtfwbuilder.htmx import is_htmx
from mtfwbuilder.models import CHANNEL_SLOTS, ProfileRequest
from mtfwbuilder.routers.firmware_builder import panel_context
from mtfwbuilder.routers.profiles import is_admin
from mtfwbuilder.services import fleets, profiles
from mtfwbuilder.services.userprefs_keys import KeyScan, find_key_scan
from mtfwbuilder.time_zones import POSIX_TZ_BY_ZONE

# A hidden and disabled fieldset or option shows nothing and sends no value with the form.
_ABSENT = Markup(" hidden disabled")

router = APIRouter(tags=["pages"])


@router.get("/")
async def index(request: Request, build: str | None = None):
    """Render the build page: the config form, the build controls and the progress panel.

    The build controls are the variant picker, the firmware version selector and the Build control.
    The selector lists the installed firmware sources, and selects the newest installed stable firmware version.
    The form hides each control and enum value that the default firmware version does not read (FR-config-coverage-16).

    The profile controls save the form as a profile and load a profile into the form (FR-fleet-builds-1 and -4).

    Args:
        build: The build id of the build that the progress panel shows (FR-frontend-htmx-10).
            An unknown build id shows "Build not found."
    """
    templates = request.app.state.templates
    settings = request.app.state.settings
    database_path = settings.database_path
    session = get_session_token(request)
    # Only an admin stores secrets in a profile (FR-fleet-builds-3), and only when the profile route reads the field.
    offer_stored_secrets = (
        "stores_secrets" in ProfileRequest.model_fields
        and session is not None
        and validate_session_token(session, settings)
    )
    nonce = (request.cookies.get(CSRF_COOKIE) or new_csrf_nonce()) if offer_stored_secrets else None
    default_source = await find_firmware_source(database_path)
    context = {
        "title": "Build",
        "build_id": build,
        "region_options": meshtastic_enums.REGIONS.options(),
        "modem_preset_options": meshtastic_enums.MODEM_PRESETS.options(),
        "role_options": meshtastic_enums.ROLES.options(),
        "rebroadcast_mode_options": meshtastic_enums.REBROADCAST_MODES.options(),
        "channel_slots": CHANNEL_SLOTS,
        "gps_mode_options": meshtastic_enums.GPS_MODES.options(),
        "packet_signature_policy_options": meshtastic_enums.PACKET_SIGNATURE_POLICIES.options(),
        "router_role_message": meshtastic_enums.ROUTER_ROLE_MESSAGE,
        "tz_posix_table": POSIX_TZ_BY_ZONE,
        # A version with status `registry failed` stays in the selector, so the picker can say that its list failed.
        "firmware_sources": [
            s for s in await list_firmware_sources(database_path) if s["status"] in ("ready", "registry failed")
        ],
        "default_firmware_version": default_source["version"] if default_source else None,
        "profiles": await profiles.list_profiles(database_path),
        "stored_secrets_csrf_token": csrf_token(nonce, settings) if nonce else None,
        **_version_filters(await find_key_scan(database_path)),
    }
    ctx = request.app.state.build_queue.get(build) if build else None
    if ctx is not None:
        context.update(panel_context(ctx))
    response = templates.TemplateResponse(request, "index.html", context)
    if nonce:
        set_cookie(response, request, CSRF_COOKIE, nonce)
    return response


@router.get("/profiles")
async def profiles_page(request: Request):
    """Render the profiles page: each profile with Load, Export, Rename and Delete (FR-fleet-builds-4).

    An htmx request gets the profile list only, so the page shows the list again after a rename or a delete.
    """
    context = {"title": "Profiles", "profiles": await profiles.list_profiles(request.app.state.settings.database_path)}
    template = "partials/profile_list.html" if is_htmx(request) else "profiles.html"
    return request.app.state.templates.TemplateResponse(request, template, context)


@router.get("/fleets")
async def fleets_page(request: Request):
    """Render the fleets page: each fleet with a link to its fleet editor, and the form that creates a fleet.

    The form creates a fleet from a profile (FR-fleet-builds-10). It selects the profile and the firmware version.
    """
    database_path = request.app.state.settings.database_path
    context = {
        "title": "Fleets",
        "fleets": await fleets.list_fleets(database_path),
        "profiles": await profiles.list_profiles(database_path),
        "firmware_sources": [s for s in await list_firmware_sources(database_path) if s["status"] == "ready"],
    }
    return request.app.state.templates.TemplateResponse(request, "fleets.html", context)


@router.get("/fleets/{fleet_id}")
async def fleet_page(request: Request, fleet_id: int):
    """Render the fleet editor: the fleet device table form and the CSV box (FR-fleet-builds-12 and -13).

    The page never holds an admin key, also for an admin session, so a saved admin key shows as "Saved" only.
    The Start fleet run control asks once for each "must re-enter" secret of the profile.
    Returns 404 when no fleet has the id.
    """
    database_path = request.app.state.settings.database_path
    fleet = await fleets.get_fleet(database_path, fleet_id, None)
    if fleet is None:
        raise HTTPException(status_code=404, detail=f"Fleet {fleet_id} does not exist")
    profile = await _run_profile(request, fleet["profile_id"])
    source = await find_firmware_source(database_path, fleet["firmware_version"])
    context = {
        "title": f"Fleet {fleet['name']}",
        "fleet": fleet,
        "profile_name": profile["name"] if profile else None,
        "firmware_version": source["version"] if source else fleet["firmware_version"],
        "variants": [entry["env"] for entry in await list_variants(database_path, source["version"])] if source else [],
        "role_options": meshtastic_enums.ROLES.options(),
        "router_role_message": meshtastic_enums.ROUTER_ROLE_MESSAGE,
        "csv_header": ",".join(fleets.CSV_HEADER),
        "must_reenter": profile["must_reenter"] if profile else [],
    }
    return request.app.state.templates.TemplateResponse(request, "fleet.html", context)


async def _run_profile(request: Request, profile_id: int) -> dict | None:
    """Return the profile as a fleet run of this session reads it, so `must_reenter` names the secrets to ask for.

    A fleet run of an admin session uses the stored secrets of the profile (ADR 0007), so they are not asked for.
    The result can hold the stored secrets, so a template gets only its name and `must_reenter`.
    """
    settings = request.app.state.settings
    return await profiles.get_profile(
        settings.database_path, profile_id, settings.secret_key if is_admin(request) else None
    )


@router.get("/fleet-runs/{run_id}")
async def fleet_run_page(request: Request, run_id: int):
    """Render the fleet run page: one row for each fleet device with its build status (FR-fleet-builds-16).

    While the fleet run runs, the state polls this route each second, and an htmx request gets the state only.
    The state of an ended fleet run has no poll, Download all, and Retry failed when a build did not complete.
    Returns 404 when no fleet run has the id.
    """
    run = await request.app.state.fleet_runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Fleet run {run_id} does not exist")
    database_path = request.app.state.settings.database_path
    fleet = await fleets.get_fleet(database_path, run["fleet_id"], None)
    profile = await _run_profile(request, fleet["profile_id"]) if fleet else None
    context = {
        "title": f"Fleet run {run_id}",
        "run": run,
        "fleet": fleet,
        "complete_count": sum(device["status"] == "complete" for device in run["devices"]),
        "must_reenter": profile["must_reenter"] if profile else [],
    }
    template = "partials/fleet_run_state.html" if is_htmx(request) else "fleet_run.html"
    return request.app.state.templates.TemplateResponse(request, template, context)


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
