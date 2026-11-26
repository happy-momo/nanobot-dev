"""Report → standalone HTML (handoff/print use case).

Zero-dependency markdown subset renderer: the archived reports are model output
constrained to headings/lists/tables/bold/code, and anything outside that subset
is escaped verbatim rather than interpreted — an industrial report must never
gain an XSS vector by being re-rendered. The deterministic DATA-SCOPE badge is
lifted out of the markdown and re-rendered as a provenance table header, so the
printout leads with the facts the reader can trust.
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


def _flush_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    head, *body = rows
    th = "".join(f"<th>{_inline(html.escape(c))}</th>" for c in head)
    trs = "".join(
        "<tr>" + "".join(f"<td>{_inline(html.escape(c))}</td>" for c in r) + "</tr>"
        for r in body)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def _render_body(md: str) -> str:
    out: list[str] = []
    list_open = False
    table_rows: list[list[str]] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
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
