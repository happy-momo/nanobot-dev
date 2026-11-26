# EdgeAdviser 0.6.0 演进实施计划（P0→P2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地 0.6.0 演进包：P0（多架构镜像 / 任务令牌落盘 / 配置 PATCH 护栏）、P1（TLS 反代文档 / TrustedHost / PIN 暴力尝试可观测）、P2（HTML 报告导出 / 知识账本 schema 统一 / 前端格式助手去重 / /metrics 端点），外加 rewrite 期遗留的 4 个前端 minors。

**Architecture:** 后端全部为 edge_adviser 子包内的增量改动——任务持久化是一个新的 `core/task_journal.py`（jsonl append-only，重启恢复）；HTML 导出复用已归档的 `report_md`，前端已有 marked+DOMPurify 但导出走后端渲染保证离线一致；账本统一是把 demo seed 的 `ts/claim/reason` 拼写迁移到 MemoryService 的 `created_at/heading/text` 拼写（一个迁移函数 + demo seed 改写），前端删双拼。前端改动限于 `src/utils/fmt.ts` 抽取 + 断点 token 化 + QA 三态契约测试。

**Tech Stack:** Python 3.11 / FastAPI（无新后端依赖——HTML 导出用 stdlib `html.escape` 不引 markdown 库）；Vue 3 + vitest（node 环境）；Docker buildx（CI 侧）。

## Global Constraints

- 后端新端点全部挂 `/api` 前缀并被既有 PIN 中间件覆盖；`/metrics` 是唯一例外（裸文本，P1 安全模型文档化）。
- 后端零新依赖（`pyproject.toml` 的 `dependencies` 不动）；前端零新依赖（`package.json` 不动）。
- 所有写文件操作沿用 tmp + `os.replace` 原子写模式。
- 测试命令：后端 `python3.11 -m pytest edge_adviser/tests -q`（repo 根目录运行，PYTHONPATH 由 conftest 处理）；前端 `cd edge_adviser/webui && npm test && npm run typecheck`。
- 版本号推进：`edge_adviser/__init__.py` 与 `edge_adviser/pyproject.toml` 同步 0.5.2 → 0.6.0，CHANGELOG 加条目（最后一个任务统一做）。
- 提交信息格式沿用仓库惯例 `feat(edge-adviser): ...` / `fix(webui): ...`，结尾 `Co-Authored-By: Claude <noreply@anthropic.com>`。
- 设计系统约束（前端任务）：颜色只用 `var(--*)` token；数字/时间戳 `mono` + tabular-nums；触摸目标 ≥32px。

---

## Task 1: 配置 PATCH 护栏——备份 + 类型保真（P0.3）

**Files:**
- Modify: `edge_adviser/edge_adviser/server/router_assets.py:256-296`（`patch_thresholds`）
- Test: `edge_adviser/tests/test_p0_guardrails.py`（新建）

**Interfaces:**
- Consumes: 现有 `_find_config_file(state)`（router_assets.py:193）、`log_run_event(store, asset_id, kind, outcome, ...)`。
- Produces: `patch_thresholds` 在写盘前调用新的 `_backup_config(cfg_path)`（模块级函数，供测试直接调用）；备份文件命名 `<config>.bak-<YYYYmmdd-HHMMSS>`，保留最近 5 份；int 值写回后保持 int（float 仅当新值确有小数）。

- [ ] **Step 1: 写失败测试**

```python
"""P0 config-PATCH guardrails: backup + int type preservation."""
from __future__ import annotations

import json
from pathlib import Path

from edge_adviser.server.router_assets import _backup_config


def test_backup_creates_timestamped_copy(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text('{"assets": []}', encoding="utf-8")
    bak = _backup_config(cfg)
    assert bak is not None and bak.exists()
    assert bak.name.startswith("config.json.bak-")
    assert json.loads(bak.read_text(encoding="utf-8")) == {"assets": []}


def test_backup_keeps_only_five(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    for _ in range(7):
        _backup_config(cfg)
    baks = sorted(tmp_path.glob("config.json.bak-*"))
    assert len(baks) == 5


def test_backup_returns_none_when_missing(tmp_path: Path) -> None:
    assert _backup_config(tmp_path / "nope.json") is None


def _thresholds_config(tmp: Path) -> Path:
    metric = {"key": "temp", "unit": "degC", "norm_max": 68,
              "warn_max": 90, "crit_max": 95, "persist": 5, "min_samples": 30}
    cfg = {"workspace": str(tmp / "ws"), "hmi_pin": "none",
           "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                       "sample_freq_s": 1.0, "metrics": [metric]}],
           "sources": [{"kind": "simulator", "asset_id": "pump1"}]}
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_patch_preserves_int_type(tmp_path: Path) -> None:
    """68 → 68.0 drifted int to float in the demo walkthrough; the PATCH must
    write back the same JSON type the operator's value implies."""
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app
    from edge_adviser.llm.mock import MockGateway

    cfg = _thresholds_config(tmp_path)
    app = create_app(cfg, gateway=MockGateway.__new__(MockGateway))
    client = TestClient(app)
    r = client.patch("/api/assets/pump1/metrics/temp/thresholds",
                     json={"warn_max": 68})
    assert r.status_code == 200, r.text
    raw = json.loads(cfg.read_text(encoding="utf-8"))
    m = raw["assets"][0]["metrics"][0]
    assert m["warn_max"] == 68
    assert isinstance(m["warn_max"], int), f"int drifted to {type(m['warn_max'])}"
    # and a real fractional value still lands as float
    r2 = client.patch("/api/assets/pump1/metrics/temp/thresholds",
                      json={"warn_max": 68.5})
    assert r2.status_code == 200
    m2 = json.loads(cfg.read_text(encoding="utf-8"))["assets"][0]["metrics"][0]
    assert m2["warn_max"] == 68.5 and isinstance(m2["warn_max"], float)
```

注意：`MockGateway.__new__` 是为了绕过其构造函数依赖 config 的细节——若构造函数可空参调用则直接 `MockGateway()`。实现者先读 `edge_adviser/llm/mock.py` 确认；更稳妥的写法是仿照 `tests/test_server.py` 的 `StubGateway`（任意带 `run/astart/aclose` 的对象即可），把 StubGateway 复制成本文件的 12 行桩。

- [ ] **Step 2: 运行验证失败**

Run: `cd /home/pengyuhang/git0repo/nanobot-dev && python3.11 -m pytest edge_adviser/tests/test_p0_guardrails.py -q`
Expected: FAIL — `ImportError: cannot import name '_backup_config'`

- [ ] **Step 3: 实现**

在 `router_assets.py` 的 `_find_config_file` 之后加入：

```python
_CONFIG_BACKUP_KEEP = 5


def _backup_config(cfg_path: Path) -> Path | None:
    """Copy the config aside before an HMI write mutates it.

    The threshold PATCH is the one endpoint that writes the live config file;
    a bad band edited at midnight must be one `cp` away from recovery. Keeps
    the newest 5 timestamped copies next to the config (same directory, no
    hidden dotfiles — an operator greps for *.bak- without docs).
    """
    import shutil
    from datetime import datetime
    if not cfg_path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = cfg_path.with_name(f"{cfg_path.name}.bak-{stamp}")
    try:
        shutil.copy2(cfg_path, bak)
    except OSError:
        return None
    # prune to the newest _CONFIG_BACKUP_KEEP copies (name sorts by timestamp)
    baks = sorted(cfg_path.parent.glob(f"{cfg_path.name}.bak-*"))
    for old in baks[:-_CONFIG_BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return bak
```

再在 `patch_thresholds` 的 `changes` 判空块（`if not changes:` return 之前）插入备份调用，并把类型保真加进赋值循环：

```python
    if not changes:
        return ok(data={"changed": {}, "metric": metric_to_dict(metric)})

    # guardrail 1: snapshot the config aside before the first mutation lands
    _backup_config(cfg_path)

    # guardrail 2: type fidelity — an int input (68) must not drift to 68.0;
    # json.dumps would otherwise rewrite every future diff as a type change
    def _typed(name: str, v: float) -> float:
        old = mrec.get(name)
        if isinstance(old, int) and not isinstance(old, bool) \
                and float(v).is_integer():
            return int(v)
        return v

    for name, v in vals.items():
        if v is None:
            continue
        old = mrec.get(name)
        if old == v:
            continue
        changes[name] = [old, v]
        mrec[name] = _typed(name, v)
```

（删除原循环里的 `mrec[name] = v` 行，其余逻辑不动。）

- [ ] **Step 4: 运行验证通过**

Run: `python3.11 -m pytest edge_adviser/tests/test_p0_guardrails.py -q`
Expected: 4 passed

- [ ] **Step 5: 全量回归**

Run: `python3.11 -m pytest edge_adviser/tests -q`
Expected: 全绿（183+），无新增 failure

- [ ] **Step 6: Commit**

```bash
git add edge_adviser/tests/test_p0_guardrails.py edge_adviser/edge_adviser/server/router_assets.py
git commit -m "feat(edge-adviser): config PATCH guardrails — timestamped backups + int type fidelity

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: 任务令牌落盘——重启后未终结任务补发失败（P0.2）

**Files:**
- Create: `edge_adviser/edge_adviser/core/task_journal.py`
- Modify: `edge_adviser/edge_adviser/server/deps.py`（AppState 持有 journal）
- Modify: `edge_adviser/edge_adviser/server/router_actions.py`（`_task` __aenter__/__aexit__ 写 journal；startup 恢复）
- Modify: `edge_adviser/edge_adviser/server/app.py`（lifespan 启动时调用恢复）
- Test: `edge_adviser/tests/test_task_journal.py`（新建）

**Interfaces:**
- Consumes: `AppState.bus.publish(kind, asset_id, payload=...)`、`KIND_TASK`、`state.store.workspace`（journal 落 `workspace/.tasks.jsonl`）。
- Produces:
  - `class TaskJournal(workspace: Path)`，方法：`append(rec: dict) -> None`、`open_tasks() -> list[dict]`（status 非终结的最新记录）、`mark_recovered(tokens: list[str]) -> None`；
  - `AppState.task_journal: TaskJournal` 属性；
  - `_task` context manager 进入时写 `{"token","kind","asset_id","ts","status":"running"}`，退出时写 `{"token",...,"status":"done"|"failed"|"timeout","error":...}`；
  - 恢复逻辑 `recover_pending_tasks(state) -> int`（router_actions 模块级）：对 `open_tasks()` 每条向 bus 补发 `phase=failed, error="server restarted before the task finished"`，然后 `mark_recovered`。返回补发条数。

- [ ] **Step 1: 写失败测试**

```python
"""Task journal — in-flight report/learn tasks survive restart as honest failures."""
from __future__ import annotations

import json
from pathlib import Path

from edge_adviser.core.task_journal import TaskJournal


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
```

- [ ] **Step 2: 运行验证失败**

Run: `python3.11 -m pytest edge_adviser/tests/test_task_journal.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'edge_adviser.core.task_journal'`

- [ ] **Step 3: 实现 task_journal.py**

```python
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
            self.append({"token": tok, "status": "recovered",
                         "error": "recovered by server startup"})
```

- [ ] **Step 4: 运行验证通过**

Run: `python3.11 -m pytest edge_adviser/tests/test_task_journal.py -q`
Expected: 4 passed

- [ ] **Step 5: 写恢复路径的失败测试（第二个测试块）**

追加到 `test_task_journal.py`：

```python
def test_recover_pending_tasks_publishes_failed(tmp_path: Path) -> None:
    """Startup recovery: every still-open token gets an honest terminal failed
    event on the bus, then the journal is stamped so it starts clean."""
    import asyncio
    from edge_adviser.core.bus import EventBus, KIND_TASK
    from edge_adviser.server.router_actions import recover_pending_tasks

    class _Store:
        workspace = tmp_path

    class _State:
        bus = EventBus(replay=1)
        store = _Store()

    j = TaskJournal(tmp_path)
    j.append({"token": "t1", "kind": "report", "asset_id": "pump1",
              "ts": 1.0, "status": "running"})

    async def _main() -> list[dict]:
        seen: list[dict] = []

        async def _spy(ev: dict) -> None:
            seen.append(ev)

        await _State.bus.subscribe({"task"}, set(), on_event=_spy)
        n = await recover_pending_tasks(_State())  # type: ignore[arg-type]
        await asyncio.sleep(0)  # let the queued publish callbacks run
        return seen, n

    seen, n = asyncio.run(_main())
    assert n == 1
    task_events = [e for e in seen if e["kind"] == "task"]
    assert task_events and task_events[0]["payload"]["token"] == "t1"
    assert task_events[0]["payload"]["phase"] == "failed"
    assert "restart" in task_events[0]["payload"]["error"]
    # journal stamped clean
    assert TaskJournal(tmp_path).open_tasks() == []
```

- [ ] **Step 6: 运行验证失败**

Run: `python3.11 -m pytest edge_adviser/tests/test_task_journal.py::test_recover_pending_tasks_publishes_failed -q`
Expected: FAIL — `ImportError: cannot import name 'recover_pending_tasks'`

- [ ] **Step 7: 实现恢复函数 + 接线**

在 `router_actions.py` 末尾追加：

```python
async def recover_pending_tasks(state: AppState) -> int:
    """Startup: journal entries still `running` from a previous process get an
    honest terminal failed event — the client's watchdog would otherwise blame
    the provider for an operator-side restart. Returns the recovered count."""
    journal = state.task_journal
    pending = journal.open_tasks()
    for rec in pending:
        await state.bus.publish(
            KIND_TASK, rec.get("asset_id", ""),
            payload={"token": rec["token"], "kind": rec.get("kind", ""),
                     "phase": "failed",
                     "error": "服务器在任务完成前重启，结果未知；请重新触发"})
    journal.mark_recovered([r["token"] for r in pending])
    return len(pending)
```

修改 `_task.__aenter__`/`__aexit__`（router_actions.py:272-298）写 journal：

```python
    async def __aenter__(self):
        await self.state.bus.publish(KIND_TASK, self.asset_id,
                                     payload={"token": self.token, "kind": self.kind,
                                              "phase": "start"})
        self.state.task_journal.append({
            "token": self.token, "kind": self.kind,
            "asset_id": self.asset_id, "status": "running"})
        return self
```

`__aexit__` 在现有 publish 之后追加一行：

```python
        await self.state.bus.publish(KIND_TASK, self.asset_id, payload=payload)
        self.state.task_journal.append({
            "token": self.token, "kind": self.kind,
            "asset_id": self.asset_id, "status": phase,
            "error": payload.get("error", "")})
```

`deps.py` 的 `AppState.__init__` 末尾（`self.supervisor = ...` 之后）加：

```python
        from .task_journal_placeholder import noop  # ← NOT THIS — see below
```

实际写法（导入放文件顶部 import 区）：

```python
from ..core.task_journal import TaskJournal
```

并在 `__init__` 内加：

```python
        self.task_journal = TaskJournal(store.workspace)
```

`app.py` lifespan 中 `await state.start()` 之后加：

```python
        from .router_actions import recover_pending_tasks
        await recover_pending_tasks(state)
```

- [ ] **Step 8: 运行验证通过 + 全量回归**

Run: `python3.11 -m pytest edge_adviser/tests/test_task_journal.py edge_adviser/tests/test_server.py -q`
Expected: 全绿

Run: `python3.11 -m pytest edge_adviser/tests -q`
Expected: 全绿

- [ ] **Step 9: Commit**

```bash
git add edge_adviser/edge_adviser/core/task_journal.py edge_adviser/edge_adviser/server/deps.py edge_adviser/edge_adviser/server/router_actions.py edge_adviser/edge_adviser/server/app.py edge_adviser/tests/test_task_journal.py
git commit -m "feat(edge-adviser): persist task tokens — restart recovers in-flight tasks as honest failures

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: PIN 暴力尝试可观测 + TrustedHost（P1.5 + P1.6）

**Files:**
- Modify: `edge_adviser/edge_adviser/server/security.py`（PinRateLimiter 暴露计数）
- Modify: `edge_adviser/edge_adviser/server/app.py`（version 端点透出；TrustedHost 中间件）
- Modify: `edge_adviser/edge_adviser/server/router_overview.py:157-173`（/api/version 增加 pin_lockout / pin_failures）
- Modify: `edge_adviser/config/model.py`（AdviserConfig 增加 `trusted_hosts`）+ `config/loader.py`（解析）
- Modify: `edge_adviser/config.sample.json`（示例字段）
- Test: `edge_adviser/tests/test_p1_security.py`（新建）

**Interfaces:**
- Consumes: `PinRateLimiter`（app.py:140 实例 `pin_limiter`）、`create_app`。
- Produces:
  - `PinRateLimiter.total_failures: int` 属性（进程生命周期累计，不受窗口清零影响）；
  - `/api/version` 响应新增 `"pin_lockout_s": int`（当前剩余锁定秒数）与 `"pin_failed_attempts": int`（累计失败次数）——pin 未启用时两个键为 0；
  - `AdviserConfig.trusted_hosts: list[str]`（默认 `[]` = 不启用）；非空时 app 注册 `TrustedHostMiddleware`。

- [ ] **Step 1: 写失败测试**

```python
"""P1 security: PIN attempt observability + optional TrustedHost gate."""
from __future__ import annotations

import json
from pathlib import Path

from edge_adviser.server.security import PinRateLimiter


def test_total_failures_survives_window_expiry() -> None:
    limiter = PinRateLimiter(max_failures=2, window_s=100, lockout_s=1)
    limiter.record_failure(now=1.0)
    limiter.record_failure(now=500.0)  # first failure aged out of the window
    assert limiter.total_failures == 2


def test_success_does_not_reset_total() -> None:
    limiter = PinRateLimiter()
    limiter.record_failure(now=1.0)
    limiter.record_success()
    assert limiter.total_failures == 1


def _cfg(tmp: Path, *, trusted: list[str] | None = None) -> Path:
    cfg = {"workspace": str(tmp / "ws"), "hmi_pin": "none",
           "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                       "sample_freq_s": 1.0,
                       "metrics": [{"key": "temp", "unit": "degC",
                                    "norm_max": 68, "persist": 5}]}],
           "sources": [{"kind": "simulator", "asset_id": "pump1"}]}
    if trusted is not None:
        cfg["trusted_hosts"] = trusted
    p = tmp / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


def test_version_exposes_pin_attempt_counters(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    client = TestClient(create_app(_cfg(tmp_path, trusted=[]), gateway=_Stub()))
    r = client.get("/api/version")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["pin_failed_attempts"] == 0
    assert data["pin_lockout_s"] == 0
    # a wrong PIN bumps the counter (pin disabled here → exchange reports
    # enabled:false and no limiter is hit; enable PIN to exercise the path)
    cfg = _cfg(tmp_path / "pin", trusted=[])
    cfg.write_text(json.dumps({
        **json.loads(cfg.read_text(encoding="utf-8")), "hmi_pin": "1357"}),
        encoding="utf-8")
    client2 = TestClient(create_app(cfg, gateway=_Stub()))
    assert client2.post("/api/auth/pin", json={"pin": "0000"}).status_code == 401
    v = client2.get("/api/version").json()["data"]
    assert v["pin_failed_attempts"] == 1


def test_trusted_host_rejects_foreign_host(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    client = TestClient(create_app(_cfg(tmp_path, trusted=["plant.local:8000"]),
                                   gateway=_Stub()))
    ok = client.get("/api/version", headers={"Host": "plant.local:8000"})
    bad = client.get("/api/version", headers={"Host": "evil.example"})
    assert ok.status_code == 200
    assert bad.status_code == 400


class _Stub:
    """Minimal gateway stub (same shape test_server.py uses)."""
    async def astart(self): pass
    async def aclose(self): pass
    async def run(self, prompt, **kw): return {}
```

- [ ] **Step 2: 运行验证失败**

Run: `python3.11 -m pytest edge_adviser/tests/test_p1_security.py -q`
Expected: FAIL — `AttributeError: 'PinRateLimiter' object has no attribute 'total_failures'`

- [ ] **Step 3: 实现**

`security.py` 的 `PinRateLimiter.__init__` 加 `self.total_failures = 0`，`record_failure` 首行加 `self.total_failures += 1`。

`config/model.py` 的 `AdviserConfig`（hmi_pin 字段附近）加：

```python
    # non-empty list enables FastAPI TrustedHostMiddleware (Host header allowlist);
    # [] keeps today's behavior (any Host accepted) — the LAN is the boundary
    trusted_hosts: list[str] = []
```

`config/loader.py` 的 `AdviserConfig(...)` 构造（hmi_pin 行后）加 `trusted_hosts=raw.get("trusted_hosts", []),`。

`router_overview.py` 的 `version` 端点（167 行 `return` 前）注入计数。需要 state 上的 limiter：`app.py` 创建 `pin_limiter` 后加 `state.pin_limiter = pin_limiter`；version 端点改为：

```python
@router.get("/version")
async def version(request: Request) -> Any:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
    state: AppState = await _state(request)
    try:
        ver = _pkg_version("edge-adviser")
    except PackageNotFoundError:
        from .. import __version__ as ver  # fallback: repo checkout
    limiter = getattr(state, "pin_limiter", None)
    return ok(data={
        "version": ver,
        # ... 既有字段保持不动 ...
        "pin_failed_attempts": getattr(limiter, "total_failures", 0) if limiter else 0,
        "pin_lockout_s": int(getattr(limiter, "locked", lambda: 0.0)()) if limiter else 0,
    })
```

（实现者读 157-173 行现状，把两个新键加进既有 dict，不重排既有键。）

`app.py`：`pin_limiter = PinRateLimiter()` 行后加 `state.pin_limiter = pin_limiter`；在 `app = FastAPI(...)` 与 PIN gate 中间件之间加：

```python
    if state.config.trusted_hosts:
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=state.config.trusted_hosts)
```

`config.sample.json` 顶层加（hmi_pin 附近）：

```json
  "trusted_hosts": [],
```

- [ ] **Step 4: 运行验证通过 + 回归**

Run: `python3.11 -m pytest edge_adviser/tests/test_p1_security.py -q` → 4 passed
Run: `python3.11 -m pytest edge_adviser/tests -q` → 全绿

- [ ] **Step 5: Commit**

```bash
git add edge_adviser/edge_adviser/server/security.py edge_adviser/edge_adviser/server/app.py edge_adviser/edge_adviser/server/router_overview.py edge_adviser/edge_adviser/config/model.py edge_adviser/edge_adviser/config/loader.py edge_adviser/config.sample.json edge_adviser/tests/test_p1_security.py
git commit -m "feat(edge-adviser): PIN attempt counters in /version + optional TrustedHost gate

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: /metrics Prometheus 端点（P2.10）

**Files:**
- Create: `edge_adviser/edge_adviser/server/router_metrics.py`
- Modify: `edge_adviser/edge_adviser/server/app.py`（include_router）
- Test: `edge_adviser/tests/test_metrics.py`（新建）

**Interfaces:**
- Consumes: `AppState`（bus、config、store）、`_provider_stats`/`_mtta_s` 的聚合口径（router_stats.py:66-102，复制聚合逻辑而非 import 私有函数——两个模块各自演进，避免跨 router 耦合）。
- Produces: `GET /metrics` 返回 `text/plain; version=0.0.4` Prometheus 文本，指标族：
  - `edge_adviser_assets`（gauge，当前启用资产数）
  - `edge_adviser_snapshots_total{asset_id}`（counter，读盘）
  - `edge_adviser_provider_calls_total` / `edge_adviser_provider_ok_total` / `edge_adviser_gated_refusals_total`（counter，runlog 聚合）
  - `edge_adviser_alerts_unacked`（gauge）
  - `edge_adviser_scheduler_last_tick_age_seconds`（gauge，`state.last_tick`）
  - `edge_adviser_sse_streams_active`（gauge，见 Step 3 的连接计数）
  - `edge_adviser_pin_failed_attempts_total`（counter）
- 该端点在 PIN 启用时**仍受 PIN 保护**（不是豁免路径）；监控抓取用 `?token=` 查询参数（GET 既有豁免规则已允许）。

- [ ] **Step 1: 写失败测试**

```python
"""/metrics — Prometheus text exposition (no new dependency)."""
from __future__ import annotations

import json
from pathlib import Path

from edge_adviser.server.router_metrics import render_metrics


def test_render_metrics_shape() -> None:
    text = render_metrics(
        assets=2,
        snapshots={"pump1": 12, "feeder1": 3},
        provider_calls=10, provider_ok=9, gated=1,
        unacked=2, tick_age=12.5, sse_active=3, pin_failures=0)
    assert "# TYPE edge_adviser_assets gauge" in text
    assert "edge_adviser_assets 2" in text
    assert 'edge_adviser_snapshots_total{asset_id="pump1"} 12' in text
    assert "edge_adviser_provider_calls_total 10" in text
    assert "edge_adviser_provider_ok_total 9" in text
    assert "edge_adviser_gated_refusals_total 1" in text
    assert "edge_adviser_alerts_unacked 2" in text
    assert "edge_adviser_scheduler_last_tick_age_seconds 12.5" in text
    assert "edge_adviser_sse_streams_active 3" in text
    assert "edge_adviser_pin_failed_attempts_total 0" in text


def test_sse_active_counter_tracks_streams(tmp_path: Path) -> None:
    import asyncio
    from edge_adviser.server.router_metrics import sse_active
    from edge_adviser.core.bus import EventBus

    async def _main() -> int:
        bus = EventBus(replay=1)
        opened = []

        async def sink(ev): pass

        t1 = await bus.subscribe({"snapshot"}, set(), on_event=sink)
        t2 = await bus.subscribe({"alert"}, set(), on_event=sink)
        opened = sse_active(bus)
        await bus.unsubscribe(t1)
        after = sse_active(bus)
        return opened, after

    opened, after = asyncio.run(_main())
    assert opened == 2 and after == 1


def test_metrics_endpoint_requires_pin(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    cfg = {"workspace": str(tmp_path / "ws"), "hmi_pin": "1357",
           "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                       "sample_freq_s": 1.0,
                       "metrics": [{"key": "temp", "unit": "degC",
                                    "norm_max": 68, "persist": 5}]}],
           "sources": [{"kind": "simulator", "asset_id": "pump1"}]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    client = TestClient(create_app(p, gateway=_Stub()))
    r = client.post("/api/auth/pin", json={"pin": "1357"})
    token = r.json()["data"]["token"]
    assert client.get("/metrics").status_code == 401
    ok = client.get("/metrics", params={"token": token})
    assert ok.status_code == 200
    assert "edge_adviser_assets" in ok.text


class _Stub:
    async def astart(self): pass
    async def aclose(self): pass
    async def run(self, prompt, **kw): return {}
```

- [ ] **Step 2: 运行验证失败**

Run: `python3.11 -m pytest edge_adviser/tests/test_metrics.py -q`
Expected: FAIL — `ModuleNotFoundError: ... router_metrics`

- [ ] **Step 3: 实现**

`bus.py` 的 `EventBus` 加一个只读计数（`__init__` 不动，新增方法）：

```python
    def watcher_count(self) -> int:
        return len(self._watchers)
```

`router_metrics.py`：

```python
"""/metrics — Prometheus text exposition for plant monitoring stacks.

Hand-rolled text format (metrics exposition 0.0.4) rather than prometheus-client:
the gauges here are aggregates over runlog/snapshots that /api/stats already
computes, and a client library would drag in a dependency the edge image does
not need. The endpoint is PIN-protected like every other /api surface; scrapers
use the existing ?token= GET allowance (EventSource precedent).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ..core.runlog import read_runlog  # if absent, inline a local jsonl reader
from .deps import AppState

router = APIRouter(tags=["metrics"])

# count active SSE streams globally — incremented in streaming.stream_bus, kept
# as a module counter because the EventBus also serves internal subscribers
_sse_streams = 0


def sse_active(bus: Any) -> int:
    """Live SSE streams = bus watchers minus internal subscribers (tests may
    pass a bus directly)."""
    return bus.watcher_count()


def render_metrics(*, assets: int, snapshots: dict[str, int],
                   provider_calls: int, provider_ok: int, gated: int,
                   unacked: int, tick_age: float | None,
                   sse_active_n: int, pin_failures: int) -> str:
    lines: list[str] = []

    def gauge(name: str, help_: str, val: Any) -> None:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {val}")

    def counter(name: str, help_: str, val: Any) -> None:
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {val}")

    gauge("edge_adviser_assets", "enabled assets", assets)
    lines.append("# HELP edge_adviser_snapshots_total archived snapshots per asset")
    lines.append("# TYPE edge_adviser_snapshots_total gauge")
    for aid, n in sorted(snapshots.items()):
        lines.append(f'edge_adviser_snapshots_total{{asset_id="{aid}"}} {n}')
    counter("edge_adviser_provider_calls_total", "LLM provider calls audited", provider_calls)
    counter("edge_adviser_provider_ok_total", "LLM provider calls that returned ok", provider_ok)
    counter("edge_adviser_gated_refusals_total", "deterministic gate refusals", gated)
    gauge("edge_adviser_alerts_unacked", "alerts awaiting acknowledgement", unacked)
    gauge("edge_adviser_scheduler_last_tick_age_seconds", "age of last scheduler tick",
          -1 if tick_age is None else round(tick_age, 1))
    gauge("edge_adviser_sse_streams_active", "open SSE streams", sse_active_n)
    counter("edge_adviser_pin_failed_attempts_total", "wrong PIN exchanges", pin_failures)
    return "\n".join(lines) + "\n"


async def _state(request: Request) -> AppState:
    return request.app.state.edge


@router.get("/metrics")
async def metrics(request: Request) -> Any:
    state: AppState = await _state(request)
    from .router_stats import _provider_stats, _mtta_s  # module-private but same package
    snap_counts: dict[str, int] = {}
    provider_rows: list[dict[str, Any]] = []
    unacked = 0
    for asset in state.config.assets:
        adir = state.store.asset_dir(asset.asset_id)
        snap_counts[asset.asset_id] = len(state.store.list_snapshots(asset.asset_id, limit=100000))
        rl_path = adir / "runlog.jsonl"
        rows: list[dict[str, Any]] = []
        if rl_path.exists():
            try:
                rows = [json.loads(l) for l in rl_path.read_text(encoding="utf-8").splitlines()
                        if l.strip()]
            except Exception:  # noqa: BLE001
                rows = []
        provider_rows.extend(rows)
        ap = adir / "alerts.jsonl"
        if ap.exists():
            try:
                unacked += sum(1 for l in ap.read_text(encoding="utf-8").splitlines()
                               if l.strip() and not json.loads(l).get("acked"))
            except Exception:  # noqa: BLE001
                pass
    prov = _provider_stats(provider_rows)
    tick_age = (time.time() - state.last_tick) if state.last_tick else None
    limiter = getattr(state, "pin_limiter", None)
    body = render_metrics(
        assets=len([a for a in state.config.assets if a.enabled]),
        snapshots=snap_counts,
        provider_calls=prov["calls"], provider_ok=prov["ok"],
        gated=prov["gated_refusals"],
        unacked=unacked, tick_age=tick_age,
        sse_active_n=sse_active(state.bus),
        pin_failures=getattr(limiter, "total_failures", 0) if limiter else 0)
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")
```

注意两处修正：① 顶部 `from ..core.runlog import read_runlog` 若该函数不存在则**删除该 import**（本文件内已内联读取）；② `sse_active` 直接用 `bus.watcher_count()`——内部订阅者（scheduler 的 relay）也算进来没关系，指标语义是"bus 活跃订阅数"。

`app.py` include 区（configuration_router 之后）：

```python
    from .router_metrics import router as metrics_router
    app.include_router(metrics_router)
```

`router_overview.py` 的 `/api/health` 若有 `sse_streams` 字段可保持不动（不强制）。

- [ ] **Step 4: 运行验证通过 + 回归**

Run: `python3.11 -m pytest edge_adviser/tests/test_metrics.py -q` → 3 passed
Run: `python3.11 -m pytest edge_adviser/tests -q` → 全绿

- [ ] **Step 5: Commit**

```bash
git add edge_adviser/edge_adviser/server/router_metrics.py edge_adviser/edge_adviser/server/app.py edge_adviser/edge_adviser/core/bus.py edge_adviser/tests/test_metrics.py
git commit -m "feat(edge-adviser): /metrics Prometheus exposition (stdlib, PIN-protected)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: HTML 报告导出（P2.7）

**Files:**
- Create: `edge_adviser/edge_adviser/report/html_export.py`
- Modify: `edge_adviser/edge_adviser/server/router_reports.py`（新增 download.html 端点）
- Modify: `edge_adviser/webui/src/api/index.ts:228-232`（downloadReport 支持 'html'）
- Modify: `edge_adviser/webui/src/views/asset/TabReports.vue:203-212`（加下载 HTML 按钮）
- Test: `edge_adviser/tests/test_html_export.py`（新建）

**Interfaces:**
- Consumes: `state.store.read_report(asset_id, date)` 返回 `{date, report_md, meta, snapshot, degraded, provider}`。
- Produces:
  - `report/html_export.py` 的 `render_report_html(report_md: str, meta: dict | None, *, asset_id: str, date: str, provider: str = "", degraded: bool = False) -> str`（纯函数，无 IO，可直接单测）；
  - 端点 `GET /api/assets/{asset_id}/reports/{date}/download.html`，`Content-Type: text/html; charset=utf-8`，`Content-Disposition: attachment; filename="{asset_id}-{date}.html"`；
  - 前端 `downloadReport(id, date, 'html' | 'md' | 'json')`。

- [ ] **Step 1: 写失败测试**

```python
"""HTML report export — stdlib-only rendering of the archived markdown."""
from __future__ import annotations

from edge_adviser.report.html_export import render_report_html


def test_escapes_html_in_markdown() -> None:
    out = render_report_html("# T <script>alert(1)</script>", None,
                             asset_id="pump1", date="2026-11-26")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_includes_provenance_header() -> None:
    meta = {"status": "healthy", "trust": "full", "sample_count": 60,
            "coverage": 1.0, "window_end": "2026-11-26T06:00:00+00:00"}
    out = render_report_html("# 报告", meta, asset_id="pump1",
                             date="2026-11-26", provider="qwen", degraded=False)
    assert "pump1" in out and "2026-11-26" in out
    assert "qwen" in out
    assert "healthy" in out


def test_degraded_flag_visible() -> None:
    out = render_report_html("# t", None, asset_id="a", date="d", degraded=True)
    assert "降级" in out


def test_markdown_basics_render() -> None:
    out = render_report_html("# 标题\n\n- 项目一\n- 项目二\n\n**加粗**", None,
                             asset_id="a", date="d")
    assert "<h1>" in out
    assert "<li>" in out
    assert "<strong>" in out
```

- [ ] **Step 2: 运行验证失败**

Run: `python3.11 -m pytest edge_adviser/tests/test_html_export.py -q`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 实现 html_export.py**

约束：零依赖 markdown→HTML。归档报告的 markdown 是模型生成的规范输出（标题/列表/表格/加粗），渲染器只需覆盖这个受控子集，未覆盖的语法按原文转义输出——绝不 `eval` 原始 HTML。

```python
"""Report → standalone HTML (handoff/print use case).

Zero-dependency markdown subset renderer: the archived reports are model output
constrained to headings/lists/tables/bold/code, and anything outside that subset
is escaped verbatim rather than interpreted — an industrial report must never
gain an XSS vector by being re-rendered. The deterministic DATA-SCOPE front-matter
is lifted out of the markdown and rendered as a provenance table header.
"""

from __future__ import annotations

import html
import re
from typing import Any


def _extract_frontmatter(md: str) -> tuple[str, str]:
    """Split the machine DATA-SCOPE comment block off the body."""
    m = re.match(r"\s*<!-- DATA-SCOPE.*?-->\s*", md, re.DOTALL)
    if not m:
        return "", md
    return m.group(0), md[m.end():]


def _inline(s: str) -> str:
    """Bold/italic/code on an ALREADY-escaped string."""
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _render_body(md: str) -> str:
    out: list[str] = []
    list_open = False
    table_rows: list[list[str]] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                continue  # separator row
            table_rows.append(cells)
            continue
        if table_rows:
            out.append(_flush_table(table_rows))
            table_rows = []
        esc = html.escape(line)
        if not esc.strip():
            if list_open:
                out.append("</ul>")
                list_open = False
            continue
        h = re.match(r"^(#{1,4})\s+(.*)$", line)
        if h:
            if list_open:
                out.append("</ul>")
                list_open = False
            level = len(h.group(1))
            out.append(f"<h{level}>{_inline(html.escape(h.group(2)))}</h{level}>")
            continue
        li = re.match(r"^[-*]\s+(.*)$", line)
        if li:
            if not list_open:
                out.append("<ul>")
                list_open = True
            out.append(f"<li>{_inline(html.escape(li.group(1)))}</li>")
            continue
        if list_open:
            out.append("</ul>")
            list_open = False
        out.append(f"<p>{_inline(esc)}</p>")
    if table_rows:
        out.append(_flush_table(table_rows))
    if list_open:
        out.append("</ul>")
    return "\n".join(out)


def _flush_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head, *body = rows
    th = "".join(f"<th>{_inline(html.escape(c))}</th>" for c in head)
    trs = "".join(
        "<tr>" + "".join(f"<td>{_inline(html.escape(c))}</td>" for c in r) + "</tr>"
        for r in body)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


_PROV_KEYS = [
    ("status", "状态"), ("trust", "信任"), ("sample_count", "样本数"),
    ("expected_samples", "应有样本"), ("coverage", "覆盖率"),
    ("window_start", "窗口起点"), ("window_end", "窗口终点"),
    ("provider", "模型"), ("degraded", "降级"),
]


def render_report_html(report_md: str, meta: dict[str, Any] | None, *,
                       asset_id: str, date: str, provider: str = "",
                       degraded: bool = False) -> str:
    """Render an archived report into a self-contained HTML string."""
    meta = meta or {}
    prov: dict[str, Any] = {}
    for k in ("status", "trust", "sample_count", "expected_samples", "coverage",
              "window_start", "window_end", "state_reason"):
        if meta.get(k) is not None:
            prov[k] = meta[k]
    prov["provider"] = provider
    prov["degraded"] = degraded

    prov_rows = "".join(
        f"<tr><th>{label}</th><td>{html.escape(str(prov[k]))}</td></tr>"
        for k, label in _PROV_KEYS if k in prov)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(asset_id)} · {html.escape(date)} · EdgeAdviser 报告</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
         margin: 32px auto; max-width: 760px; color: #17202a; line-height: 1.6; }}
  h1 {{ font-size: 20px; border-bottom: 2px solid #3BE0D8; padding-bottom: 6px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0;
          font-size: 13px; }}
  th, td {{ border: 1px solid #cfd8dc; padding: 6px 10px; text-align: left; }}
  thead th {{ background: #eceff1; }}
  .prov {{ background: #f4fbfb; }}
  .prov th {{ width: 110px; color: #546e7a; font-weight: 500; }}
  .degraded {{ color: #b45309; font-weight: 600; }}
  footer {{ margin-top: 28px; font-size: 11px; color: #78909c; }}
  code {{ background: #eceff1; padding: 1px 5px; border-radius: 3px;
         font-size: 12px; }}
</style>
</head>
<body>
<h1>{html.escape(asset_id)} · {html.escape(date)} 设备报告
{'<span class="degraded">（已降级）</span>' if degraded else ''}</h1>
<table class="prov">{prov_rows}</table>
{_render_body(_extract_frontmatter(report_md)[1])}
<footer>由 EdgeAdviser 生成 · 数据范围以表中确定性字段为准，正文为模型解读</footer>
</body>
</html>"""
```

- [ ] **Step 4: 运行验证通过**

Run: `python3.11 -m pytest edge_adviser/tests/test_html_export.py -q`
Expected: 4 passed

- [ ] **Step 5: 端点接线 + 失败测试**

追加测试：

```python
def test_download_html_endpoint(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient
    from edge_adviser.server.app import create_app

    ws = tmp_path / "ws"
    day = ws / "assets" / "pump1" / "reports" / "2026-11-26"
    day.mkdir(parents=True)
    (day / "report.md").write_text("# 报告\n\n- 结论", encoding="utf-8")
    cfg = {"workspace": str(ws), "hmi_pin": "none",
           "assets": [{"asset_id": "pump1", "name": "p", "window_s": 60,
                       "sample_freq_s": 1.0,
                       "metrics": [{"key": "temp", "unit": "degC",
                                    "norm_max": 68, "persist": 5}]}],
           "sources": [{"kind": "simulator", "asset_id": "pump1"}]}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    client = TestClient(create_app(p, gateway=_Stub()))
    r = client.get("/api/assets/pump1/reports/2026-11-26/download.html")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "设备报告" in r.text
```

**先运行确认失败**（404），再在 `router_reports.py` 的 `download_report_json` 后加：

```python
@router.get("/assets/{asset_id}/reports/{date}/download.html")
async def download_report_html(request: Request, asset_id: str, date: str) -> Any:
    """Standalone HTML export — offline handoff / print, no app shell needed."""
    state: AppState = await _state(request)
    r = state.store.read_report(asset_id, date)
    if r is None or not r.get("report_md"):
        return fail("report not found", status=404)
    from ..report.html_export import render_report_html
    body = render_report_html(
        r["report_md"], r.get("meta"), asset_id=asset_id, date=date,
        provider=r.get("provider") or "", degraded=bool(r.get("degraded")))
    return Response(body, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="{asset_id}-{date}.html"'})
```

（`Response` 从 `fastapi.responses` 导入——文件顶部已引 `PlainTextResponse`，同一行加上。）

Run: `python3.11 -m pytest edge_adviser/tests/test_html_export.py -q` → 5 passed

- [ ] **Step 6: 前端接线**

`webui/src/api/index.ts` 的 `downloadReport` 改为：

```ts
export const downloadReport = (id: string, date: string, kind: 'md' | 'json' | 'html') =>
  downloadFile(`/api/assets/${id}/reports/${date}/download.${kind}`,
               `${id}-${date}.${kind}`)
```

`webui/src/views/asset/TabReports.vue`：`downloading` ref 类型改 `ref<'md' | 'json' | 'html' | null>(null)`；`download` 函数签名改 `kind: 'md' | 'json' | 'html'`；下载按钮组加第三个按钮（在 .json 按钮后）：

```html
          <button
            type="button"
            class="tr-btn"
            :disabled="!selectedDate || downloading !== null"
            @click="download('html')"
          >
            <Download :size="13" :stroke-width="1.5" />
            <span>{{ downloading === 'html' ? '下载中…' : '下载 .html' }}</span>
          </button>
```

- [ ] **Step 7: 前端验证**

Run: `cd edge_adviser/webui && npm run typecheck && npm test && npm run build`
Expected: 全部通过

- [ ] **Step 8: 全量回归 + Commit**

Run: `python3.11 -m pytest edge_adviser/tests -q` → 全绿

```bash
git add edge_adviser/edge_adviser/report/html_export.py edge_adviser/edge_adviser/server/router_reports.py edge_adviser/tests/test_html_export.py edge_adviser/webui/src/api/index.ts edge_adviser/webui/src/views/asset/TabReports.vue
git commit -m "feat(edge-adviser): standalone HTML report export — stdlib renderer + download endpoint

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: 知识账本 schema 统一（P2.8）

**Files:**
- Modify: `edge_adviser/edge_adviser/server/demo.py:250-277`（seed 写统一拼写）
- Create: `edge_adviser/edge_adviser/core/ledger_migrate.py`
- Modify: `edge_adviser/edge_adviser/server/router_assets.py:132-160`（knowledge 端点输出前先跑迁移读取器）
- Modify: `edge_adviser/webui/src/views/asset/TabKnowledge.vue`（删双拼）
- Test: `edge_adviser/tests/test_ledger_schema.py`（新建）

**Interfaces:**
- Consumes: `MemoryConflictLedger` 的文件布局（`assets/<id>/memory_claims.jsonl` / `memory_conflicts.jsonl`）。
- Produces:
  - **唯一 schema**（v2，与生产 MemoryService 一致）：claim = `{id, heading, text, created_at, source_window, status}`；conflict = `{id, heading, old, new, created_at}`；
  - `ledger_migrate.normalize_records(rows: list[dict]) -> list[dict]`（纯函数：把 v1 demo 拼写映射到 v2；已合规的记录原样返回）；
  - `ledger_migrate.migrate_file(path: Path) -> int`（就地迁移，返回迁移行数；写入用 tmp+replace 原子替换）；
  - knowledge 端点对**读取到的每一行**先 `normalize_records` 再返回——旧 workspace 不迁移文件也能渲染；
  - 前端 `ClaimRec` 收敛为单拼写 `{id, heading, text, created_at, status, source_window}`。

- [ ] **Step 1: 写失败测试**

```python
"""Knowledge ledger schema unification — one spelling for both producers."""
from __future__ import annotations

import json
from pathlib import Path

from edge_adviser.core.ledger_migrate import normalize_records, migrate_file


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
```

- [ ] **Step 2: 运行验证失败**

Run: `python3.11 -m pytest edge_adviser/tests/test_ledger_schema.py -q`
Expected: FAIL — ModuleNotFoundError

- [ ] **Step 3: 实现 ledger_migrate.py**

```python
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
    if "reason" not in rec and "ts" not in rec:
        return rec  # already v2
    return {
        "id": rec.get("id", ""),
        "heading": rec.get("heading", ""),
        # v1 semantics: claim_id points at the superseded claim, reason is the
        # human evidence — mapped onto v2's old/new pair
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
```

- [ ] **Step 4: 运行验证通过**

Run: `python3.11 -m pytest edge_adviser/tests/test_ledger_schema.py -q` → 4 passed

- [ ] **Step 5: 改 demo seed + knowledge 端点**

`demo.py` 的 claims/conflicts seed 块（258-277 行）改为写 v2：

```python
    claims = [
        {"id": "c1", "heading": "振动均值稳定在 2.8~3.2 区间",
         "text": "振动均值稳定在 2.8~3.2 区间",
         "created_at": now_iso, "source_window": "learn", "status": "active"},
        {"id": "c2", "heading": "联轴器对中后振动应低于 2.5",
         "text": "联轴器对中后振动应低于 2.5",
         "created_at": now_iso, "source_window": "feedback", "status": "stale"},
    ]
    conflicts = [
        {"id": "f1", "heading": "联轴器对中后振动应低于 2.5",
         "old": "c2", "new": "当前振动均值高于该声明值",
         "created_at": now_iso},
    ]
```

`router_assets.py` 的 `knowledge` 端点：读取循环内 `rec = json.loads(line)` 后加规范化：

```python
                    rec = json.loads(line)
                    from ..core.ledger_migrate import normalize_records
                    rec = normalize_records([rec], cls=cls)[0]
                    rec.setdefault("cls", cls)
```

（import 提到文件顶部，循环内只留 `rec = normalize_records([rec], cls=cls)[0]`。）

- [ ] **Step 6: 前端删双拼**

`TabKnowledge.vue`：

```ts
// Ledger records come from ONE producer spelling (v2, memory_service.py):
// claim = {id, heading, text, created_at, source_window, status}
// conflict = {id, heading, old, new, created_at}. The demo seed writes the
// same spelling (0.6.0 schema unification) and the knowledge endpoint
// normalizes legacy v1 rows at the API boundary.
interface ClaimRec {
  id?: string
  heading?: string
  text?: string
  created_at?: string
  source_window?: string
  status?: string
}
interface ConflictRec {
  id?: string
  heading?: string
  old?: string
  new?: string
  created_at?: string
}
```

同时把模板/脚本里所有 `c.claim`、`c.ts`、`r.reason`、`r.claim_id`、`r.ts` 引用改为 `c.heading`、`c.created_at`、`r.old`/`r.new`/`r.created_at`。`claimText` 函数简化为（v2 text 不再需要去重前缀的 fallback 链——保留生产 text 前缀裁剪）：

```ts
const claimText = (c: ClaimRec): string => {
  // production `text` duplicates its heading prefix (memory_service writes
  // title + " " + body); trim it so the heading isn't shown twice.
  if (c.text) {
    const body = c.heading && c.text.startsWith(c.heading + ' ')
      ? c.text.slice(c.heading.length + 1) : c.text
    if (body.trim()) return body
  }
  return c.heading || '(无正文)'
}
```

时间显示：所有 `fmtTs(...ts)` 改为 `fmtTs(...created_at)`（`created_at` 是 ISO 字符串；若现有 `fmtTs` 只接 epoch 秒，新增一个 `fmtIso(iso: string)`——`new Date(iso)` 后同格式化，Invalid 显示 `—`）。

- [ ] **Step 7: 验证**

Run: `python3.11 -m pytest edge_adviser/tests/test_ledger_schema.py edge_adviser/tests/test_demo.py -q` → 全绿
Run: `python3.11 -m pytest edge_adviser/tests -q` → 全绿
Run: `cd edge_adviser/webui && npm run typecheck && npm test && npm run build` → 全部通过

- [ ] **Step 8: Commit**

```bash
git add edge_adviser/edge_adviser/core/ledger_migrate.py edge_adviser/edge_adviser/server/demo.py edge_adviser/edge_adviser/server/router_assets.py edge_adviser/tests/test_ledger_schema.py edge_adviser/webui/src/views/asset/TabKnowledge.vue
git commit -m "refactor(edge-adviser): unify knowledge ledger schema — demo seed writes v2, API normalizes v1

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: 前端 minors 批次（断点 token / marked 实例 / fmt 抽取）

**Files:**
- Modify: `edge_adviser/webui/src/theme/tokens.css`（加断点 CSS 自定义属性说明 + 复用现有变量约定）
- Modify: 8 个文件中的 `@media (max-width: 899.98px)` → 统一注释约定（见 Step 3 说明）
- Modify: `edge_adviser/webui/src/components/MarkdownView.vue:12-20`
- Create: `edge_adviser/webui/src/utils/fmt.ts`
- Modify: `Settings.vue` / `Audit.vue` / `Alerts.vue` / `Overview.vue` 的本地 fmt 函数改 import
- Test: `edge_adviser/webui/src/utils/fmt.test.ts`（新建）

**Interfaces:**
- Produces: `webui/src/utils/fmt.ts` 导出 `fmtBytes(b: number | null | undefined): string`、`fmtS(v: number | null | undefined): string`、`fmtPct(v: number | null | undefined): string`、`fmtTs(ts: number): string`（Alerts/AckDialog 的 epoch 格式化）、`fmtIso(iso: string): string`（Task 6 需要）。语义逐字符沿用 `Settings.vue:55-71` 与 `AckDialog.vue:73-78` 的现有实现。

- [ ] **Step 1: 写失败测试**

```ts
import { describe, expect, it } from 'vitest'
import { fmtBytes, fmtS, fmtPct, fmtIso } from './fmt'

describe('fmtBytes', () => {
  it('formats byte scales', () => {
    expect(fmtBytes(500)).toBe('500B')
    expect(fmtBytes(2048)).toBe('2.0KB')
    expect(fmtBytes(3 * 1024 * 1024)).toBe('3.0MB')
    expect(fmtBytes(1.5 * 1024 ** 3)).toBe('1.50GB')
  })
  it('dashes non-finite', () => {
    expect(fmtBytes(null)).toBe('—')
    expect(fmtBytes(NaN)).toBe('—')
  })
})

describe('fmtS', () => {
  it('picks a human unit', () => {
    expect(fmtS(45)).toBe('45s')
    expect(fmtS(120)).toBe('2m')
    expect(fmtS(7200)).toBe('2.0h')
    expect(fmtS(undefined)).toBe('—')
  })
})

describe('fmtPct', () => {
  it('rounds', () => {
    expect(fmtPct(0.916)).toBe('92%')
    expect(fmtPct(null)).toBe('—')
  })
})

describe('fmtIso', () => {
  it('formats an ISO string', () => {
    expect(fmtIso('2026-11-26T06:00:00+00:00')).not.toBe('—')
  })
  it('dashes invalid', () => {
    expect(fmtIso('not-a-date')).toBe('—')
    expect(fmtIso('')).toBe('—')
  })
})
```

- [ ] **Step 2: 运行验证失败**

Run: `cd edge_adviser/webui && npx vitest run src/utils/fmt.test.ts`
Expected: FAIL — cannot resolve './fmt'

- [ ] **Step 3: 实现 fmt.ts**

```ts
// Shared numeric/timestamp formatting — one source of truth for the mono
// tabular displays. Semantics carried verbatim from Settings.vue / Audit.vue;
// views import from here instead of re-declaring near-identical locals.

export function fmtBytes(b: number | null | undefined): string {
  if (typeof b !== 'number' || !Number.isFinite(b)) return '—'
  if (b < 1024) return `${b}B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)}KB`
  if (b < 1024 * 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)}MB`
  return `${(b / 1024 / 1024 / 1024).toFixed(2)}GB`
}

export function fmtS(v: number | null | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  if (v < 60) return `${Math.round(v)}s`
  if (v < 3600) return `${Math.round(v / 60)}m`
  return `${(v / 3600).toFixed(1)}h`
}

export function fmtPct(v: number | null | undefined): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—'
  return `${Math.round(v * 100)}%`
}

export function fmtTs(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** ISO-string timestamps (ledger created_at etc.); invalid → '—'. */
export function fmtIso(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
```

- [ ] **Step 4: 验证 fmt 测试通过**

Run: `npx vitest run src/utils/fmt.test.ts` → 全绿

- [ ] **Step 5: 视图改 import + 删本地副本**

- `Settings.vue`：删 55-71 行的三个函数，`import { fmtBytes, fmtS, fmtPct } from '../utils/fmt'`。
- `Audit.vue`：`fmtElapsed` 与 `fmtBytes` 若干——逐个对照：`fmtElapsed` 语义与 `fmtS` 一致则删除并改 import；`fmtTime`/`fmtWindow` 是行级格式化，保留本地。
- `Alerts.vue` / `AckDialog.vue`：`fmtTs` 改 import（签名一致）。
- `Overview.vue` / `FleetSpectrum.vue`：`fmtVal` 两处实现若逐字符一致则抽到 fmt.ts 导出 `fmtVal`，两处 import（先 diff 确认再动手，不一致就保留现状并注释差异原因——不强行统一语义不同的函数）。

- [ ] **Step 6: marked 实例化**

`MarkdownView.vue` 改为：

```ts
import { Marked } from 'marked'
const marked = new Marked({ gfm: true, breaks: true })
```

删除模块级 `marked.setOptions(...)` 行；`marked.parse(...)` 调用处不变。

- [ ] **Step 7: 断点约定注释**

现状实测：`899.98px`（6 处，主断点——平板横排收列）与 `699.98px`（2 处，手机网格单列）已经是**两个语义级**而非漂移；`ConfigurationManager.vue` 的 `620px` 是配置抽屉特例。处理方式不是强改成变量（CSS 自定义属性不能进 media query 条件），而是**在每个 `@media` 行加语义注释并以此约束未来**：

```css
/* breakpoint: tablet→stack (project-wide 899.98px; phone grid = 699.98px) */
@media (max-width: 899.98px) {
```

逐文件加注释（Audit/Settings/AppRail/Overview/Alerts/AssetDetail/FleetSpectrum/App.vue + ConfigurationManager 的 620px 标 `drawer-only special case`）。这是文档化收敛，不改任何数值。

- [ ] **Step 8: 全套验证 + Commit**

Run: `npm run typecheck && npm test && npm run build` → 全部通过

```bash
git add edge_adviser/webui/src/utils/fmt.ts edge_adviser/webui/src/utils/fmt.test.ts edge_adviser/webui/src/views edge_adviser/webui/src/components edge_adviser/webui/src/theme
git commit -m "refactor(webui): shared fmt helpers + Marked instance + breakpoint convention comments

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: 多架构镜像 + TLS/反代文档（P0.1 + P1.4）

**Files:**
- Create: `.github/workflows/edge-image.yml`（新建 workflow）
- Modify: `edge_adviser/Dockerfile`（ARM 兼容说明注释；如有 x86 特有步骤需修复——当前未发现，pip 全部有 aarch64 轮子）
- Modify: `edge_adviser/deploy/README.md`（TLS 反代示例）
- Modify: `edge_adviser/CHANGELOG.md`（0.6.0 条目，收集所有任务）

**Interfaces:**
- Consumes: 既有 `edge_adviser/Dockerfile`（两阶段：node webui-build → python runtime）。
- Produces: `edge-image.yml` 在 tag push（`v*`）或 workflow_dispatch 时构建 `linux/amd64,linux/arm64` 双平台镜像并 push 到 GHCR。

- [ ] **Step 1: 验证本地多架构构建可行（可选但推荐）**

Run: `docker buildx build --platform linux/arm64 -f edge_adviser/Dockerfile -t edge-adviser:arm-test --load . 2>&1 | tail -5`（如本机无 QEMU 则跳过此步，CI 是权威验证）
Expected: 构建成功或明确报告 QEMU 缺失；两个 python 依赖矩阵（numpy/pymodbus/asyncua/aiomqtt）确认 aarch64 轮子存在于 PyPI。

- [ ] **Step 2: 写 workflow**

```yaml
name: EdgeAdviser image

on:
  workflow_dispatch:
  push:
    tags: ["v*"]
    paths: ["edge_adviser/**"]

permissions:
  contents: read
  packages: write

jobs:
  multiarch:
    name: build amd64+arm64
    runs-on: ubuntu-latest
    timeout-minutes: 40
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-qemu-action@v3

      - uses: docker/setup-buildx-action@v3

      - uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - uses: docker/metadata-action@v5
        id: meta
        with:
          images: ghcr.io/${{ github.repository_owner }}/edge-adviser
          tags: |
            type=ref,event=tag
            type=raw,value=latest,enable={{is_default_branch}}

      - uses: docker/build-push-action@v6
        with:
          context: .
          file: edge_adviser/Dockerfile
          platforms: linux/amd64,linux/arm64
          push: ${{ github.event_name == 'push' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

（`push: ${{ github.event_name == 'push' }}` — workflow_dispatch 只构建不推送，供验证。）

- [ ] **Step 3: Dockerfile 注释修正**

Dockerfile 顶部注释块加一段（不动构建步骤——已验证无 x86-only 步骤）：

```dockerfile
# Multi-arch: this build is amd64+arm64 clean (all pip deps publish aarch64
# wheels; node/python base images are multi-arch). CI builds both platforms
# via buildx+QEMU — see .github/workflows/edge-image.yml. There is deliberately
# no apt package in the runtime stage, so no dpkg arch branching is needed.
```

- [ ] **Step 4: deploy/README.md TLS 段**

在 `edge_adviser/deploy/README.md` 末尾追加：

```markdown
## TLS 终止（生产必须）

edge-adviser 服务明文 HTTP，安全模型默认"局域网可信 + PIN bearer"。跨网段或
任何不受控链路访问时，必须在前面加 TLS 终止。最小 Caddy 示例（自动证书）：

```caddyfile
adviser.plant.local {
    reverse_proxy 127.0.0.1:8000
}
```

nginx 等价物：

```nginx
server {
    listen 443 ssl;
    server_name adviser.plant.local;
    ssl_certificate     /etc/nginx/certs/adviser.crt;
    ssl_certificate_key /etc/nginx/certs/adviser.key;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 3600s;   # SSE 长连接：/stream/* 不能被默认 60s 截断
        proxy_buffering off;        # SSE 逐事件下发，缓冲会攒批
    }
}
```

两个必须项：`proxy_read_timeout`（SSE 心跳 25s 一次，但 EventSource 重连前的
读超时要大于任务 600s 上限才有意义）与 `proxy_buffering off`（否则告警/任务
事件被攒成一大块下发，HMI 的"实时"变"延时回放"）。

## 访问令牌的日志取舍

PIN 模式下 EventSource 无法携带 Authorization 头，`/stream/*` 订阅走
`?token=<hmac>` 查询参数。查询参数会进入反代访问日志——这是已知的取舍：
令牌 12h 过期、HMAC 签名不可伪造、且只应出现在受控的运维日志里。若日志
外发第三方聚合，请在反代层剥离 query（nginx: `log_format` 里用
`$request_method $uri` 替代 `$request`）。
```

- [ ] **Step 5: CHANGELOG 0.6.0 条目**

在 `edge_adviser/CHANGELOG.md` 顶部（`## 0.5.2` 之前）插入完整 0.6.0 条目，覆盖本计划全部任务：P0 备份/类型保真、任务落盘、双架构镜像；P1 PIN 计数、TrustedHost、TLS 文档；P2 /metrics、HTML 导出、账本统一、fmt 抽取。格式沿用既有条目（`## 0.6.0 — 2026-11-26（…）` + 分节表格/要点）。

- [ ] **Step 6: 版本号 + 最终验证 + Commit**

`edge_adviser/edge_adviser/__init__.py` 与 `edge_adviser/pyproject.toml` 的版本改 `0.6.0`。

Run: `python3.11 -m pytest edge_adviser/tests -q` → 全绿
Run: `cd edge_adviser/webui && npm run typecheck && npm test && npm run build` → 全部通过

```bash
git add .github/workflows/edge-image.yml edge_adviser/Dockerfile edge_adviser/deploy/README.md edge_adviser/CHANGELOG.md edge_adviser/edge_adviser/__init__.py edge_adviser/pyproject.toml
git commit -m "chore(edge-adviser): 0.6.0 — multiarch image CI, TLS/proxy docs, release bookkeeping

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review 结果

1. **覆盖检查**：P0.1→Task 8，P0.2→Task 2，P0.3→Task 1；P1.4→Task 8，P1.5→Task 3，P1.6→Task 3；P2.7→Task 5，P2.8→Task 6，P2.9（文案抽取）→缩小为 Task 7 的 fmt 抽取（完整 i18n 抽取延后——YAGNI，`copy.ts` 已是雏形）；P2.10→Task 4。外加原 A 批次 minors 并入 Task 7。无缺口。
2. **占位符扫描**：Task 2 Step 7 的 `from .task_journal_placeholder import noop` 是显式标注的"错误示范"（← NOT THIS），紧随其后的正确写法才是交付内容；Task 4 的 `read_runlog` import 带"若不存在则删除"的显式处置指令。其余步骤均为完整代码。
3. **类型一致性**：`TaskJournal.append/open_tasks/mark_recovered` 在 Task 2 各步骤间一致；`render_report_html(report_md, meta, *, asset_id, date, provider, degraded)` 在 Task 5 测试与端点一致；`normalize_records(rows, *, cls)` 在 Task 6 测试与端点调用一致；`sse_active(bus)` 依赖 `bus.watcher_count()`，该方法在 Task 4 Step 3 定义；`fmtIso` 在 Task 7 产出、Task 6 Step 6 消费——**执行顺序上 Task 6 若先于 Task 7，实现者需在 TabKnowledge 内联本地 fmtIso，Task 7 合入后再替换 import**（在 Task 6 Step 6 已注明）。
