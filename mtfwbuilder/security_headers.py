"""Security headers on every HTTP response: Content-Security-Policy, framing, MIME sniffing, referrer, permissions."""

import secrets

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# A style attribute cannot run script, and the templates set the width of progress bars with one.
# The import map of the push config page is an inline script, so it carries the nonce of the request.
PAGE_POLICY = (
    "default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self'; style-src-attr 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'"
)

# FastAPI serves Swagger UI and ReDoc from cdn.jsdelivr.net with an inline start script, so only these paths
# get the CDN and inline sources.
DOCS_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc"})
DOCS_POLICY = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https://fastapi.tiangolo.com https://cdn.redoc.ly; "
    "worker-src blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)

# Flashing and push config use Web Serial. Linux Chromium 159 reports `bluetooth` as an unknown feature on the
# console, so the policy leaves it out, and Web Bluetooth keeps its default allowlist of the page origin.
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), hid=(), "
    "magnetometer=(), microphone=(), payment=(), serial=(self), usb=()"
)


class SecurityHeadersMiddleware:
    """Add the security headers to each HTTP response, and put a new CSP nonce in `request.state.csp_nonce`.

    A response that sets its own Content-Security-Policy keeps it, for example a device image response.
    The middleware is plain ASGI, so it does not hold back the progress streams.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        nonce = secrets.token_urlsafe(16)
        scope.setdefault("state", {})["csp_nonce"] = nonce
        policy = DOCS_POLICY if scope["path"] in DOCS_PATHS else PAGE_POLICY.format(nonce=nonce)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", policy)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("Referrer-Policy", "same-origin")
                headers.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
            await send(message)

        await self.app(scope, receive, send_with_headers)
