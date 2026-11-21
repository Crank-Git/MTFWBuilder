"""Profile API routes: save, load, rename, export as JSON and delete a profile (feature fleet-builds)."""

import re

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from mtfwbuilder.models import Profile, ProfileRequest, ProfileUpdate
from mtfwbuilder.services import profiles

router = APIRouter(prefix="/api/v1", tags=["profiles"])

# A profile name is free text, and a quote or a path separator in the file name breaks the header or the download.
_UNSAFE_FILE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _not_found(profile_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Profile {profile_id} does not exist")


def _duplicate(name: str) -> HTTPException:
    return HTTPException(status_code=409, detail=f"A profile with the name {name} exists")


@router.get("/profiles", response_model=list[Profile])
async def get_profiles(request: Request):
    """Return every profile in name order, with the stored secrets of each profile that stores secrets."""
    settings = request.app.state.settings
    return await profiles.list_profiles(settings.database_path, settings.secret_key)


@router.post("/profiles", status_code=201, response_model=Profile)
async def post_profile(body: ProfileRequest, request: Request):
    """Save the config as a profile (FR-fleet-builds-1 to -3).

    Without `stores_secrets`, the save removes the secrets, and `must_reenter` names each removed field. With
    `stores_secrets` true, the profile keeps the secrets encrypted. Returns 409 when another profile has the
    name, and 422 with field errors when `ConfigRequest` rejects the config.
    """
    settings = request.app.state.settings
    try:
        return await profiles.create_profile(settings.database_path, settings.secret_key, body)
    except profiles.DuplicateProfileName:
        raise _duplicate(body.name) from None


@router.get("/profiles/{profile_id}", response_model=Profile)
async def get_profile(profile_id: int, request: Request):
    """Return the profile with its stored secrets, or 404 when no profile has the id.

    When the stored secrets do not decrypt with the current `secret_key`, `must_reenter` names them and `warning`
    says so.
    """
    settings = request.app.state.settings
    profile = await profiles.get_profile(settings.database_path, profile_id, settings.secret_key)
    if profile is None:
        raise _not_found(profile_id)
    return profile


@router.put("/profiles/{profile_id}", response_model=Profile)
async def put_profile(profile_id: int, body: ProfileUpdate, request: Request):
    """Rename the profile, replace its config, change its store secrets option, or more of them (FR-fleet-builds-4).

    A new config replaces the saved config in full. Returns 404 when no profile has the id, and 409 when another
    profile has the new name.
    """
    settings = request.app.state.settings
    try:
        profile = await profiles.update_profile(settings.database_path, settings.secret_key, profile_id, body)
    except profiles.DuplicateProfileName:
        raise _duplicate(body.name) from None
    if profile is None:
        raise _not_found(profile_id)
    return profile


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
