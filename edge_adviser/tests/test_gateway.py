"""Unit tests for LLMGateway routing/degradation — no real nanobot/LLM.

The gateway's degradation contract ("try candidates best-first, mark degraded
when the preferred provider failed, never give up while a candidate remains")
is the compliance-relevant core of the system; it runs against a fake Nanobot
so the tests exercise the real gateway code path, not a stub in its place.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# ensure the inner package is importable regardless of CWD
_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402

from edge_adviser.config.model import AdviserConfig, ModelRole, ProviderSpec  # noqa: E402
from edge_adviser.llm.gateway import (  # noqa: E402
    LLMGateway,
    LLMResult,
    ProviderUnavailable,
    _resolve_config_path,
)


class _FakeBotResult:
    def __init__(self, content: str = "ok", error: str | None = None):
        self.content = content
        self.error = error
        self.usage = {}


class _FakeRuntime:
    def __init__(self):
        self.registered = []

    def add_context_provider(self, cb):
        self.registered.append(cb)
        return lambda: None


class _FakeBot:
    """Records model_preset per call; fails for configured presets."""

    def __init__(self, fail_presets: set[str] | None = None,
                 content: str = "ok", error: str | None = None):
        self.fail_presets = fail_presets or set()
        self.content = content
        self.error = error
        self.calls: list[str] = []
        self.runtime = _FakeRuntime()

    async def run(self, prompt, *, session_key="", model_preset=None,
                  model=None, attributes=None, **kw):
        self.calls.append(model_preset or "")
        if model_preset in self.fail_presets:
            raise RuntimeError(f"preset {model_preset} exploded")
        return _FakeBotResult(content=self.content, error=self.error)


def _config(tmp_path: Path, **providers) -> AdviserConfig:
    return AdviserConfig(workspace=tmp_path,
                         defaults={"config_path": str(tmp_path / "runtime.json")},
                         providers={
                             "online": ProviderSpec(role=ModelRole.ONLINE,
                                                    model_preset="online-p"),
                             "offline": ProviderSpec(role=ModelRole.OFFLINE,
                                                     model_preset="offline-p"),
                             "routine": ProviderSpec(role=ModelRole.ROUTINE,
                                                     model_preset="routine-p"),
                             **providers,
                         })


def _gateway(tmp_path: Path, bot: _FakeBot, **providers) -> LLMGateway:
    gw = LLMGateway(_config(tmp_path, **providers), None)  # type: ignore
    gw._bot = bot  # inject the fake, skipping Nanobot.from_config
    return gw


# --------------------------------------------------------------------------- #
# routing order
# --------------------------------------------------------------------------- #
def test_online_role_tries_online_first(tmp_path: Path) -> None:
    bot = _FakeBot()
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE))
    assert res.error is None
    assert res.used_provider == "online-p"
    assert res.degraded is False
    assert bot.calls == ["online-p"]


def test_routine_role_prefers_routine(tmp_path: Path) -> None:
    bot = _FakeBot()
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ROUTINE))
    assert res.used_provider == "routine-p"


# --------------------------------------------------------------------------- #
# degradation
# --------------------------------------------------------------------------- #
def test_degrades_to_fallback_on_primary_failure(tmp_path: Path) -> None:
    bot = _FakeBot(fail_presets={"online-p"})
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE))
    assert res.error is None
    assert res.used_provider == "offline-p"
    assert res.degraded is True, "fallback success must be marked degraded"
    assert bot.calls == ["online-p", "offline-p"]


def test_all_providers_failing_returns_error(tmp_path: Path) -> None:
    bot = _FakeBot(fail_presets={"online-p", "offline-p", "routine-p"})
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE))
    assert res.error and res.error.startswith("all providers failed")


def test_explicit_model_preset_keeps_degradation_chain(tmp_path: Path) -> None:
    """A forced preset goes first but the role fallback must still be tried."""
    bot = _FakeBot(fail_presets={"forced", "online-p"})
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE, model_preset="forced"))
    assert res.error is None, "forced preset failure must degrade, not abort"
    assert res.used_provider == "offline-p"
    assert res.degraded is True
    assert bot.calls == ["forced", "online-p", "offline-p"]


def test_explicit_preset_deduplicates_candidates(tmp_path: Path) -> None:
    bot = _FakeBot()
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE, model_preset="online-p"))
    assert bot.calls == ["online-p"], "preset equal to primary must not run twice"


def test_provider_error_result_counts_as_failure(tmp_path: Path) -> None:
    """A bot that returns (not raises) an error result must trigger fallback."""
    class _ErrThenOkBot(_FakeBot):
        async def run(self, prompt, *, model_preset=None, **kw):
            self.calls.append(model_preset or "")
            if model_preset == "online-p":
                return _FakeBotResult(content="", error="upstream 500")
            return _FakeBotResult(content="ok")

    bot = _ErrThenOkBot()
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE))
    assert res.used_provider == "offline-p"
    assert res.degraded is True


def test_no_candidates_raises_provider_unavailable(tmp_path: Path) -> None:
    gw = LLMGateway(AdviserConfig(workspace=tmp_path), None)  # type: ignore
    gw._bot = _FakeBot()
    with pytest.raises(ProviderUnavailable):
        asyncio.run(gw.run("q", role=ModelRole.ONLINE))


# --------------------------------------------------------------------------- #
# timeouts + json parsing
# --------------------------------------------------------------------------- #
def test_timeout_moves_to_next_provider(tmp_path: Path) -> None:
    class _SlowBot(_FakeBot):
        async def run(self, prompt, *, model_preset=None, **kw):
            self.calls.append(model_preset or "")
            if model_preset == "online-p":
                await asyncio.sleep(10)
            return _FakeBotResult(content="ok")

    bot = _SlowBot()
    gw = _gateway(tmp_path, bot,
                  online=ProviderSpec(role=ModelRole.ONLINE, model_preset="online-p",
                                      timeout_s=0.05),
                  offline=ProviderSpec(role=ModelRole.OFFLINE, model_preset="offline-p",
                                       timeout_s=5))
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE))
    assert res.used_provider == "offline-p"
    assert res.degraded is True


def test_parse_json_extracts_object(tmp_path: Path) -> None:
    bot = _FakeBot(content='```json\n{"risk": "low"}\n```')
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE, parse_json=True))
    assert res.parsed_json == {"risk": "low"}


def test_parse_json_falls_back_gracefully(tmp_path: Path) -> None:
    bot = _FakeBot(content="no json here at all")
    gw = _gateway(tmp_path, bot)
    res = asyncio.run(gw.run("q", role=ModelRole.ONLINE, parse_json=True))
    assert res.parsed_json is None
    assert res.error is None, "unparseable JSON must not fail the whole call"


# --------------------------------------------------------------------------- #
# config path resolution
# --------------------------------------------------------------------------- #
def test_resolve_config_path_relative_to_source_file(tmp_path: Path) -> None:
    cfg = AdviserConfig(workspace=tmp_path,
                        defaults={"config_path": "runtime.json",
                                  "_source_file": str(tmp_path / "cfg" / "adviser.json")})
    resolved = _resolve_config_path(cfg)
    assert Path(resolved).is_absolute()
    assert Path(resolved).parent == (tmp_path / "cfg")


# --------------------------------------------------------------------------- #
# cron matcher (task 17's engine)
# --------------------------------------------------------------------------- #
def test_cron_matches_basic_and_ranges() -> None:
    from datetime import datetime
    from edge_adviser.core.cron import cron_matches

    assert cron_matches("0 6 * * *", datetime(2026, 11, 19, 6, 0))
    assert not cron_matches("0 6 * * *", datetime(2026, 11, 19, 6, 1))
    assert cron_matches("*/15 * * * *", datetime(2026, 11, 19, 9, 45))
    assert cron_matches("0 3 * * 1-5", datetime(2026, 11, 16, 3, 0))  # Monday
    assert not cron_matches("0 3 * * 1-5", datetime(2026, 11, 15, 3, 0))  # Sunday
    assert cron_matches("30 2 1,15 * *", datetime(2026, 11, 15, 2, 30))


def test_cron_matches_rejects_malformed() -> None:
    from datetime import datetime
    from edge_adviser.core.cron import cron_matches

    with pytest.raises(ValueError):
        cron_matches("99 6 * * *", datetime(2026, 11, 19, 6, 0))
    with pytest.raises(ValueError):
        cron_matches("0 6 * *", datetime(2026, 11, 19, 6, 0))


def test_run_due_jobs_fires_deep_report_once_per_slot(tmp_path: Path) -> None:
    """The cron wiring runs deep_report/learn at most once per matching slot."""
    from datetime import datetime, timezone
    from edge_adviser.core.scheduler import Supervisor

    asset_cfg = __import__("edge_adviser.config.model", fromlist=["AssetSpec"]).AssetSpec(
        asset_id="pump1", name="P", window_s=60, sample_freq_s=1.0,
        metrics=[__import__("edge_adviser.config.model",
                            fromlist=["MetricSpec"]).MetricSpec(key="temp")])

    class _Learn:
        def __init__(self):
            self.calls = 0

        def buffer_snapshot(self, *a, **k):
            pass

        async def learn_asset(self, *a, **k):
            self.calls += 1
            return LLMResult(content="learned")

    deep_calls = {"n": 0}

    class _Gen:
        async def generate(self, *a, **k):
            return LLMResult(content="# R")

        def archive(self, *a, **k):
            deep_calls["n"] += 1
            return tmp_path / "r"

    class _Gw:
        async def astart(self):
            pass

        async def aclose(self):
            pass

        async def run(self, *a, **k):
            return LLMResult(content="")

    from edge_adviser.core.asset_store import AssetStore
    config = AdviserConfig(workspace=tmp_path, assets=[asset_cfg],
                           deep_report_cron="0 6 * * *", learn_cron="0 3 * * *")
    store = AssetStore(tmp_path)
    # the read gate demands a fresh, trusted snapshot before a report may run
    store.save_snapshot("pump1", {
        "asset_id": "pump1", "window_end": datetime.now(timezone.utc).isoformat(),
        "status": "healthy", "overall_level": "info", "sample_count": 10,
        "coverage": 1.0, "metrics": [],
    })
    learn = _Learn()
    sup = Supervisor(config=config, store=store, gateway=_Gw(),  # type: ignore
                     report_gen=_Gen(), learner=learn)  # type: ignore

    async def scenario():
        # 06:00 slot: two ticks in the same minute → deep report fires once
        t = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
        await sup.run_due_jobs(asset_cfg, t)
        await sup.run_due_jobs(asset_cfg, t.replace(second=30))
        # 06:01 → not matching; 06:00 next day → fires again
        await sup.run_due_jobs(asset_cfg, t.replace(minute=1))
        await sup.run_due_jobs(asset_cfg, t.replace(day=20))
        # learn cron (03:00) never hit in this timeline
        await sup.run_due_jobs(asset_cfg, datetime(2026, 11, 19, 3, 0,
                                                   tzinfo=timezone.utc))

    asyncio.run(scenario())
    assert deep_calls["n"] == 2
    assert learn.calls == 1
