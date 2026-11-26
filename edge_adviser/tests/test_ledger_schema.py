"""Knowledge ledger schema unification — one spelling for both producers.

Two writers historically fed memory_claims/conflicts.jsonl: the demo seed
(ts/claim/reason/claim_id/source) and the production MemoryService
(created_at/heading/text/old/new). The HMI was forced to read both spellings —
a dual-producer tax on every future consumer. v2 is the production spelling;
demo records are normalized at the API boundary (read path) and the seed now
writes v2 directly, so fresh demo workspaces are born clean.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.core.ledger_migrate import (  # noqa: E402
    normalize_records, migrate_file)


def test_v1_demo_claim_maps_to_v2() -> None:
    rows = [{"id": "c1", "claim": "振动均值稳定在 2.8~3.2 区间",
             "ts": "2026-11-25T00:00:00+00:00", "status": "active",
             "source": "learn"}]
    out = normalize_records(rows, cls="claim")
    assert out == [{"id": "c1", "heading": "振动均值稳定在 2.8~3.2 区间",
                    "text": "振动均值稳定在 2.8~3.2 区间",
                    "created_at": "2026-11-25T00:00:00+00:00",
                    "source_window": "learn", "status": "active"}]


def test_v2_production_claim_passes_through() -> None:
    v2 = {"id": "abc123", "heading": "运行洞察", "text": "运行洞察 启停尖峰为瞬态",
          "created_at": "2026-11-25T01:00:00+00:00", "source_window": "",
          "status": "active"}
    assert normalize_records([v2], cls="claim") == [v2]


def test_v1_conflict_maps_to_v2() -> None:
    rows = [{"id": "f1", "claim_id": "c2", "reason": "当前振动均值高于该声明值",
             "ts": "2026-11-25T00:00:00+00:00", "status": "open"}]
    out = normalize_records(rows, cls="conflict")
    assert out[0]["old"] == "c2"          # claim_id → old
    assert out[0]["new"] == "当前振动均值高于该声明值"  # reason → new
    assert out[0]["created_at"] == rows[0]["ts"]
    assert "reason" not in out[0] and "claim_id" not in out[0]


def test_migrate_file_rewrites_in_place(tmp_path: Path) -> None:
    p = tmp_path / "memory_claims.jsonl"
    p.write_text(json.dumps({"id": "c1", "claim": "x", "ts": "t",
                             "status": "active", "source": "learn"},
                            ensure_ascii=False) + "\n"
                 + json.dumps({"id": "v2ok", "heading": "h", "text": "h t",
                               "created_at": "now", "source_window": "",
                               "status": "active"}, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    n = migrate_file(p)
    assert n == 1  # only the v1 row needed migration
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows[0]["heading"] == "x" and "claim" not in rows[0]
    assert rows[1]["heading"] == "h"  # v2 row untouched


def test_migrate_file_handles_conflict_ledger(tmp_path: Path) -> None:
    p = tmp_path / "memory_conflicts.jsonl"
    p.write_text(json.dumps({"id": "f1", "claim_id": "c2",
                             "reason": "现场值更高", "ts": "t"},
                            ensure_ascii=False) + "\n", encoding="utf-8")
    assert migrate_file(p) == 1
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec["old"] == "c2" and rec["new"] == "现场值更高"
    assert "created_at" in rec and "ts" not in rec


def test_migrate_file_missing_is_noop(tmp_path: Path) -> None:
    assert migrate_file(tmp_path / "absent.jsonl") == 0
