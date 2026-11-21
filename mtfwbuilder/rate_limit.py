"""Rate limiting: the shared limiter, its client key and its limits."""

from ipaddress import ip_address

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from mtfwbuilder.config import Settings

# slowapi calls a limit function with no arguments, so the limits cannot come from the request.
_active = {"settings": Settings()}


def configure_rate_limits(settings: Settings) -> None:
    """Make the limiter use the limits and the trusted proxies of `settings`."""
    _active["settings"] = settings


def build_rate_limit() -> str:
    """Return the limit for the build route."""
    return _active["settings"].build_rate_limit


def login_rate_limit() -> str:
    """Return the limit for the login route."""
    return _active["settings"].login_rate_limit


def _is_trusted(address: str) -> bool:
    try:
        ip = ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in _active["settings"].trusted_proxies)


def client_address(request: Request) -> str:
    """Return the address that the limiter counts requests against.

    The result is the peer address, unless the peer is a trusted proxy. For a trusted proxy,
    the result is the rightmost `X-Forwarded-For` hop that is not a trusted proxy.
    """
    peer = get_remote_address(request)
    if not _is_trusted(peer):
        return peer
    hops = [hop.strip() for value in request.headers.getlist("x-forwarded-for") for hop in value.split(",")]
    hops = [hop for hop in hops if hop]
    # Each trusted proxy appends the address it received from, so only the hops to the right are reliable.
    for hop in reversed(hops):
        if not _is_trusted(hop):
            return hop
    return hops[0] if hops else peer


limiter = Limiter(key_func=client_address)
