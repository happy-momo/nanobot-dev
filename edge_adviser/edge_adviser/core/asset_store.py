"""Asset registry / file layout on the workspace.

Each asset owns a directory:

    <workspace>/assets/<asset_id>/
        ASSET.md         scene metadata / background knowledge (from profile)
        MEMORY.md        domain knowledge consolidated by the learn loop
        snapshots/<ts>.json      per-analysis analytics snapshots
        reports/<date>/report.md, snapshot.json   archived reports

The learn loop and QA both read `MEMORY.md` for the asset's accumulated
knowledge. This store also keeps simple append-only feedback in
`feedback.jsonl` so the learner can incorporate operator corrections.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config.model import AssetSpec
from ..config.loader import ensure_default_asset_md


class AssetStore:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.assets_root = self.workspace / "assets"

    # ------------------------------------------------------------------ #
    # layout helpers
    # ------------------------------------------------------------------ #
    def asset_dir(self, asset_id: str) -> Path:
        d = self.assets_root / asset_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def asset_md(self, asset_id: str) -> Path:
        return self.asset_dir(asset_id) / "ASSET.md"

    def memory_md(self, asset_id: str) -> Path:
        return self.asset_dir(asset_id) / "MEMORY.md"

    def snapshots_dir(self, asset_id: str) -> Path:
        d = self.asset_dir(asset_id) / "snapshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def reports_dir(self, asset_id: str) -> Path:
        d = self.asset_dir(asset_id) / "reports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def feedback_path(self, asset_id: str) -> Path:
        return self.asset_dir(asset_id) / "feedback.jsonl"

    def runlog_path(self, asset_id: str) -> Path:
        """Path to the append-only audit ledger for an asset (sibling of reports/)."""
        return self.asset_dir(asset_id) / "runlog.jsonl"

    # ------------------------------------------------------------------ #
    # asset metadata
    # ------------------------------------------------------------------ #
    def ensure_asset(self, asset: AssetSpec) -> Path:
        """Ensure ASSET.md exists and return its path."""
        return ensure_default_asset_md(asset, self.assets_root)

    def read_asset_md(self, asset_id: str) -> str:
        p = self.asset_md(asset_id)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def read_memory_md(self, asset_id: str) -> str:
        p = self.memory_md(asset_id)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def write_memory_md(self, asset_id: str, content: str) -> Path:
        p = self.memory_md(asset_id)
        p.write_text(content, encoding="utf-8")
        return p

    # ------------------------------------------------------------------ #
    # snapshots
    # ------------------------------------------------------------------ #
    def save_snapshot(self, asset_id: str, snapshot: dict[str, Any]) -> Path:
        # Filename derives from the window end so snapshots are naturally
        # time-ordered (sorted listing = chronological history) and JSON-safe.
        ts = snapshot.get("window_end") or datetime.now().isoformat(timespec="seconds")
        safe = ts.replace(":", "-").replace("/", "-")
        p = self.snapshots_dir(asset_id) / f"{safe}.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        return p

    def list_snapshots(self, asset_id: str, limit: int = 50) -> list[Path]:
        files = sorted(self.snapshots_dir(asset_id).glob("*.json"), reverse=True)
        return files[:limit]

    def read_persisted_snapshot(self, asset_id: str, filename: str) -> dict[str, Any] | None:
        """Read one saved snapshot by its (sanitized-window-end) filename.

        `filename` may be bare ("2026-....json") or the full path returned by
        ``list_snapshots``. Returns None if missing/unparseable.
        """
        p = Path(filename)
        if not p.is_absolute():
            p = self.snapshots_dir(asset_id) / p.name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    # ------------------------------------------------------------------ #
    # archived reports (<date>/report.md + snapshot.json)
    # ------------------------------------------------------------------ #
    def list_reports(self, asset_id: str) -> list[dict[str, Any]]:
        """Return archived report summaries (newest first) for an asset.

        Each entry: {date, report_md, meta, degraded, provider, count}.
        """
        out: list[dict[str, Any]] = []
        rd = self.reports_dir(asset_id)
        for day in sorted(rd.iterdir(), reverse=True):
            if not day.is_dir() or not day.name[:4].isdigit():
                continue
            day_md = day / "report.md"
            day_json = day / "snapshot.json"
            entry: dict[str, Any] = {"date": day.name, "report_md": None, "meta": {},
                                     "snapshot": None, "degraded": False, "provider": ""}
            if day_md.exists():
                entry["report_md"] = day_md.read_text(encoding="utf-8")
            if day_json.exists():
                try:
                    doc = json.loads(day_json.read_text(encoding="utf-8"))
                    entry["meta"] = doc.get("meta") or {}
                    entry["snapshot"] = doc.get("snapshot")
                    entry["degraded"] = bool(doc.get("degraded"))
                    entry["provider"] = doc.get("provider", "")
                except json.JSONDecodeError:
                    pass
            out.append(entry)
        return out

    def read_report(self, asset_id: str, date: str) -> dict[str, Any] | None:
        """Read one archived report day. Returns list_reports-style dict or None."""
        day = self.reports_dir(asset_id) / date
        if not day.is_dir():
            return None
        matches = [r for r in self.list_reports(asset_id) if r["date"] == date]
        return matches[0] if matches else None

    # ------------------------------------------------------------------ #
    # feedback (operator corrections → learn loop)
    # ------------------------------------------------------------------ #
    def append_feedback(self, asset_id: str, note: str, **extra: Any) -> None:
        rec = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "note": note,
            **extra,
        }
        with open(self.feedback_path(asset_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def read_feedback(self, asset_id: str) -> list[dict[str, Any]]:
        p = self.feedback_path(asset_id)
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    # ------------------------------------------------------------------ #
    # runlog (audit ledger, P2) — append-only JSON lines
    # ------------------------------------------------------------------ #
    def append_runlog(self, asset_id: str, record: dict[str, Any]) -> None:
        with open(self.runlog_path(asset_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_runlog(self, asset_id: str, limit: int = 100) -> list[dict[str, Any]]:
        p = self.runlog_path(asset_id)
        if not p.exists():
            return []
        out: list[dict[str, Any]] = []
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out[-limit:]


def create_asset_memory_store(asset: AssetSpec, workspace: Path) -> NanobotMemoryStore:
    """A nanobot MemoryStore scoped to the asset directory.

    Reuses nanobot's durable memory + GitStore bookkeeping (SOUL.md/USER.md /
    MEMORY.md tracked, versioned commits, audit) — the same machinery Dream
    uses — so the asset's knowledge base becomes a revisable, auditable
    knowledge asset.
    """
    from nanobot.agent.memory import MemoryStore as NanobotMemoryStore

    asset_dir = workspace / "assets" / asset.asset_id
    asset_dir.mkdir(parents=True, exist_ok=True)
    return NanobotMemoryStore(workspace=asset_dir)