"""Optional shared-PIN gate for the HMI (no user store — LAN LAN internal).

When ``hmi_pin`` is a non-"none" string, the browser must POST it to
``/api/auth/pin`` once; on success the server returns a short-lived HMAC-signed
token that the client supplies via ``Authorization: Bearer <token>`` on later
requests / SSE subscriptions.

Deliberately minimal by design (this is a shared-plant terminal, not a multiuser
system): a single displayed pin, a signed value with an expiry, no database, no
refresh/revocation of the token beyond its TTL. Use a real identity layer if you
need per-user roles/audit of who acted.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

_ALGO = "sha256"
_TTL_S = 12 * 3600  # a shareable token that lasts a work shift


def new_secret() -> str:
    """A random HMAC key so tokens are not forgeable without reading config."""
    return secrets.token_hex(32)


def token_for(pin: str, secret: str, *, ttl: int = _TTL_S, now: float | None = None) -> str:
    """Sign a pin+expiry into a compact bearer token: <exp>.<sig>."""
    now = now if now is not None else time.time()
    exp = int(now + ttl)
    body = f"{exp}."
    sig = _sig(body, pin, secret)
    return f"{body}{sig}"


def verify_token(token: str, pin: str, secret: str, *, now: float | None = None) -> bool:
    """True iff the token is well-formed, expires in the future, and matches `pin`."""
    if not token or "." not in token:
        return False
    body, sig = token.rsplit(".", 1)
    if not body or not sig:
        return False
    expected = _sig(body, pin, secret)
    if not hmac.compare_digest(sig, expected):
        return False
    exp = int(body[:-1]) if body.endswith(".") else 0
    return exp > (now if now is not None else time.time())


def _sig(body: str, pin: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{body}{pin}".encode(), hashlib.sha256).hexdigest()