"""Minimal cron expression matcher (stdlib-only).

Supports the standard 5-field syntax the config promises (``0 6 * * *``):
minute, hour, day-of-month, month, day-of-week — with ``*``, lists (``0,30``),
ranges (``9-17``) and steps (``*/15``). Day-of-month and day-of-week follow the
classic Vixie-cron OR semantics (either matching fires when both are restricted).

This exists because the edge deployment must not pull in croniter just to fire a
daily report; the supported surface is deliberately small and fully testable.
"""

from __future__ import annotations

from datetime import datetime


def _parse_field(field: str, lo: int, hi: int) -> set[int] | None:
    """Parse one cron field into a set of allowed values; None means '*' (any)."""
    if field == "*":
        return None
    allowed: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"empty cron field part: {field!r}")
        step = 1
        if "/" in part:
            part, _, step_s = part.partition("/")
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"cron step must be positive: {field!r}")
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, _, b = part.partition("-")
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start < lo or end > hi or start > end:
            raise ValueError(f"cron field out of range: {field!r}")
        allowed.update(range(start, end + 1, step))
    return allowed


def cron_matches(expr: str, now: datetime) -> bool:
    """True when ``now`` matches the 5-field cron expression ``expr``."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields: {expr!r}")
    minute = _parse_field(fields[0], 0, 59)
    hour = _parse_field(fields[1], 0, 23)
    dom = _parse_field(fields[2], 1, 31)
    month = _parse_field(fields[3], 1, 12)
    # 0=Sunday (Vixie cron), Python's weekday() is 0=Monday.
    # (d-1)%7: cron 0(Sun)->6, 1(Mon)->0, ..., 6(Sat)->5, 7(Sun)->6.
    dow_raw = _parse_field(fields[4], 0, 7)
    dow = None
    if dow_raw is not None:
        dow = {(d - 1) % 7 for d in dow_raw}

    if minute is not None and now.minute not in minute:
        return False
    if hour is not None and now.hour not in hour:
        return False
    if month is not None and now.month not in month:
        return False
    # dom/dow: unrestricted field is ignored; when both are restricted, either
    # matching fires (classic Vixie semantics)
    dom_restricted = dom is not None
    dow_restricted = dow is not None
    if dom_restricted and dow_restricted:
        if now.day not in dom and now.weekday() not in dow:
            return False
    elif dom_restricted and now.day not in dom:
        return False
    elif dow_restricted and now.weekday() not in dow:
        return False
    return True


def next_fire_after(expr: str, after: datetime, *, horizon_days: int = 400) -> datetime | None:
    """The next datetime > ``after`` matching ``expr`` (minute resolution).

    Scans minute-by-minute from the following minute up to ``horizon_days``.
    Returns None for expressions that never fire within the horizon (impossible
    dates like Feb 31).
    """
    from datetime import timedelta

    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=horizon_days)
    while t <= limit:
        if cron_matches(expr, t):
            return t
        t += timedelta(minutes=1)
    return None
