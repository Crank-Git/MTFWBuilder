"""Responses that give an htmx request an HTML fragment and every other request JSON."""

import re
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response


def is_htmx(request: Request) -> bool:
    """Return True when htmx sent the request.

    htmx sends `HX-Request: true` on each request it makes (https://htmx.org/reference/#request_headers).
    """
    return request.headers.get("HX-Request") == "true"


def fragment_or_json(request: Request, template: str, context: dict[str, Any], data: Any) -> Response:
    """Return the template as an HTML fragment for an htmx request, else `data` as JSON.

    Args:
        request: The request. Its `HX-Request` header selects the format.
        template: The fragment template, relative to `templates/`.
        context: The template context.
        data: The JSON body, a pydantic model or another value that `jsonable_encoder` accepts.
    """
    if is_htmx(request):
        return request.app.state.templates.TemplateResponse(request, template, context)
    return JSONResponse(jsonable_encoder(data))


def _field_error(error: dict[str, Any]) -> dict[str, str]:
    """Return the location, the form field name and the message of one body field error.

    The location `channel_0.psk` matches the error slot `error-channel_0.psk` on the page.
    The name `channel_0[psk]` is the name of the form field.
    """
    loc = [str(part) for part in error["loc"]]
    if loc[:1] == ["body"]:
        loc = loc[1:]
    field = ".".join(loc)
    name = re.sub(r"^(channel_\d)\.(\w+)$", r"\1[\2]", field)
    return {"field": field, "name": name, "message": error["msg"]}


async def validation_error_response(request: Request, exc: RequestValidationError) -> Response:
    """Return a 422 fragment that puts each message under its field, or the FastAPI JSON 422.

    The fragment carries one out-of-band element for each field error, so htmx swaps the message
    into the error slot of that field (https://htmx.org/attributes/hx-swap-oob/).
    """
    if not is_htmx(request):
        return await request_validation_exception_handler(request, exc)
    errors = [_field_error(error) for error in exc.errors()]
    return request.app.state.templates.TemplateResponse(
        request, "partials/field_errors.html", {"errors": errors}, status_code=422
    )
