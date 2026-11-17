"""N6 keyword-index retrieval + N8 conflict/evolution ledger (all stdlib, offline)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.config.model import AssetSpec  # noqa: E402
from edge_adviser.core.memory_service import MemoryService  # noqa: E402


def make_asset() -> AssetSpec:
    return AssetSpec(asset_id="pump1", name="pump", window_s=60, sample_freq_s=1.0)


def _mem(**kw) -> MemoryService:
    tmp = Path(__file__).resolve().parent / "_tmp_mem"
    tmp.mkdir(exist_ok=True)
    return MemoryService(
        make_asset(), tmp,
        index="keywords", conflict_policy="flag", decay_s=1,
    )


def test_keywords_retrieves_relevant_over_recent(tmp_path: Path) -> None:
    asset = make_asset()
    svc = MemoryService(asset, tmp_path, index="keywords", conflict_policy="off")
    mem = (
        "# Knowledge\n"
        "## Vibration fault pattern\n"
        "High vibration above 3 mm/s indicates bearing wear in the pump.\n"
        "## Temperature baseline\n"
        "Normal operating temperature is around 65 C with slow seasonal drift.\n"
    )
    svc.write(mem)
    hits = svc.retrieve(query="temperature normal", top_k=1, max_chars=2000)
    assert hits and "Temperature baseline" in hits[0]


def test_off_mode_returns_most_recent(tmp_path: Path) -> None:
    asset = make_asset()
    svc = MemoryService(asset, tmp_path, index="off", conflict_policy="off")
    mem = (
        "# Knowledge\n"
        "## Vibration fault pattern\n"
        "Bearing wear.\n"
        "## Temperature baseline\n"
        "Normal 65C.\n"
    )
    svc.write(mem)
    hits = svc.retrieve(top_k=1, max_chars=2000)
    # legacy behavior: newest section first
    assert hits and "Temperature baseline" in hits[0]


def test_conflict_supersedes_claim(tmp_path: Path) -> None:
    asset = make_asset()
    svc = MemoryService(asset, tmp_path, index="keywords", conflict_policy="flag")
    svc.write("# K\n## Threshold\nNormal oil pressure range is [40, 60] bar.\n")
    svc.write("# K\n## Threshold\nNormal oil pressure range is [70, 90] bar.\n")
    claims = svc._ledger._read(svc._ledger.claims_path)
    superseded = [c for c in claims if c.get("status") == "superseded"]
    # same topic, contradictory numeric bounds -> first claim superseded
    assert any("Threshold" in c.get("heading", "") for c in superseded)
    conflicts = svc._ledger._read(svc._ledger.conflicts_path)
    assert any("Threshold" in c.get("heading", "") for c in conflicts)


def test_stale_claim_flagged(tmp_path: Path) -> None:
    asset = make_asset()
    svc = MemoryService(asset, tmp_path, index="keywords", conflict_policy="flag", decay_s=1)
    svc.write("# K\n## Vibration\nBearing wear above 3 mm/s.\n")
    # backdate the claim to force staleness
    claims_path = svc._ledger.claims_path
    recs = [json.loads(l) for l in claims_path.read_text(encoding="utf-8").splitlines() if l]
    for r in recs:
        r["created_at"] = "2000-01-01T00:00:00"
    with open(claims_path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    hits = svc.retrieve(query="vibration", top_k=1, max_chars=2000)
    assert hits and "[stale]" in hits[0]