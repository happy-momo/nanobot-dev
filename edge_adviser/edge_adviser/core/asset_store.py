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