"""Web HMI server for EdgeAdviser — FastAPI + SSE + static SPA host.

Entry points:
  * ``create_app(config_path)`` — programmatic app factory (tests, uvicorn).
  * ``app_factory`` / ``app``    — uvicorn-friendly default (env EDGE_ADVISER_CONFIG).

Never touches the nanobot core. Reuses the existing deterministic services.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]