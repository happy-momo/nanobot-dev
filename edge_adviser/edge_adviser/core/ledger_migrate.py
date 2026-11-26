"""Knowledge-ledger schema unification (one spelling for both producers).

Two writers historically fed memory_claims/conflicts.jsonl: the demo seed
(ts/claim/reason/claim_id/source) and the production MemoryService
(created_at/heading/text/old/new). The HMI was forced to read both spellings —
a dual-producer tax on every future consumer. v2 is the production spelling;
demo records are normalized at the API boundary (read path) and the seed now
writes v2 directly, so fresh demo workspaces are born clean.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def normalize_claim(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a v1 demo claim to the v2 production spelling; v2 passes through."""
    if "claim" not in rec and "ts" not in rec:
        return rec  # already v2
    heading = rec.get("heading") or rec.get("claim") or ""
    return {
        "id": rec.get("id", ""),
        "heading": heading,
        "text": rec.get("text") or heading,
        "created_at": rec.get("created_at") or rec.get("ts", ""),
        "source_window": rec.get("source_window") or rec.get("source", ""),
        "status": rec.get("status", "active"),
    }


def normalize_conflict(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a v1 demo conflict to v2; v2 passes through.

    v1 semantics: claim_id points at the superseded claim, reason is the human
    evidence — mapped onto v2's old/new pair so the UI renders one shape.
    """
    if "reason" not in rec and "ts" not in rec:
        return rec  # already v2
    return {
        "id": rec.get("id", ""),
        "heading": rec.get("heading", ""),
        "old": rec.get("old") or rec.get("claim_id", ""),
        "new": rec.get("new") or rec.get("reason", ""),
        "created_at": rec.get("created_at") or rec.get("ts", ""),
    }


def normalize_records(rows: list[dict[str, Any]], *, cls: str) -> list[dict[str, Any]]:
    fn = normalize_claim if cls == "claim" else normalize_conflict
    return [fn(r) for r in rows]


def migrate_file(path: Path) -> int:
    """Rewrite a ledger jsonl in place to v2; returns rows actually changed."""
    if not path.exists():
        return 0
    cls = "claim" if "claims" in path.name else "conflict"
    rows: list[dict[str, Any]] = []
    changed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"_raw": line})
            continue
        v2 = normalize_claim(rec) if cls == "claim" else normalize_conflict(rec)
        if v2 is not rec:
            changed += 1
        rows.append(v2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            if "_raw" in r:
                f.write(r["_raw"] + "\n")
            else:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return changed
