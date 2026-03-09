"""
Rate-limiting middleware powered by slowapi.

Protects all endpoints against brute-force / abuse.
Limits are configurable via environment variables.
"""

from __future__ import annotations

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Default: 60 requests per minute per IP
DEFAULT_RATE = os.getenv("RATE_LIMIT", "60/minute")

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[DEFAULT_RATE],
    storage_uri=os.getenv("RATE_LIMIT_STORAGE", "memory://"),
)
