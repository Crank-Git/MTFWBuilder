"""Responses that give an htmx request an HTML fragment and every other request JSON."""

import re
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response


def is_htmx(request: Request) -> bool:
    """Return True when htmx sent the request.

    htmx sends `HX-Request: true` on each request it makes (https://htmx.org/reference/#request_headers).
    """
    return request.headers.get("HX-Request") == "true"


def fragment_or_json(
    request: Request, template: str, context: dict[str, Any], data: Any, status_code: int = 200
) -> Response:
    """Return the template as an HTML fragment for an htmx request, else `data` as JSON.

    Args:
        request: The request. Its `HX-Request` header selects the format.
        template: The fragment template, relative to `templates/`.
        context: The template context.
        data: The JSON body, a pydantic model or another value that `jsonable_encoder` accepts.
        status_code: The status code of both formats.
    """
    if is_htmx(request):
        return request.app.state.templates.TemplateResponse(request, template, context, status_code=status_code)
    return JSONResponse(jsonable_encoder(data), status_code=status_code)


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
    """Return a 422 fragment that puts each message under its field, or a JSON 422 with `loc`, `msg` and `type`.

    The fragment carries one out-of-band element for each field error, so htmx swaps the message
    into the error slot of that field (https://htmx.org/attributes/hx-swap-oob/).
    """
    if not is_htmx(request):
        # Pydantic puts the submitted value in `input` and `ctx`, and the value can be a PSK or another secret.
        detail = [{"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()]
        return JSONResponse(jsonable_encoder({"detail": detail}), status_code=422)
    errors = [_field_error(error) for error in exc.errors()]
    return request.app.state.templates.TemplateResponse(
        request, "partials/field_errors.html", {"errors": errors}, status_code=422
    )
