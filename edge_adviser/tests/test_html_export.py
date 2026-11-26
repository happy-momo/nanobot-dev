"""HTML report export — stdlib-only rendering of the archived markdown.

Offline handoff / print use case. The renderer covers the controlled markdown
subset the report generator produces (headings/lists/tables/bold/code) and
escapes everything else — an industrial report must never gain an XSS vector
by being re-rendered.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent / "edge_adviser"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from edge_adviser.report.html_export import render_report_html  # noqa: E402


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


def test_tables_render() -> None:
    md = "| 指标 | 均值 |\n|---|---|\n| temp | 62.0 |"
    out = render_report_html(md, None, asset_id="a", date="d")
    assert "<table>" in out and "<th>指标</th>" in out and "<td>62.0</td>" in out


def test_frontmatter_comment_is_stripped() -> None:
    md = "<!-- DATA-SCOPE (machine generated; do not edit) -->\nstatus: healthy\n<!-- end -->\n\n# 正文"
    out = render_report_html(md, None, asset_id="a", date="d")
    assert "DATA-SCOPE" not in out
    assert "正文" in out


class _Stub:
    async def astart(self): pass
    async def aclose(self): pass
    async def run(self, prompt, **kw): return {}


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
