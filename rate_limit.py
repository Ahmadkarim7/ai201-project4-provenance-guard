"""
Rate limiting for the /submit endpoint.

Real implementation uses Flask-Limiter (per requirements.txt). If it isn't
installed — e.g. in an offline sandbox — a simple in-memory fallback limiter
is used instead so the endpoint's rate-limiting *behavior* can still be
tested. The fallback is NOT a replacement for Flask-Limiter in production;
it exists only so this can be verified without network access to pip.

Chosen limits: 10 per minute, 100 per day, applied per remote address.
Reasoning is documented in README.md.
"""

import time
from collections import defaultdict
from functools import wraps

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    FLASK_LIMITER_AVAILABLE = True
except ImportError:
    FLASK_LIMITER_AVAILABLE = False
    print("WARNING: flask-limiter not installed — using dev fallback rate "
          "limiter. Install flask-limiter per requirements.txt for real use.")


def build_limiter(app):
    """
    Returns a real Flask-Limiter instance configured with our chosen limits.
    Only call this if FLASK_LIMITER_AVAILABLE is True.
    """
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=[],
        storage_uri="memory://",
    )
    return limiter


# ---------------------------------------------------------------------------
# DEV FALLBACK — simple in-memory sliding-window limiter, per-IP/remote-addr.
# Mirrors the same limits (10/minute, 100/day) so behavior can be tested
# without flask-limiter installed. NOT for production use.
# ---------------------------------------------------------------------------

_request_log = defaultdict(list)

MINUTE_LIMIT = 10
DAY_LIMIT = 100


def dev_fallback_rate_limit(get_key):
    """
    Decorator factory. get_key(request) -> str, used to identify the caller
    (e.g. remote address). Returns 429 if either limit is exceeded.
    """
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            from flask import request, jsonify
            key = get_key(request)
            now = time.time()

            _request_log[key] = [t for t in _request_log[key] if now - t < 86400]

            recent_minute = [t for t in _request_log[key] if now - t < 60]
            if len(recent_minute) >= MINUTE_LIMIT:
                return jsonify({"error": "Rate limit exceeded: 10 per minute"}), 429

            if len(_request_log[key]) >= DAY_LIMIT:
                return jsonify({"error": "Rate limit exceeded: 100 per day"}), 429

            _request_log[key].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator
