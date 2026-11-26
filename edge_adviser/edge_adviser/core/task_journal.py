"""Persisted task tokens — a restart must not silently swallow queued work.

Background report/learn tasks previously lived only in the SSE bus: a process
restart mid-task left the client watching /stream/task/{token} forever (the
600s watchdog fired, blaming the provider for an operator-side restart). The
journal is an append-only jsonl in the workspace; on startup the server replays
it, re-publishes a terminal `failed` event for every still-running token, and
stamps recovery rows so the next restart starts clean.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

_TERMINAL = {"done", "failed", "timeout", "recovered"}


class TaskJournal:
    """Append-only task lifecycle ledger: <workspace>/.tasks.jsonl."""

    def __init__(self, workspace: Path):
        self.path = Path(workspace) / ".tasks.jsonl"

    def append(self, rec: dict) -> None:
        rec = dict(rec)
        rec.setdefault("ts", time.time())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def open_tasks(self) -> list[dict]:
        """Latest record per token whose status is not terminal."""
        latest: dict[str, dict] = {}
        for r in self._rows():
            tok = r.get("token")
            if tok:
                latest[tok] = r
        return [r for r in latest.values() if r.get("status") not in _TERMINAL]

    def mark_recovered(self, tokens: list[str]) -> None:
        for tok in tokens:
            # status "failed" (terminal) with a recovery marker in the error:
            # an honest record that the result was lost to a restart, not to
            # the provider
            self.append({"token": tok, "status": "failed",
                         "error": "server restarted before the task finished "
                                  "(recovered by startup)"})
