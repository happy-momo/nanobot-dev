"""ReportGenerator — produce and archive professional analysis reports.

A deep report combines:
  * the deterministic AnalyticsSnapshot (facts)
  * a markdown narrative produced by the LLM (interpretation)

It archives `report.md` + a machine-readable `snapshot.json` under
`<assets>/<asset_id>/reports/<YYYY-MM-DD>/`, and appends a one-line conclusion to
the asset's historical knowledge so the learn loop can reference it.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config.model import AdviserConfig, AssetSpec, ModelRole
from ..core.asset_store import AssetStore
from ..llm.gateway import LLMGateway, LLMResult
from ..llm.providers import asset_session

_REPORT_PROMPT = """You are a senior industrial reliability engineer.
A deterministic analytics engine produced the following FACTUAL snapshot about
the asset named below. Interpret the numbers and write a professional,
concise maintenance report in Markdown.

The snapshot is ground truth: do not invent numbers. Use the asset profile for
units and normal ranges, and the provided historical knowledge to explain
trends or recommend actions.

Produce this section structure:
# <Asset Name> — Operational Report
## Executive Summary       (2-3 sentences: overall state, top risk)
## Key Metrics             (table: metric | unit | mean | min | max | 95th | trend | status)
## Anomalies & Detections  (list any warn/critical band events, change-points, trends)
## Assessment              (what the numbers mean for the asset)
## Recommendations         (prioritized list of actions)
## Confidence & Data Scope (sampling window, source, any limitations)

Current date: {date_text}
"""


class ReportGenerator:
    def __init__(self, config: AdviserConfig, store: AssetStore, gateway: LLMGateway):
        self.config = config
        self.store = store
        self.gateway = gateway

    async def generate(
        self,
        asset: AssetSpec,
        snapshot: dict[str, Any],
        *,
        role: ModelRole = ModelRole.ONLINE,
        source: str = "",
        regime: str = "",
    ) -> LLMResult:
        """Run the LLM to interpret a snapshot and return the report markdown."""
        from ..llm.context import ContextBuilder

        builder = ContextBuilder(self.config, self.store)
        blocks: list[Any] = []
        ab = builder.build_asset_block(asset)
        if ab:
            blocks.append(ab)
        blocks.append(builder.build_snapshot_block(snapshot))
        kb = builder.build_knowledge_block(asset.asset_id)
        if kb:
            blocks.append(kb)

        prompt = _REPORT_PROMPT.format(
            date_text=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )
        session_key = asset_session(asset.asset_id, "report")
        return await self.gateway.run(
            prompt,
            role=role,
            session_key=session_key,
            blocks=blocks,
        )

    def archive(self, asset: AssetSpec, snapshot: dict[str, Any],
                report_md: str, *, source: str = "", degraded: bool = False,
                provider: str = "") -> Path:
        """Write report.md + snapshot.json into the dated report folder.
        Returns the report directory."""
        day = datetime.now().strftime("%Y-%m-%d")
        out = self.store.reports_dir(asset.asset_id) / day
        out.mkdir(parents=True, exist_ok=True)

        md = (
            f"<!-- asset: {asset.asset_id} | generated: {datetime.now().isoformat(timespec='seconds')} | provider: {provider} | degraded: {degraded} -->\n"
            f"{report_md}\n"
        )
        (out / "report.md").write_text(md, encoding="utf-8")

        snapshot_doc = {
            "asset_id": asset.asset_id,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "provider": provider,
            "degraded": degraded,
            "source": source,
            "snapshot": snapshot,
        }
        (out / "snapshot.json").write_text(
            json.dumps(snapshot_doc, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return out

    def latest_conclusion(self, asset_id: str) -> str:
        """One-line conclusion of the most recent report for the learn loop."""
        files = sorted(self.store.reports_dir(asset_id).glob("*/report.md"))
        if not files:
            return ""
        return _extract_conclusion(files[-1].read_text(encoding="utf-8"))


def _extract_conclusion(md: str) -> str:
    if "## Executive Summary" in md:
        seg = md.split("## Executive Summary", 1)[1]
        first_par = next((p.strip() for p in seg.split("\n\n") if p.strip()), "")
        return first_par.strip().replace("\n", " ")[:400]
    return md.strip()[-400:]