"""Admin routes — sign-in, the admin dashboard, firmware update, cleanup."""

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from mtfwbuilder.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_session_token,
    csrf_token,
    get_session_token,
    new_csrf_nonce,
    require_admin,
    require_csrf,
    set_cookie,
    validate_session_token,
    verify_password,
)
from mtfwbuilder.database import get_db, get_recent_builds
from mtfwbuilder.htmx import fragment_or_json
from mtfwbuilder.rate_limit import limiter
from mtfwbuilder.services.cleanup_service import cleanup_old_builds
from mtfwbuilder.services.firmware_updater import get_firmware_version, update_firmware

logger = logging.getLogger("mtfwbuilder.admin")

router = APIRouter(tags=["admin"])

RECENT_BUILD_COUNT = 10
UPDATE_SUCCEEDED = {"success": True, "message": "Firmware updated successfully."}
UPDATE_FAILED = {"success": False, "error": "Firmware update failed. Check logs for details."}


def _admin_page(request: Request, nonce: str, context: dict, status_code: int = 200) -> Response:
    """Return `admin.html` with the CSRF token for `nonce`, and set `nonce` as the CSRF cookie."""
    context = {"title": "Admin", "csrf_token": csrf_token(nonce, request.app.state.settings), **context}
    response = request.app.state.templates.TemplateResponse(request, "admin.html", context, status_code=status_code)
    set_cookie(response, request, CSRF_COOKIE, nonce)
    return response


def _sign_in_page(request: Request, nonce: str, error: str | None = None, status_code: int = 200) -> Response:
    """Return the sign-in form, with an optional error message."""
    return _admin_page(request, nonce, {"signed_in": False, "error": error}, status_code)


@router.get("/admin")
async def admin_page(request: Request):
    """Render the dashboard when a session exists, else the sign-in form."""
    settings = request.app.state.settings
    nonce = request.cookies.get(CSRF_COOKIE) or new_csrf_nonce()
    token = get_session_token(request)
    if not token or not validate_session_token(token, settings):
        return _sign_in_page(request, nonce)

    db = await get_db(settings)
    try:
        builds = await get_recent_builds(db, limit=RECENT_BUILD_COUNT)
    finally:
        await db.close()
    context = {"signed_in": True, "firmware": get_firmware_version(settings), "builds": builds}
    return _admin_page(request, nonce, context)


@router.post("/admin/login", dependencies=[Depends(require_csrf)])
@limiter.limit("10/minute")
async def admin_login(request: Request):
    """Authenticate admin and set session cookie."""
    settings = request.app.state.settings

    form = await request.form()
    password = form.get("admin_key", "")

    if not verify_password(password, settings.admin_password_hash):
        return _sign_in_page(request, request.cookies[CSRF_COOKIE], "Wrong password.", status_code=401)

    token = create_session_token(settings)
    response = RedirectResponse(url="/admin", status_code=303)
    set_cookie(response, request, SESSION_COOKIE, token, max_age=settings.session_max_age)
    # A new CSRF cookie at sign-in makes the CSRF token belong to this session only.
    set_cookie(response, request, CSRF_COOKIE, new_csrf_nonce())
    logger.info("Admin login successful")
    return response


async def rate_limit_response(request: Request, exc: RateLimitExceeded) -> Response:
    """Return the sign-in form with a wait message for `/admin/login`, else the slowapi JSON 429."""
    if request.url.path == "/admin/login":
        nonce = request.cookies.get(CSRF_COOKIE) or new_csrf_nonce()
        return _sign_in_page(request, nonce, "Too many attempts. Wait one minute.", status_code=429)
    return _rate_limit_exceeded_handler(request, exc)


@router.post("/admin/logout", dependencies=[Depends(require_csrf)])
async def admin_logout():
    """Clear admin session."""
    response = RedirectResponse(url="/admin", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return response


@router.post("/api/v1/update-firmware", dependencies=[Depends(require_admin), Depends(require_csrf)])
async def update_firmware_route(request: Request):
    """Start firmware update from GitHub (admin only)."""
    settings = request.app.state.settings

    # The update replaces the firmware source, so it waits for the running build and blocks the next one.
    lock = request.app.state.build_queue.firmware_lock
    await lock.acquire()
    update = asyncio.get_running_loop().run_in_executor(None, update_firmware, settings)
    # A cancelled request cannot stop the update thread, so the lock stays held until the thread ends.
    update.add_done_callback(lambda _future: lock.release())
    try:
        success = await asyncio.shield(update)
    except Exception as e:
        logger.error(f"Firmware update error: {e}")
        result = {"success": False, "error": str(e)}
    else:
        result = UPDATE_SUCCEEDED if success else UPDATE_FAILED
    return fragment_or_json(request, "partials/admin_result.html", result, result)


@router.post("/api/v1/cleanup", dependencies=[Depends(require_admin), Depends(require_csrf)])
async def cleanup_route(request: Request):
    """Manually trigger build cleanup (admin only)."""
    settings = request.app.state.settings
    removed = cleanup_old_builds(settings, keep=request.app.state.build_queue.unended_build_ids())
    result = {"success": True, "message": f"Removed {removed} old build directories."}
    return fragment_or_json(request, "partials/admin_result.html", result, result)
