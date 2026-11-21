"""HTML page routes — serves Jinja2 templates."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["pages"])


@router.get("/")
async def index(request: Request):
    """Render the build page: the config form, the variant picker and the Build control."""
    templates = request.app.state.templates
    manufacturers: dict[str, list] = {}
    for variant in request.app.state.device_registry.all_variants:
        manufacturers.setdefault(variant.manufacturer, []).append(variant)
    return templates.TemplateResponse(request, "index.html", {"title": "Build", "manufacturers": manufacturers})


@router.get("/firmware-builder")
async def firmware_builder() -> RedirectResponse:
    """Redirect the old firmware builder page to the build page.

    308 keeps old bookmarks working (FR-frontend-htmx-4).
    """
    return RedirectResponse("/", status_code=308)
