"""Admin routes — login, firmware update, cleanup, system info."""

import logging

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from mtfwbuilder.auth import (
    SESSION_COOKIE,
    create_session_token,
    require_admin,
    verify_password,
)
from mtfwbuilder.rate_limit import limiter
from mtfwbuilder.services import firmware_updater
from mtfwbuilder.services.cleanup_service import cleanup_old_builds

logger = logging.getLogger("mtfwbuilder.admin")

router = APIRouter(tags=["admin"])


@router.get("/admin")
async def admin_page(request: Request):
    """Render the admin dashboard."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "admin.html", {"title": "Admin Dashboard"})


@router.post("/admin/login")
@limiter.limit("10/minute")
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
        secure=not settings.debug,
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
    """Install the newest stable firmware version, and return the result after the install ends (admin only).

    This old route stays for the admin page until #38. It starts the same install as
    `POST /api/v1/firmware/install` with `{"channel": "stable"}`, and waits for it.
    """
    installs: firmware_updater.FirmwareInstalls = request.app.state.firmware_installs
    try:
        install = await installs.start(channel="stable")
    except firmware_updater.FirmwareVersionInstalled as e:
        return {"success": True, "message": f"Firmware version {e} is installed."}
    except firmware_updater.InstallActive as e:
        install = e.install
    except (firmware_updater.ReleaseNotFound, requests.RequestException) as e:
        logger.error(f"No stable firmware version: {e}")
        return {"success": False, "error": "Cannot get the firmware version list from GitHub."}
    # The install is a task of its own, so a closed request does not stop it.
    await install.wait()
    if install.status == "ready":
        return {"success": True, "message": f"Firmware version {install.version} is installed."}
    return {"success": False, "error": install.error}


@router.post("/api/v1/cleanup", dependencies=[Depends(require_admin)])
async def cleanup_route(request: Request):
    """Manually trigger build cleanup (admin only)."""
    settings = request.app.state.settings
    removed = cleanup_old_builds(settings, keep=request.app.state.build_queue.unended_build_ids())
    return {"success": True, "message": f"Removed {removed} old build directories."}
