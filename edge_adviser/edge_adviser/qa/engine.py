"""QAEngine — answer "what is the status of my asset right now?" as a chat.

Retrieves the asset snapshot (most recent), asset profile, and learned knowledge,
then asks the model to answer from those facts. Runs with the configured role
(default routine → cheap/local; falls back to online automatically).
"""

from __future__ import annotations

from typing import Any

from ..config.model import AdviserConfig, AssetSpec, ModelRole
from ..core.asset_store import AssetStore
from ..llm.gateway import LLMGateway, LLMResult
from ..llm.providers import asset_session

_QA_SYSTEM = """You are an industrial equipment advisor. A user asks about the status
of a piece of equipment. Use ONLY the provided snapshot and knowledge; do not
invent numbers. Answer in the user's language, be concise but technical, and if
the user asks for a recommendation, give a prioritized action list grounded in
the data.
"""

_QA_PROMPT = """Question: {question}

Use the asset profile, current analytics snapshot, and historical knowledge above
to answer.
"""


class QAEngine:
    def __init__(self, config: AdviserConfig, store: AssetStore, gateway: LLMGateway):
        self.config = config
        self.store = store
        self.gateway = gateway

    def _latest_snapshot(self, asset_id: str) -> dict[str, Any] | None:
        files = self.store.list_snapshots(asset_id, limit=1)
        if not files:
            return None
        try:
            import json
            return json.loads(files[0].read_text(encoding="utf-8"))
        except Exception:
            return None

    async def ask(self, asset: AssetSpec, question: str,
                  *, role: ModelRole = ModelRole.ROUTINE) -> LLMResult:
        """Answer a free-form question about an asset."""
        from ..llm.context import ContextBuilder

        builder = ContextBuilder(self.config, self.store)
        snapshot = self._latest_snapshot(asset.asset_id)
        blocks: list[Any] = []
        ab = builder.build_asset_block(asset)
        if ab:
            blocks.append(ab)
        if snapshot:
            blocks.append(builder.build_snapshot_block(snapshot))
        kb = builder.build_knowledge_block(asset.asset_id)
        if kb:
            blocks.append(kb)
        pc = builder.build_prev_conclusion_block(asset.asset_id)
        if pc:
            blocks.append(pc)

        prompt = _QA_SYSTEM + "\n\n" + _QA_PROMPT.format(question=question)
        return await self.gateway.run(
            prompt,
            role=role,
            session_key=asset_session(asset.asset_id, "qa"),
            blocks=blocks,
        )