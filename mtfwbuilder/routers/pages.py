"""HTML page routes — serves Jinja2 templates."""

from fastapi import APIRouter, Request

router = APIRouter(tags=["pages"])


@router.get("/")
async def index(request: Request):
    """Render the main configuration generator page."""
    templates = request.app.state.templates
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/firmware-builder")
async def firmware_builder(request: Request):
    """Render the firmware builder page with supported device variants."""
    templates = request.app.state.templates
    registry = request.app.state.device_registry

    variants = [
        {"id": v.id, "name": v.name, "manufacturer": v.manufacturer}
        for v in registry.all_variants
    ]
    manufacturers = {}
    for v in registry.all_variants:
        if v.manufacturer not in manufacturers:
            manufacturers[v.manufacturer] = []
        manufacturers[v.manufacturer].append(
            {"id": v.id, "name": v.name, "manufacturer": v.manufacturer}
        )

    return templates.TemplateResponse(
        "firmware_builder.html",
        {"request": request, "variants": variants, "manufacturers": manufacturers},
    )
