"""Rate limiting configuration — shared limiter instance."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Shared limiter instance used by route decorators
limiter = Limiter(key_func=get_remote_address)
