"""Historical learning loop — consolidates accumulated data into durable domain
knowledge (`MEMORY.md`) per asset using nanobot's Dream-style memory machinery.

The learner consumes:
  * the asset's MEMORY.md (existing knowledge),
  * operator feedback (feedback.jsonl),
  * a set of recent snapshots,
  * the latest report conclusion,
and asks the LLM to refine MEMORY.md into a living knowledge base for that asset.
New knowledge is appended (or existing sections amended); old or contradicted
facts are flagged. Everything is written back versioned so it is an auditable,
revisable knowledge asset.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger

from ..config.model import AdviserConfig, AssetSpec, ModelRole
from ..core.asset_store import AssetStore
from ..llm.gateway import LLMGateway, LLMResult

_LEARN_PROMPT = """You maintain the long-term domain knowledge base for an industrial
asset. Below is the current knowledge (MEMORY.md), recent operator feedback, and
a snapshot of recent data readings. Update the knowledge base:

- Add new operational insights (observed limits, failure precursor signals,
  effective maintenance actions, correlations between metrics).
- Correct anything contradicted by the feedback/data.
- Keep entries concise, evidence-grounded, and prioritized by importance.
- Do NOT invent measurement values that are not in the data.

Under NO circumstances return commentary. Return the ENTIRE new MEMORY.md
content (markdown) as your final message.

---
## Existing MEMORY.md
{memory}

## Recent operator feedback
{feedback}

## Recent analytics snapshot (summary)
{snapshot_summary}

## Latest report conclusion
{prev_conclusion}
"""


class Learner:
    def __init__(self, config: AdviserConfig, store: AssetStore, gateway: LLMGateway):
        self.config = config
        self.store = store
        self.gateway = gateway
        self._buffer: dict[str, list[dict[str, Any]]] = {}

    def buffer_snapshot(self, asset_id: str, snapshot: dict[str, Any], max_buf: int = 20) -> None:
        """Accumulate recent snapshots; keeps last `max_buf` per asset."""
        buf = self._buffer.setdefault(asset_id, [])
        buf.append(snapshot)
        if len(buf) > max_buf:
            del buf[: len(buf) - max_buf]

    async def learn_asset(self, asset: AssetSpec, *, role: ModelRole = ModelRole.ONLINE) -> LLMResult:
        """Run one learning pass over an asset."""
        asset_id = asset.asset_id
        memory = self.store.read_memory_md(asset_id)
        feedback = self.store.read_feedback(asset_id)
        snapshots = self._buffer.get(asset_id, []) or self._read_recent_snapshots(asset_id)
        prev_conclusion = self._prev_conclusion(asset_id)
        if not snapshots:
            logger.info("learn: no snapshots for {asset}; skipping", asset=asset_id)
            return LLMResult(content="", error="no snapshots to learn from")

        feedback_text = _fmt_feedback(feedback) or "(none)"
        snap_text = _fmt_snapshot_summary(snapshots)
        prompt = _LEARN_PROMPT.format(
            memory=memory or "(empty knowledge base)",
            feedback=feedback_text,
            snapshot_summary=snap_text,
            prev_conclusion=prev_conclusion or "(no previous report)",
        )
        res = await self.gateway.run(
            prompt,
            role=role,
            session_key=f"asset:{asset_id}:learn",
        )
        if res.error or not res.content.strip():
            logger.warning("learn: provider failed for {asset}: {err}", asset=asset_id, err=res.error)
            return res
        # persist new knowledge, versioned
        self.store.write_memory_md(asset_id, res.content.strip() + "\n")
        logger.info("learn: updated MEMORY.md for {asset}", asset=asset_id)
        return res

    def _read_recent_snapshots(self, asset_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for p in self.store.list_snapshots(asset_id, limit=12):
            try:
                import json
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    def _prev_conclusion(self, asset_id: str) -> str:
        # iterate reports dir newest
        files = sorted(self.store.reports_dir(asset_id).glob("*/snapshot.json"))
        if not files:
            return ""
        try:
            import json
            doc = json.loads(files[-1].read_text(encoding="utf-8"))
            return str(doc.get("conclusion", ""))[:500]
        except Exception:
            return ""


def _fmt_feedback(feedback: list[dict[str, Any]], limit: int = 10) -> str:
    if not feedback:
        return ""
    lines = []
    for f in feedback[-limit:]:
        lines.append(f"- [{f.get('timestamp','?')}] {f.get('note','')}")
    return "\n".join(lines)


def _fmt_snapshot_summary(snapshots: list[dict[str, Any]], limit: int = 6) -> str:
    lines = []
    for s in snapshots[-limit:]:
        lines.append(f"- {s.get('window_start','?')}~{s.get('window_end','?')} "
                     f"level={s.get('overall_level')}: {s.get('headline','')}")
    return "\n".join(lines)