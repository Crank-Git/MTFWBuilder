"""Authentication via bcrypt + signed cookie sessions."""

from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from itsdangerous import TimestampSigner, BadSignature, SignatureExpired

from mtfwbuilder.config import Settings

SESSION_COOKIE = "mtfw_session"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


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


def get_session_token(request: Request) -> Optional[str]:
    """Extract session token from cookie."""
    return request.cookies.get(SESSION_COOKIE)


async def require_admin(request: Request) -> None:
    """FastAPI dependency that requires a valid admin session."""
    settings: Settings = request.app.state.settings
    token = get_session_token(request)
    if not token or not validate_session_token(token, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required",
        )
