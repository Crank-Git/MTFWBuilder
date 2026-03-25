"""Admin routes — login, firmware update, cleanup, system info."""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from mtfwbuilder.auth import (
    SESSION_COOKIE,
    create_session_token,
    require_admin,
    verify_password,
)
from mtfwbuilder.services.cleanup_service import cleanup_old_builds
from mtfwbuilder.services.firmware_updater import get_firmware_version, update_firmware

logger = logging.getLogger("mtfwbuilder.admin")

router = APIRouter(tags=["admin"])


@router.get("/admin")
async def admin_page(request: Request):
    """Render the admin dashboard."""
    templates = request.app.state.templates
    return templates.TemplateResponse("admin.html", {"request": request, "title": "Admin Dashboard"})


@router.post("/admin/login")
async def admin_login(request: Request):
    """Authenticate admin and set session cookie."""
    settings = request.app.state.settings

    form = await request.form()
    password = form.get("admin_key", "")

    if not verify_password(password, settings.admin_password_hash):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = create_session_token(settings)
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_max_age,
        httponly=True,
        samesite="lax",
    )
    logger.info("Admin login successful")
    return response


@router.post("/admin/logout")
async def admin_logout():
    """Clear admin session."""
    response = RedirectResponse(url="/admin", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.post("/api/v1/update-firmware", dependencies=[Depends(require_admin)])
async def update_firmware_route(request: Request):
    """Start firmware update from GitHub (admin only)."""
    settings = request.app.state.settings

    # Run firmware update in a thread (blocking I/O)
    loop = asyncio.get_event_loop()
    try:
        success = await loop.run_in_executor(None, update_firmware, settings)
    except Exception as e:
        logger.error(f"Firmware update error: {e}")
        return {"success": False, "error": str(e)}

    if success:
        return {"success": True, "message": "Firmware updated successfully."}
    else:
        return {"success": False, "error": "Firmware update failed. Check logs for details."}


@router.post("/api/v1/cleanup", dependencies=[Depends(require_admin)])
async def cleanup_route(request: Request):
    """Manually trigger build cleanup (admin only)."""
    settings = request.app.state.settings
    removed = cleanup_old_builds(settings)
    return {"success": True, "message": f"Removed {removed} old build directories."}
