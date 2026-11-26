"""Task journal — in-flight report/learn tasks survive restart as honest failures.

A process restart mid-task previously left the client watching
/stream/task/{token} until the 600s watchdog fired, blaming the provider for an
operator-side restart. The journal is an append-only jsonl in the workspace;
on startup the server replays it, re-publishes a terminal `failed` event for
every still-running token, and stamps recovery rows so the next restart starts
clean.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.core.task_journal import TaskJournal  # noqa: E402


def test_append_and_open(tmp_path: Path) -> None:
    j = TaskJournal(tmp_path)
    j.append({"token": "t1", "kind": "report", "asset_id": "pump1",
              "ts": 1.0, "status": "running"})
    j.append({"token": "t2", "kind": "learn", "asset_id": "pump1",
              "ts": 2.0, "status": "running"})
    j.append({"token": "t1", "kind": "report", "asset_id": "pump1",
              "ts": 3.0, "status": "done"})
    open_tasks = j.open_tasks()
    assert [t["token"] for t in open_tasks] == ["t2"]


def test_open_tasks_ignores_corrupt_lines(tmp_path: Path) -> None:
    j = TaskJournal(tmp_path)
    (tmp_path / ".tasks.jsonl").write_text(
        '{"token":"t1","status":"running"}\nnot json at all\n\n', encoding="utf-8")
    assert [t["token"] for t in j.open_tasks()] == ["t1"]


def test_mark_recovered_writes_terminal(tmp_path: Path) -> None:
    j = TaskJournal(tmp_path)
    j.append({"token": "t1", "kind": "report", "asset_id": "a",
              "ts": 1.0, "status": "running"})
    j.mark_recovered(["t1"])
    assert j.open_tasks() == []
    rows = [json.loads(l) for l in
            (tmp_path / ".tasks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows[-1]["status"] == "failed" and "recovered" in rows[-1]["error"]


def test_journal_file_is_hidden_in_workspace_root(tmp_path: Path) -> None:
    j = TaskJournal(tmp_path)
    j.append({"token": "t", "kind": "report", "asset_id": "a",
              "ts": 1.0, "status": "running"})
    assert (tmp_path / ".tasks.jsonl").exists()


def test_recover_pending_tasks_publishes_failed(tmp_path: Path) -> None:
    """Startup recovery: every still-open token gets an honest terminal failed
    event on the bus, then the journal is stamped so it starts clean."""
    from edge_adviser.core.bus import EventBus
    from edge_adviser.server.router_actions import recover_pending_tasks

    class _Store:
        workspace = tmp_path

    class _State:
        def __init__(self) -> None:
            self.bus = EventBus(replay=1)
            self.store = _Store()
            self.task_journal = TaskJournal(tmp_path)

    j = TaskJournal(tmp_path)
    j.append({"token": "t1", "kind": "report", "asset_id": "pump1",
              "ts": 1.0, "status": "running"})

    async def _main() -> tuple[list[dict], int]:
        state = _State()
        seen: list[dict] = []

        async def _spy(ev: dict) -> None:
            seen.append(ev)

        await state.bus.subscribe({"task"}, set(), on_event=_spy)
        n = await recover_pending_tasks(state)  # type: ignore[arg-type]
        await asyncio.sleep(0)  # let queued publish callbacks run
        return seen, n

    seen, n = asyncio.run(_main())
    assert n == 1
    task_events = [e for e in seen if e["kind"] == "task"]
    assert task_events and task_events[0]["payload"]["token"] == "t1"
    assert task_events[0]["payload"]["phase"] == "failed"
    assert "重启" in task_events[0]["payload"]["error"]
    # journal stamped clean
    assert TaskJournal(tmp_path).open_tasks() == []
