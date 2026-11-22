"""Authentication via bcrypt + signed cookie sessions, and CSRF tokens for admin forms."""

import hmac
import secrets

import bcrypt
from fastapi import HTTPException, Request, status
from fastapi.responses import Response
from itsdangerous import BadSignature, SignatureExpired, Signer, TimestampSigner
from slowapi.util import get_remote_address

from mtfwbuilder.config import Settings
from mtfwbuilder.htmx import is_htmx
from mtfwbuilder.rate_limit import is_trusted_proxy

SESSION_COOKIE = "mtfw_session"
CSRF_COOKIE = "mtfw_csrf"
CSRF_FIELD = "csrf_token"
# A JSON route and a DELETE route have no form, so they take the CSRF token in this header.
CSRF_HEADER = "X-CSRF-Token"
# The salt keeps a CSRF token apart from a session token, which the same secret key signs.
_CSRF_SALT = "mtfw-csrf"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_session_token(settings: Settings) -> str:
    """Create a signed session token."""
    signer = TimestampSigner(settings.secret_key)
    return signer.sign("admin").decode("utf-8")


def validate_session_token(token: str, settings: Settings) -> bool:
    """Validate a signed session token."""
    signer = TimestampSigner(settings.secret_key)
    try:
        signer.unsign(token, max_age=settings.session_max_age)
        return True
    except (BadSignature, SignatureExpired):
        return False


def get_session_token(request: Request) -> str | None:
    """Extract session token from cookie."""
    return request.cookies.get(SESSION_COOKIE)


def _is_https(request: Request) -> bool:
    """Return True when the request scheme is `https`, or a trusted proxy sends `X-Forwarded-Proto: https`.

    By default, uvicorn reads `X-Forwarded-Proto` only from a loopback peer, so behind the nginx overlay
    the scheme is `http`. Only a peer in `trusted_proxies` can change the result.
    """
    if request.url.scheme == "https":
        return True
    settings: Settings = request.app.state.settings
    forwarded_proto = request.headers.get("x-forwarded-proto", "").strip().lower()
    return forwarded_proto == "https" and is_trusted_proxy(get_remote_address(request), settings)


def set_cookie(response: Response, request: Request, key: str, value: str, max_age: int | None = None) -> None:
    """Set an HttpOnly, SameSite=Lax cookie that is `Secure` only when the request came over HTTPS.

    A browser does not store a `Secure` cookie from a plain HTTP response, so a fixed `Secure` flag
    stops sign-in on a default install.
    """
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
    )


def new_csrf_nonce() -> str:
    """Return a random value for the CSRF cookie."""
    return secrets.token_urlsafe(32)


def csrf_token(nonce: str, settings: Settings) -> str:
    """Return the CSRF token for the CSRF cookie value `nonce`: the nonce, signed with the secret key."""
    return Signer(settings.secret_key, salt=_CSRF_SALT).sign(nonce).decode("utf-8")


def csrf_token_is_valid(token: str, nonce: str, settings: Settings) -> bool:
    """Return True when `token` is the signed form of the CSRF cookie value `nonce`."""
    try:
        signed_nonce = Signer(settings.secret_key, salt=_CSRF_SALT).unsign(token).decode("utf-8")
    except BadSignature:
        return False
    return hmac.compare_digest(signed_nonce, nonce)


async def require_admin(request: Request) -> None:
    """FastAPI dependency that requires a valid admin session.

    Raises:
        HTTPException: 401 when the session is absent or expired. For an htmx request, the
            `HX-Redirect` header sends the page to `/admin`, which shows the sign-in form.
    """
    settings: Settings = request.app.state.settings
    token = get_session_token(request)
    if not token or not validate_session_token(token, settings):
        # htmx follows `HX-Redirect` before it checks the status, so a 401 still reloads the page.
        headers = {"HX-Redirect": "/admin"} if is_htmx(request) else None
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
            headers=headers,
        )


async def require_csrf(request: Request) -> None:
    """FastAPI dependency that requires the CSRF token to match the CSRF cookie.

    The token is the header `X-CSRF-Token` when the request has it, else the form field `csrf_token`.

    Raises:
        HTTPException: 403 when the token or the cookie is absent, or the token does not match.
    """
    settings: Settings = request.app.state.settings
    token = request.headers.get(CSRF_HEADER)
    if token is None:
        form = await request.form()
        token = form.get(CSRF_FIELD)
    nonce = request.cookies.get(CSRF_COOKIE)
    if not isinstance(token, str) or not nonce or not csrf_token_is_valid(token, nonce, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF token missing or invalid")
