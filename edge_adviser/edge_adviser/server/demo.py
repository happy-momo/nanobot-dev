"""Demo workspace seeding — one-shot rich history so every HMI page has data.

Called from the FastAPI lifespan when ``config.demo`` is true, *before* the
scheduler loop starts. Generates timestamps relative to *now* so the freshness
gate passes on first load, and deliberately exercises every HMI surface:

  * 24 snapshots per asset (30-min spacing, newest at now-2min)
  * 4 backdated deep reports for the healthy/warn/critical assets
  * 3 alerts (one already acknowledged) — the stale asset's alert is NOT seeded:
    the scheduler derives it live on tick 1 through the (fixed) SSE alert path
  * runlog / feedback / MEMORY.md / claims+conflicts for the knowledge ledger

Idempotent per calendar day via a marker file — restarts within the same day do
not duplicate data, and a new day reseeds with fresh relative timestamps.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..analytics.engine import analyze
from ..config.model import AssetState, AssetSpec
from ..core.bus import KIND_SNAPSHOT, make_snapshot_event
from ..core.runlog import log_run_event
from ..ingest import create_source
from .deps import AppState

SEED_VERSION = 1
MARKER = ".demo_seed.json"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _seeded_today(state: AppState) -> bool:
    p = Path(state.config.workspace) / MARKER
    if not p.exists():
        return False
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("date") == _today()
    except Exception:  # noqa: BLE001 — corrupt marker → reseed
        return False


def _mark_seeded(state: AppState) -> None:
    p = Path(state.config.workspace) / MARKER
    p.write_text(json.dumps({"date": _today(), "version": SEED_VERSION},
                            ensure_ascii=False, indent=2), encoding="utf-8")


async def seed_demo_workspace(state: AppState) -> None:
    """Populate the demo workspace (idempotent per day)."""
    # The downtime window must be re-injected on EVERY startup (it is an
    # in-memory window anchored to `now`), before the seeded-today early return:
    # without this, a same-day restart leaves tower1's placeholder window and
    # the demo asset drifts from "planned downtime" to an OFFLINE alarm.
    _inject_demo_downtime(state)
    if _seeded_today(state):
        return
    now = datetime.now(timezone.utc)

    for asset in state.config.assets:
        await _seed_asset_snapshots(state, asset, now)
        await _seed_reports(state, asset, now)
        _seed_runlog(state, asset, now)
        _seed_feedback(state, asset)
        _seed_memory(state, asset)
        _publish_latest(state, asset)

    _seed_alerts(state, now)
    _mark_seeded(state)


# --------------------------------------------------------------------------- #
def _inject_demo_downtime(state: AppState) -> None:
    """Give tower1 an in-memory planned-downtime window covering now.

    The config JSON keeps a placeholder; the seeder owns the live window so the
    demo always shows a planned_downtime asset regardless of when it starts.
    """
    for asset in state.config.assets:
        if asset.asset_id != "tower1":
            continue
        now = datetime.now(timezone.utc)
        asset.downtime_windows = [
            {"start": (now - timedelta(hours=2)).isoformat(),
             "end": (now + timedelta(hours=6)).isoformat()}
        ]


async def _seed_asset_snapshots(state: AppState, asset: AssetSpec,
                                now: datetime) -> None:
    """Persist a history of analyzed snapshots, spaced 30 minutes apart."""
    window = timedelta(seconds=asset.window_s)
    src = _source_for(state, asset)
    # feeder1's configured source is the fault regime (it must keep failing
    # live), so its single stale snapshot is generated from a clean stand-in.
    stale_assets = {"feeder1"}
    # tower1 (offline regime) only gets snapshots older than the downtime start
    # — during downtime the scheduler writes empty windows, which is the point.
    downtime_start = now - timedelta(hours=2)
    count = 24
    for i in range(count):
        end = now - timedelta(minutes=2 + 30 * (count - 1 - i))
        if asset.asset_id in stale_assets:
            if i < count - 1:
                continue  # only the single stale one below
            end = now - timedelta(hours=3)
        if asset.asset_id == "tower1" and end > downtime_start:
            continue  # nothing newer than downtime start → planned_downtime live
        start = end - window
        use_src = src
        if asset.asset_id in stale_assets:
            use_src = _clean_standin(state, asset)
        try:
            samples = await use_src.pull(start, end)
        except Exception:  # noqa: BLE001 — fault regime source
            continue
        if not samples:
            continue
        snap = analyze(asset, samples, source="simulator", regime="seeded",
                       window_start=start.isoformat(timespec="seconds"),
                       window_end=end.isoformat(timespec="seconds"))
        state.store.save_snapshot(asset.asset_id, snap.to_dict())


def _source_for(state: AppState, asset: AssetSpec) -> Any:
    """Build a throwaway simulator matching the asset's configured source spec."""
    spec = next((s for s in state.config.sources if s.asset_id == asset.asset_id), None)
    if spec is None:
        spec = next((s for s in state.config.sources if not s.asset_id), None)
    if spec is None:
        raise RuntimeError(f"demo asset {asset.asset_id} has no source spec")
    return create_source(spec)


def _clean_standin(state: AppState, asset: AssetSpec) -> Any:
    """A clean-regime copy of the asset's source for seeding stale snapshots."""
    from copy import deepcopy
    spec = next((s for s in state.config.sources if s.asset_id == asset.asset_id), None)
    spec = deepcopy(spec) if spec else deepcopy(state.config.sources[0])
    spec.params = dict(spec.params)
    spec.params.pop("regime", None)
    return create_source(spec)


async def _seed_reports(state: AppState, asset: AssetSpec, now: datetime) -> None:
    """Archive 4 daily reports (today + 3 backdated) via the real generate path."""
    if asset.asset_id not in ("pump1", "motor1", "chiller1"):
        return
    # use the freshest persisted snapshot as the facts for every archived day
    files = state.store.list_snapshots(asset.asset_id, limit=1)
    if not files:
        return
    snap = state.store.read_persisted_snapshot(asset.asset_id, files[0].name)
    if not snap:
        return
    for back in range(4):
        day = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        generated_at = (now - timedelta(days=back)).isoformat(timespec="seconds")
        try:
            res = await state.report_gen.generate(asset, snap)
        except Exception:  # noqa: BLE001 — demo must not die on one report
            continue
        state.report_gen.archive(
            asset, snap, res.content or "(empty)",
            degraded=res.degraded, provider=res.used_provider,
            generated_at=generated_at, day=day,
        )


def _seed_alerts(state: AppState, now: datetime) -> None:
    """Append three historical alerts; the stale asset's alert stays live-derived."""
    def rec(asset_id: str, subject: str, state_: str, level: str,
            headline: str, age_min: float, acked: bool) -> dict[str, Any]:
        ts = (now - timedelta(minutes=age_min)).timestamp()
        return {
            "asset_id": asset_id,
            "state": state_,
            "subject": subject,
            "reason": "seeded demo alert",
            "overall_level": level,
            "headline": headline,
            "window_end": (now - timedelta(minutes=age_min)).isoformat(timespec="seconds"),
            "ts": ts,
            "acked": acked,
            "acked_at": (now - timedelta(minutes=age_min - 5)).isoformat(timespec="seconds") if acked else None,
            "dedup_key": f"{asset_id}:{subject}",
        }

    alerts = [
        rec("pump1", "warn", "healthy", "warn",
            "vibration 持续高于警告阈值,疑似轴承磨损初期", 40, False),
        rec("chiller1", "critical", "healthy", "critical",
            "温度瞬时尖峰越过严重阈值", 12, False),
        rec("motor1", "warn", "healthy", "warn",
            "夜间负载波动引起的临时警告(已确认)", 26 * 60, True),
    ]
    for a in alerts:
        p = state.store.asset_dir(a["asset_id"]) / "alerts.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")


def _seed_runlog(state: AppState, asset: AssetSpec, now: datetime) -> None:
    """~6 audit rows per day for the last 3 days, plus fault rows for feeder1."""
    for back in range(3):
        day = (now - timedelta(days=back)).strftime("%Y-%m-%d")
        stamp = (now - timedelta(days=back, hours=6)).isoformat(timespec="seconds")
        if asset.asset_id == "feeder1" and back == 0:
            log_run_event(state.store, asset.asset_id, "run_once", "exception",
                          reason="pull failed: simulated transport fault",
                          timestamp=stamp, date=day, state="unknown")
            log_run_event(state.store, asset.asset_id, "deep_report", "no_data",
                          reason="no_fresh_snapshot: stale", timestamp=stamp,
                          date=day, state="stale")
            continue
        log_run_event(state.store, asset.asset_id, "run_once", "ok",
                      reason="", timestamp=stamp, date=day, state="healthy")
        log_run_event(state.store, asset.asset_id, "run_once", "ok",
                      reason="", timestamp=stamp, date=day, state="healthy")
        log_run_event(state.store, asset.asset_id, "deep_report", "ok",
                      reason="", timestamp=stamp, date=day)
        log_run_event(state.store, asset.asset_id, "learn", "ok",
                      reason="", timestamp=stamp, date=day)
        log_run_event(state.store, asset.asset_id, "monitor", "ok",
                      reason="", timestamp=stamp, date=day)


def _seed_feedback(state: AppState, asset: AssetSpec) -> None:
    notes = {
        "pump1": ["泵体振动偏大时优先检查联轴器对中。", "该泵夜班负载低,阈值可适当放宽。"],
        "motor1": ["电机温升与室温相关性较强,夏季阈值需复核。"],
        "chiller1": ["温度尖峰多为启停冲击,属正常瞬态。"],
    }
    for note in notes.get(asset.asset_id, ["运行正常,无特别备注。"]):
        state.store.append_feedback(asset.asset_id, note)


def _seed_memory(state: AppState, asset: AssetSpec) -> None:
    """Pre-write MEMORY.md; pump1 additionally gets claims/conflicts ledger rows."""
    mem = {
        "pump1": "# pump1 运行记忆\n\n## 运行洞察\n\n- 振动均值长期在 3.0 上下波动,接近警告边界。\n- 温度随环境温度有 ±3℃ 的季节性漂移,属正常。\n\n## 维护动作\n\n- 2026-11 曾对联轴器重新对中,振动短暂下降后回升。\n\n## 数据质量\n\n- 数据覆盖率稳定;夜班窗口偶有稀疏,门禁已拦截。\n",
        "motor1": "# motor1 运行记忆\n\n## 运行洞察\n\n- 电机运行平稳,温升与负载线性相关。\n\n## 维护动作\n\n- 常规保养周期 6 个月,上次保养已记录。\n",
        "chiller1": "# chiller1 运行记忆\n\n## 运行洞察\n\n- 启停时温度瞬时尖峰为已知瞬态,非故障。\n\n## 数据质量\n\n- 尖峰窗口建议提高 persist 要求以避免误报。\n",
    }
    content = mem.get(asset.asset_id)
    if content:
        state.store.write_memory_md(asset.asset_id, content)

    if asset.asset_id != "pump1":
        return
    claims_path = state.store.asset_dir(asset.asset_id) / "memory_claims.jsonl"
    conflicts_path = state.store.asset_dir(asset.asset_id) / "memory_conflicts.jsonl"
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    claims = [
        {"id": "c1", "claim": "振动均值稳定在 2.8~3.2 区间", "ts": now_iso,
         "status": "active", "source": "learn"},
        {"id": "c2", "claim": "联轴器对中后振动应低于 2.5", "ts": now_iso,
         "status": "stale", "source": "feedback"},
    ]
    conflicts = [
        {"id": "f1", "claim_id": "c2", "reason": "当前振动均值高于该声明值",
         "ts": now_iso, "status": "open"},
    ]
    for path, rows in ((claims_path, claims), (conflicts_path, conflicts)):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _publish_latest(state: AppState, asset: AssetSpec) -> None:
    """Announce the newest seeded snapshot on the bus so SSE replay has data."""
    import asyncio

    files = state.store.list_snapshots(asset.asset_id, limit=1)
    if not files:
        return
    snap = state.store.read_persisted_snapshot(asset.asset_id, files[0].name)
    if not snap:
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(state.bus.publish(
            KIND_SNAPSHOT, asset.asset_id,
            payload=make_snapshot_event(asset.asset_id, snap),
        ))
    except RuntimeError:  # no running loop (e.g. direct test invocation)
        pass
