"""Memory knowledge as a first-class, versioned, retrievable knowledge asset (N6/N8).

Edges:
  * N6 — upgraded retrieval: a lightweight stdlib keyword (TF) index that ranks
    MEMORY.md sections by *query relevance* instead of the legacy "most recent
    few sections". Configurable (``memory_index``: off | keywords). ``off`` keeps
    byte-for-byte legacy behavior.
  * N8 — conflict & staleness handling: each learned MEMORY.md write is broken
    into claims; numerically contradictory claims mark the older one
    ``superseded`` (a conflict ledger records it), and claims older than
    ``memory_decay_s`` are treated as ``stale`` during retrieval. Configurable
    via ``memory_conflict_policy`` (off | flag).

These sit entirely in edge_adviser/ and wrap nanobot's MemoryStore (which owns
git-versioned persistence of memory/MEMORY.md) rather than modifying nanobot.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config.model import AssetSpec
from ..core.asset_store import create_asset_memory_store

# --------------------------------------------------------------------------- #
# N6: lightweight keyword (TF) index over MEMORY.md sections
# --------------------------------------------------------------------------- #
_TOKEN_EN = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> Counter:
    """English words + Chinese 2-grams, both lowercased (stdlib only)."""
    low = text.lower()
    toks = _TOKEN_EN.findall(low)
    for i in range(len(low) - 1):
        ch = low[i:i + 2]
        if "一" <= ch[0] <= "鿿" or "一" <= ch[1] <= "鿿":
            toks.append(ch)
    return Counter(t for t in toks if len(t) >= 1)


@dataclass
class _Section:
    title: str
    body: str


def split_sections(memory_md: str) -> list[_Section]:
    """Split MEMORY.md into (title, body) sections on ``## `` headings.

    Mirrors the legacy naive retrieval's boundary so indexing is a strict
    superset of the current behavior.
    """
    parts = [p for p in memory_md.split("\n## ") if p.strip()]
    out: list[_Section] = []
    for p in parts:
        lines = p.split("\n")
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        out.append(_Section(title=title, body=body))
    return out


class KeywordMemoryIndex:
    """In-memory TF index over a MEMORY.md, built lazily and cached by mtime."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict[int, Counter], list[_Section]]] = {}

    def _build(self, path: Path) -> tuple[dict[int, Counter], list[_Section]]:
        sections = split_sections(path.read_text(encoding="utf-8"))
        index: dict[int, Counter] = {}
        for i, s in enumerate(sections):
            index[i] = _tokens(f"{s.title} {s.body}")
        return index, sections

    def get(self, path: Path) -> tuple[dict[int, Counter], list[_Section]]:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}, []
        cached = self._cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
        index, sections = self._build(path)
        self._cache[str(path)] = (mtime, index, sections)
        return index, sections

    def query(self, path: Path, query: str, top_k: int, *, decay: bool = False) -> list[tuple[int, str, float]]:
        """Return [(section_idx, title, score)] ranked by TF relevance."""
        index, sections = self.get(path)
        if not sections:
            return []
        q = _tokens(query)
        if not q:
            return []
        # document-length normalised dot product of TF vectors
        scores: list[float] = []
        for i, sec_idx in index.items():
            if sec_idx:
                dot = sum(q[t] * sec_idx[t] for t in q)
                norm = math.sqrt(sum(v * v for v in sec_idx.values()))
                scores.append(dot / (norm + 1e-9) if norm > 0 else 0.0)
            else:
                scores.append(0.0)
        order = sorted(range(len(sections)),
                       key=lambda i: scores[i], reverse=True)
        out: list[tuple[int, str, float]] = []
        for i in order[:top_k]:
            if scores[i] > 0:
                out.append((i, sections[i].title, scores[i]))
        if not out:
            # fall back to legacy "most recent" so a cold/unmatched query still yields knowledge
            for i in range(max(0, len(sections) - top_k), len(sections)):
                out.append((i, sections[i].title, 0.0))
        return out


# --------------------------------------------------------------------------- #
# N8: claim ledger for conflict & staleness
# --------------------------------------------------------------------------- #
def _extract_numeric_claims(text: str) -> list[tuple[str, float]]:
    """Pull (unitless, value) pairs like ``70.0`` / ``[60, 80]`` from a claim body."""
    bounds = re.findall(r"\[(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\]", text)
    nums: list[tuple[str, float]] = [("lo", float(a)) for a, b in bounds]
    nums += [("hi", float(b)) for a, b in bounds]
    for v in re.findall(r"\d+(?:\.\d+)?", text):
        nums.append(("v", float(v)))
    return nums


def _numeric_conflict(old: list[tuple[str, float]], new: list[tuple[str, float]]) -> bool:
    """Two claims conflict when they both assert a value for the same axis and it
    moved by more than a deterministic tolerance."""
    for a, b in zip(old, new):
        if a[0] != b[0]:
            continue
        tol = max(1.0, 0.1 * abs(a[1]))
        if abs(b[1] - a[1]) > tol:
            return True
    return False


class MemoryConflictLedger:
    """Append-only sidecar tracking claims, conflicts, and staleness (N8)."""

    def __init__(self, asset_dir: Path):
        self.claims_path = asset_dir / "memory_claims.jsonl"
        self.conflicts_path = asset_dir / "memory_conflicts.jsonl"
        self.asset_dir = asset_dir

    def _read(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        out: list[dict] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def _append(self, path: Path, rec: dict) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def record_claims(self, memory_md: str, *, source_window: str = "") -> None:
        """Break a newly-learned MEMORY.md into claims and append them."""
        for sec in split_sections(memory_md):
            rec = {
                "id": _claim_id(sec.title),
                "heading": sec.title,
                "text": sec.title + " " + sec.body,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source_window": source_window,
                "status": "active",
            }
            self._append(self.claims_path, rec)
        self._check_conflicts()

    def _check_conflicts(self) -> None:
        claims = self._read(self.claims_path)
        active: dict[str, dict] = {}
        for claim in claims:
            if claim.get("status") != "active":
                continue
            key = _claim_key(claim.get("heading", ""))
            prev = active.get(key)
            if prev and _numeric_conflict(
                _extract_numeric_claims(prev.get("text", "")),
                _extract_numeric_claims(claim.get("text", "")),
            ):
                prev["status"] = "superseded"
                self._append(self.conflicts_path, {
                    "id": f"{prev['id']}->{claim['id']}",
                    "heading": claim.get("heading", ""),
                    "old": prev.get("text", ""),
                    "new": claim.get("text", ""),
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                })
            active[key] = claim
        # rewrite claims with superseded statuses so the file reflects reality
        with open(self.claims_path, "w", encoding="utf-8") as f:
            for claim in claims:
                f.write(json.dumps(claim, ensure_ascii=False) + "\n")

    def conflicts_for(self, heading: str) -> list[dict]:
        return [c for c in self._read(self.conflicts_path) if heading in c.get("heading", "")]


def _claim_id(heading: str) -> str:
    import hashlib
    return hashlib.sha1(heading.encode("utf-8")).hexdigest()[:12]


def _claim_key(heading: str) -> str:
    # topic key: normalized tokens of the heading, ignoring trailing digits
    toks = [t for t in _TOKEN_EN.findall(heading.lower()) if not t.isdigit()]
    return " ".join(toks) or heading.lower()


# --------------------------------------------------------------------------- #
# MemoryService — versioned read/write + retrieval for an asset
# --------------------------------------------------------------------------- #
class MemoryService:
    """Wrap nanobot's git-versioned MemoryStore so knowledge writes are versioned,
    and expose query-driven retrieval with optional conflict/staleness handling."""

    def __init__(self, asset: AssetSpec, workspace: Path,
                 *, index: str = "off", conflict_policy: str = "flag",
                 decay_s: int = 7776000):
        self.asset = asset
        self.asset_dir = Path(workspace) / "assets" / asset.asset_id
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self._nb = create_asset_memory_store(asset, Path(workspace))
        self._index_mode = index
        self._conflict_policy = conflict_policy
        self._decay_s = decay_s
        self._index = KeywordMemoryIndex()
        self._ledger = MemoryConflictLedger(self.asset_dir)
        self._migrate()

    def _migrate(self) -> None:
        """One-time copy of root MEMORY.md -> nanobot memory/MEMORY.md if any."""
        root = self.asset_dir / "MEMORY.md"
        nb_memory = self.asset_dir / "memory" / "MEMORY.md"
        if root.exists() and not nb_memory.exists():
            try:
                nb_memory.parent.mkdir(parents=True, exist_ok=True)
                nb_memory.write_text(root.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass

    def read(self) -> str:
        return self._nb.read_memory()

    def write(self, content: str) -> None:
        # route through nanobot so MEMORY.md is git-versioned
        self._nb.write_memory(content)
        # keep the legacy root file in sync for any old reader (non-authoritative)
        try:
            (self.asset_dir / "MEMORY.md").write_text(content, encoding="utf-8")
        except OSError:
            pass
        if self._conflict_policy != "off":
            self._ledger.record_claims(content)

    def _memory_path(self) -> Path:
        return self.asset_dir / "memory" / "MEMORY.md"

    def retrieve(self, query: str = "", top_k: int = 6, max_chars: int = 2400) -> list[str]:
        """Retrieve knowledge sections for a query.

        With memory_index=keywords: TF-relevance ranking; with off: legacy
        most-recent sections (byte-for-byte legacy behavior). Honors max_chars.
        """
        path = self._memory_path()
        if not path.exists():
            return []
        if self._index_mode == "keywords" and query:
            ranked = self._index.query(path, query, top_k)
        else:
            sections = split_sections(path.read_text(encoding="utf-8"))
            ranked = [(i, s.title, 0.0) for i, s in
                      enumerate(sections[-top_k:], start=max(0, len(sections) - top_k))]

        out: list[str] = []
        chars = 0
        staled = set()
        if self._conflict_policy != "off" and self._index_mode == "keywords":
            claims = {c.get("heading", ""): c for c in self._ledger._read(self._ledger.claims_path)}
            for heading, c in claims.items():
                age = 0.0
                try:
                    created = datetime.fromisoformat(c.get("created_at", ""))
                    # claims may carry naive (legacy) or aware (UTC) timestamps;
                    # normalize to aware UTC before subtracting or this raises
                    # TypeError and takes down retrieve() for every query.
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - created).total_seconds()
                except (ValueError, TypeError):
                    pass
                if c.get("status") == "stale" or (self._decay_s and age > self._decay_s):
                    staled.add(heading)
        for idx, title, _score in ranked:
            sections = self._index.get(path)[1] if self._index_mode == "keywords" \
                else split_sections(path.read_text(encoding="utf-8"))
            sec = sections[idx]
            label = f"## {sec.title}"
            if self._conflict_policy != "off" and title in staled:
                label += " [stale]"
            text = f"{label}\n{sec.body}".rstrip()
            if chars + len(text) > max_chars:
                break
            out.append(text)
            chars += len(text)
        return out


def _fmt_conflict(conflict: dict) -> str:
    return (f"[superseded] {conflict.get('heading')}: was "
            f"'{conflict.get('old','')[:120]}' now '{conflict.get('new','')[:120]}'")


def build_conflict_block(memory_service: MemoryService) -> str | None:
    """Render superseded-claim evolution hints for context injection (N8)."""
    conflicts = memory_service._ledger._read(memory_service._ledger.conflicts_path)
    if not conflicts:
        return None
    lines = [f"  - {_fmt_conflict(c)}" for c in conflicts[-3:]]
    return "Known superseded knowledge (historical only):\n" + "\n".join(lines)