"""Variants API routes: the registry of a firmware version and its device images (feature 10)."""

import re
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from mtfwbuilder.database import find_firmware_source, list_firmware_sources, list_variants
from mtfwbuilder.htmx import is_htmx
from mtfwbuilder.models import RegistryEntry

router = APIRouter(prefix="/api/v1", tags=["variants"])

# The interface of feature 10 sends SVG and PNG only, so no other file of the firmware source can leave it.
IMAGE_TYPES = {".svg": "image/svg+xml", ".png": "image/png"}
# An SVG can hold a script, and this policy stops it when a browser opens the image URL as a page.
IMAGE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
}


def search_text(text: str) -> str:
    """Return `text` in lower case without punctuation or spaces, so "heltec v4" and `heltec-v4` give one value."""
    return re.sub(r"[\W_]", "", text.lower())


def image_path(source_dir: Path, image: str | None) -> Path | None:
    """Return the path of the image file in the firmware source, or None when the source does not hold it.

    `image` comes from upstream `custom_meshtastic_images`. A value that resolves outside `source_dir`, or that is
    not an SVG or PNG file, gives None.
    """
    if not image:
        return None
    root = source_dir.resolve()
    path = (root / image).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
        return None
    return path


async def _source(request: Request, version: str | None) -> dict | None:
    """Return the firmware source record of `version`, or of the default firmware version when `version` is None.

    Raises HTTP 404 when no firmware source record has the version `version`.
    """
    database_path = request.app.state.settings.database_path
    if version is None:
        return await find_firmware_source(database_path)
    # A firmware source with status `registry failed` has no registry, and the list of it is empty, not 404.
    source = next((s for s in await list_firmware_sources(database_path) if s["version"] == version), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Firmware version {version} is not installed")
    return source


def _matches(entry: dict, query: str, arch: list[str] | None, experimental: bool) -> bool:
    if entry["support_level"] == "extra" and not experimental:
        return False
    if arch and entry["architecture"] not in arch:
        return False
    return any(query in search_text(entry[name]) for name in ("display_name", "env", "manufacturer"))


@router.get("/variants", response_model=list[RegistryEntry])
async def get_variants(
    request: Request,
    version: str | None = None,
    q: str = "",
    arch: Annotated[list[str] | None, Query()] = None,
    experimental: bool = False,
):
    """Return the registry entries of the firmware version that match the filters, in env order.

    Args:
        version: The firmware version. Without it, the result is the registry of the default firmware version.
        q: Text that the display name, the env or the manufacturer contains, without case and punctuation.
        arch: The architectures to keep. Give the parameter once for each architecture.
        experimental: When true, the result also holds the variants with support level `extra`.

    Returns an empty list when no firmware version is installed. Returns 404 for a `version` that is not installed.
    An htmx request gets the device picker list as HTML (FR-generated-registry-8 to -12).
    """
    source = await _source(request, version)
    registry = await list_variants(request.app.state.settings.database_path, source["version"]) if source else []
    query = search_text(q)
    entries = []
    for entry in registry:
        if _matches(entry, query, arch, experimental):
            has_image = image_path(Path(source["path"]), entry["image"]) is not None
            image_query = urlencode({"version": source["version"]})
            url = f"/api/v1/variants/{quote(entry['env'])}/image?{image_query}" if has_image else None
            entries.append(RegistryEntry(**entry, image_url=url))
    if not is_htmx(request):
        return entries
    context = {
        "source": source,
        "q": q,
        "arch": arch or [],
        "architectures": sorted({entry["architecture"] for entry in registry}),
        "manufacturers": _by_manufacturer(entries),
    }
    return request.app.state.templates.TemplateResponse(request, "partials/device_list.html", context)


def _by_manufacturer(entries: list[RegistryEntry]) -> dict[str, list[RegistryEntry]]:
    """Return the entries in groups by manufacturer, in name order, with the group "Other" last.

    "Other" holds the variants without a manufacturer tag (FR-generated-registry-4), so it is not a name to sort.
    """
    groups: dict[str, list[RegistryEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.manufacturer, []).append(entry)
    return {name: groups[name] for name in sorted(groups, key=lambda name: (name == "Other", name.casefold()))}


@router.get("/variants/{env}/image")
async def get_variant_image(env: str, request: Request, version: str | None = None) -> FileResponse:
    """Send the device image of the env from the firmware source, as SVG or PNG.

    Without `version`, the image comes from the default firmware version. Returns 404 when the version is not
    installed, when the env is not in its registry, or when the firmware source does not hold the image file.
    """
    source = await _source(request, version)
    entries = await list_variants(request.app.state.settings.database_path, source["version"]) if source else []
    # The env only selects a registry entry, so a path in `env` never reaches the file system.
    entry = next((e for e in entries if e["env"] == env), None)
    path = image_path(Path(source["path"]), entry["image"]) if entry else None
    if path is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path, media_type=IMAGE_TYPES[path.suffix.lower()], headers=IMAGE_HEADERS)
