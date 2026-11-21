"""Profile API routes: save, load, rename, export as JSON and delete a profile (feature fleet-builds)."""

import re

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from mtfwbuilder.auth import get_session_token, require_csrf, validate_session_token
from mtfwbuilder.models import Profile, ProfileRequest, ProfileUpdate
from mtfwbuilder.services import profiles

router = APIRouter(prefix="/api/v1", tags=["profiles"])

# A profile name is free text, and a quote or a path separator in the file name breaks the header or the download.
_UNSAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9_.-]")
# A response with stored secrets must not stay in the browser cache, because browser storage holds no secret.
NO_STORE = {"Cache-Control": "no-store"}


def _not_found(profile_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Profile {profile_id} does not exist")


def _duplicate(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"A profile with the name {name} exists")


def _is_admin(request: Request) -> bool:
    token = get_session_token(request)
    return bool(token) and validate_session_token(token, request.app.state.settings)


async def _require_admin_for_secrets(request: Request) -> None:
    """Raise 403 unless the request has an admin session and a valid CSRF token.

    Raises:
        HTTPException: 403 when the admin session is absent or expired, or the CSRF token is invalid.
    """
    if not _is_admin(request):
        raise HTTPException(
            status_code=403, detail="An admin session is required to store or remove the secrets of a profile"
        )
    await require_csrf(request)


async def _load(request: Request, profile_id: int) -> dict | None:
    """Return the profile, with the stored secrets only for an admin session."""
    settings = request.app.state.settings
    admin = _is_admin(request)
    profile = await profiles.get_profile(settings.database_path, profile_id, settings.secret_key if admin else None)
    if profile is not None and profile["stores_secrets"] and not admin:
        profile["warning"] = profiles.SIGN_IN_FOR_SECRETS
    return profile


@router.get("/profiles", response_model=list[Profile])
async def get_profiles(request: Request):
    """Return every profile in name order, without secrets, also for an admin session."""
    return await profiles.list_profiles(request.app.state.settings.database_path)


def _check_secrets_change(saved_stores: bool, body: ProfileUpdate) -> bool:
    """Return True when the change stores or removes secrets, so it needs an admin session.

    Raises:
        HTTPException: 422 when the change turns on `stores_secrets` without a config, because no secret comes in.
    """
    if body.stores_secrets and body.config is None and not saved_stores:
        error = {"loc": ["body", "config"], "msg": "a config is required to turn on stores_secrets", "type": "missing"}
        raise HTTPException(status_code=422, detail=[error])
    stores = saved_stores if body.stores_secrets is None else body.stores_secrets
    return (stores or saved_stores) and (body.config is not None or body.stores_secrets is not None)


@router.post("/profiles", status_code=201, response_model=Profile)
async def post_profile(body: ProfileRequest, request: Request, response: Response):
    """Save the config as a profile (FR-fleet-builds-1 to -3).

    Without `stores_secrets`, the save removes the secrets, and `must_reenter` names each removed field. With
    `stores_secrets` true, the profile keeps the secrets encrypted. Returns:

    - 403 when `stores_secrets` is true without an admin session and a valid CSRF token.
    - 409 when another profile has the name.
    - 422 with field errors when `ConfigRequest` rejects the config.
    """
    if body.stores_secrets:
        await _require_admin_for_secrets(request)
    response.headers.update(NO_STORE)
    settings = request.app.state.settings
    try:
        profile_id = await profiles.create_profile(settings.database_path, settings.secret_key, body)
    except profiles.DuplicateProfileName:
        raise _duplicate(body.name) from None
    return await _load(request, profile_id)


@router.get("/profiles/{profile_id}", response_model=Profile)
async def get_profile(profile_id: int, request: Request, response: Response):
    """Return the profile, or 404 when no profile has the id.

    The profile holds its stored secrets only for an admin session. Without one, `must_reenter` names the
    stored secrets and `warning` asks for an admin sign-in. When the stored secrets do not decrypt with the
    current `secret_key`, `must_reenter` names them and `warning` says so.
    """
    profile = await _load(request, profile_id)
    if profile is None:
        raise _not_found(profile_id)
    response.headers.update(NO_STORE)
    return profile


@router.put("/profiles/{profile_id}", response_model=Profile)
async def put_profile(profile_id: int, body: ProfileUpdate, request: Request, response: Response):
    """Rename the profile, replace its config, change its store secrets option, or more of them (FR-fleet-builds-4).

    A new config replaces the saved config in full. Returns:

    - 403 when the change stores or removes secrets without an admin session and a valid CSRF token.
    - 404 when no profile has the id.
    - 409 when another profile has the new name.
    - 422 when the change turns on `stores_secrets` without a config.
    """
    settings = request.app.state.settings
    saved = await profiles.get_profile(settings.database_path, profile_id, None)
    if saved is None:
        raise _not_found(profile_id)
    if _check_secrets_change(saved["stores_secrets"], body):
        await _require_admin_for_secrets(request)
    try:
        found = await profiles.update_profile(settings.database_path, settings.secret_key, profile_id, body)
    except profiles.DuplicateProfileName:
        raise _duplicate(body.name) from None
    if not found:
        raise _not_found(profile_id)
    response.headers.update(NO_STORE)
    return await _load(request, profile_id)


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: int, request: Request) -> Response:
    """Delete the profile, or return 404 when no profile has the id."""
    if not await profiles.delete_profile(request.app.state.settings.database_path, profile_id):
        raise _not_found(profile_id)
    return Response(status_code=204)


@router.get("/profiles/{profile_id}/export")
async def export_profile(profile_id: int, request: Request) -> JSONResponse:
    """Send the profile as a JSON file with `name`, `config` and `must_reenter`, and without secrets."""
    # An export file leaves the server, so it never holds a secret or the encrypted secrets.
    profile = await profiles.get_profile(request.app.state.settings.database_path, profile_id, None)
    if profile is None:
        raise _not_found(profile_id)
    content = {key: profile[key] for key in ("name", "config", "must_reenter")}
    file_name = f"profile-{_UNSAFE_FILE_NAME.sub('_', profile['name'])}.json"
    return JSONResponse(content, headers={"Content-Disposition": f'attachment; filename="{file_name}"'})
