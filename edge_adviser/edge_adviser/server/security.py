"""Token signing + persistent HMAC secret + PIN rate limiting for the HMI.

When ``hmi_pin`` is a non-"none" string, the browser must POST it to
``/api/auth/pin`` once; on success the server returns a short-lived HMAC-signed
token that the client supplies via ``Authorization: Bearer <token>`` on later
requests / SSE subscriptions.

The HMAC secret is persisted next to the workspace so tokens survive a server
restart — otherwise every deploy would silently invalidate all open sessions and
lock already-authenticated operators out (their clients retry forever instead of
re-prompting for the PIN).

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
from pathlib import Path

_ALGO = "sha256"
_TTL_S = 12 * 3600  # a shareable token that lasts a work shift


def new_secret() -> str:
    """A random HMAC key so tokens are not forgeable without reading config."""
    return secrets.token_hex(32)


def load_or_create_secret(secret_file: Path) -> str:
    """Load the HMAC secret from disk, creating it on first use.

    Survives restarts: a persisted secret keeps issued tokens valid across
    process lifetimes. The file is written with restrictive permissions
    (best-effort on platforms without POSIX chmod semantics).
    """
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = secret_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    secret = new_secret()
    try:
        secret_file.write_text(secret, encoding="utf-8")
        try:
            secret_file.chmod(0o600)
        except OSError:
            pass
    except OSError:
        # fall back to an ephemeral secret rather than failing startup
        return new_secret()
    return secret


# --------------------------------------------------------------------------- #
# PIN rate limiting — a shared 4-6 digit PIN on 0.0.0.0 is otherwise brute-forceable
# --------------------------------------------------------------------------- #
class PinRateLimiter:
    """In-process failure tracking with lockout after repeated wrong PINs.

    Sliding window: after ``max_failures`` wrong attempts within ``window_s``,
    exchanges are refused for ``lockout_s``. State is per-process (an attacker
    who can trigger a restart gains little — the token TTL is the real bound).
    """

    def __init__(self, max_failures: int = 5, window_s: float = 300.0,
                 lockout_s: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window_s = window_s
        self.lockout_s = lockout_s
        self._failures: list[float] = []
        self._locked_until = 0.0
        # process-lifetime total — the sliding window clears and the lockout
        # expires, but the brute-force evidence stays visible in /api/version
        self.total_failures = 0

    def locked(self, now: float | None = None) -> float:
        """Seconds remaining in lockout, 0 if not locked."""
        now = now if now is not None else time.time()
        return max(0.0, self._locked_until - now)

    def record_failure(self, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        self.total_failures += 1
        cutoff = now - self.window_s
        self._failures = [t for t in self._failures if t >= cutoff]
        self._failures.append(now)
        if len(self._failures) >= self.max_failures:
            self._locked_until = now + self.lockout_s
            self._failures.clear()

    def record_success(self) -> None:
        self._failures.clear()
        self._locked_until = 0.0

    def reset(self) -> None:
        self._failures.clear()
        self._locked_until = 0.0


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
    try:
        exp = int(body.rstrip("."))
    except ValueError:
        return False
    # token_for signs "<exp>." WITH the trailing dot — verify the exact bytes.
    expected = _sig(f"{body.rstrip('.')}.", pin, secret)
    if not hmac.compare_digest(sig, expected):
        return False
    return exp > (now if now is not None else time.time())


def _sig(body: str, pin: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{body}{pin}".encode(), hashlib.sha256).hexdigest()
