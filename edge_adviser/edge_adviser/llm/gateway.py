"""LLMGateway — the Orchestration + nanobot SDK integration core.

Owns one nanobot `Nanobot` instance, routes requests between configured
providers (online strong model / offline Ollama) with automatic degradation,
injects controlled per-asset context via `add_context_provider`, and normalizes
results. Structured JSON is requested with an explicit schema instruction and
parsed defensively; we never mutate nanobot internals.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from nanobot import Nanobot, RequestContext, RuntimeContextBlock

from ..config.model import AdviserConfig, ModelRole, ProviderSpec
from .context import ContextBuilder


class ProviderUnavailable(RuntimeError):
    """Raised when every configured provider fails (e.g. fully offline)."""


@dataclass
class LLMResult:
    content: str = ""
    used_provider: str = ""
    used_role: ModelRole | None = None
    degraded: bool = False
    error: str | None = None
    usage: dict = field(default_factory=dict)
    parsed_json: Any = None
    # provenance of the snapshot the answer was actually built from — the UI
    # trust badge must describe this, not whatever the live telemetry says at
    # render time (a fresh snapshot after the answer would rewrite history).
    # Populated by QAEngine/report paths that pass a read gate.
    snapshot_meta: dict = field(default_factory=dict)


class LLMGateway:
    def __init__(self, config: AdviserConfig, context_builder: ContextBuilder):
        self.config = config
        self.ctx = context_builder
        self._bot: Nanobot | None = None
        self._context_unsubscribe: Any = None
        # pre-flight: does an online provider exist at all?
        self._online_spec = config.providers.get("online")
        self._offline_spec = config.providers.get("offline")
        self._routine_spec = config.providers.get("routine")

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    async def astart(self) -> None:
        if self._bot is not None:
            return
        self._bot = Nanobot.from_config(
            config_path=_resolve_config_path(self.config),
            workspace=self.config.workspace,
        )
        # register the runtime context provider once; reads RequestContext attributes
        self._context_unsubscribe = self._bot.runtime.add_context_provider(self._ctx_provider)

    async def aclose(self) -> None:
        if self._context_unsubscribe:
            self._context_unsubscribe()
            self._context_unsubscribe = None
        if self._bot is not None:
            await self._bot.aclose()
            self._bot = None

    async def __aenter__(self) -> "LLMGateway":
        await self.astart()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _ctx_provider(self, request: RequestContext) -> RuntimeContextBlock | Any:
        """Dispatch to ContextBuilder.provide."""
        return await self.ctx.provide(request)

    # ------------------------------------------------------------------ #
    # routing + degradation
    # ------------------------------------------------------------------ #
    def _role_for(self, role: ModelRole) -> list[ProviderSpec]:
        """Candidate presets for a role, ordered best-first, ending on the other."""
        if role == ModelRole.ONLINE:
            primary, fallback = self._online_spec, self._offline_spec
        elif role == ModelRole.ROUTINE:
            primary = self._routine_spec or self._offline_spec
            fallback = self._online_spec
        else:  # OFFLINE explicit
            primary, fallback = self._offline_spec, self._online_spec
        cands = [c for c in (primary, fallback) if c is not None]
        return cands or []

    async def run(
        self,
        prompt: str,
        *,
        role: ModelRole = ModelRole.ROUTINE,
        session_key: str = "adviser:default",
        blocks: list[RuntimeContextBlock] | None = None,
        parse_json: bool = False,
        model_preset: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Run the agent with automatic degradation.

        Tries each candidate preset in order; on error or empty result, moves to
        the next. `degraded=True` is set when the first (preferred) provider fails.
        """
        candidates = self._role_for(role)
        if model_preset:
            # an explicit preset goes FIRST, not alone: if it fails we still
            # degrade through the role's remaining providers instead of giving
            # up ("automatic degradation" must hold for every entry point)
            forced = ProviderSpec(role=ModelRole.ROUTINE, model_preset=model_preset)
            candidates = [forced] + [c for c in candidates
                                     if c.model_preset != model_preset]

        attrs = dict(attributes or {})
        if blocks is not None:
            attrs["ea_blocks"] = blocks

        last_err: str | None = None
        for idx, spec in enumerate(candidates):
            try:
                res = await self._run_once(prompt, spec, session_key,
                                           parse_json=parse_json, attributes=attrs)
                if res.error:
                    raise RuntimeError(res.error)
                res.degraded = idx > 0
                res.used_role = role
                return res
            except (ProviderUnavailable, RuntimeError, asyncio.TimeoutError) as exc:
                last_err = str(exc)
                # try next preset
        # If we have no configured providers at all, surface a clear error.
        if not candidates:
            raise ProviderUnavailable(
                "No provider configured for role. Add providers.online/offline/routine."
            )
        return LLMResult(error=f"all providers failed: {last_err}")

    async def _run_once(
        self,
        prompt: str,
        spec: ProviderSpec,
        session_key: str,
        *,
        parse_json: bool,
        attributes: dict[str, Any],
    ) -> LLMResult:
        bot = self._bot
        if bot is None:
            raise ProviderUnavailable("gateway not started")
        if parse_json:
            prompt = _json_prompt(prompt)
        try:
            result = await asyncio.wait_for(
                bot.run(
                    prompt,
                    session_key=session_key,
                    model_preset=spec.model_preset or None,
                    model=spec.model or None,
                    attributes=attributes,
                ),
                timeout=spec.timeout_s,
            )
        except asyncio.TimeoutError:
            raise ProviderUnavailable(f"provider {spec.model_preset or spec.model} timed out")
        except Exception as exc:
            raise ProviderUnavailable(f"provider {spec.model_preset or spec.model} error: {exc}")

        if result.error:
            raise ProviderUnavailable(result.error)

        content = result.content or ""
        parsed = None
        if parse_json:
            parsed = _parse_json(content)
            if parsed is None and content.strip():
                # non-JSON but had response: degrade would fail; treat as content
                pass
        return LLMResult(
            content=content,
            used_provider=spec.model_preset or spec.model or "",
            usage=result.usage or {},
            parsed_json=parsed,
        )

    # shorthand
    @property
    def bot(self) -> Nanobot:
        if self._bot is None:
            raise RuntimeError("gateway not started")
        return self._bot


def _resolve_config_path(config: AdviserConfig) -> Any:
    raw = config.defaults.get("config_path")
    if raw is None:
        return None
    from pathlib import Path

    p = Path(raw)
    if not p.is_absolute():
        # resolve relative to the adviser config location when known
        src = config.defaults.get("_source_file")
        if src:
            p = Path(src).parent / p
    return p


def _json_prompt(prompt: str) -> str:
    return (
        "Return ONLY a single valid JSON object. Do not wrap in ```json fences, "
        "do not add commentary. Follow the schema described in the task.\n\n"
        f"{prompt}"
    )


def _parse_json(content: str) -> Any:
    content = content.strip()
    # strip possible code fences
    m = re.search(r"```(?:json)?\s*(.*?)```", content, re.DOTALL)
    if m:
        content = m.group(1).strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Robust fallback: many small/local models wrap a JSON object in prose
        # or trailing text. Scan for the first balanced { ... } block and try to
        # decode just that span so a single valid object can still be recovered.
        start = content.find("{")
        if start >= 0:
            depth = 0
            for i in range(start, len(content)):
                ch = content[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(content[start:i + 1])
                        except json.JSONDecodeError:
                            return None
        return None