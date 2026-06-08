"""
Per-session in-memory state for the Joseph consulting agent.

This is process-local state — it does NOT persist across server restarts.
That's intentional for the POC: chat memory lives in AgentCore Memory
(authoritative), and side-panel state (scores, coverage, citations, current
report, uploaded files) lives here only for the duration of a working
session.

Threading model
---------------
- One ConsultingState per session_id, held in a module-level dict.
- One asyncio.Queue per in-flight chat request, set on _current_queue
  module global by the router before invoking the Strands agent so the
  tool functions can push events without taking the queue as a parameter
  (Strands tool signatures are visible to the model).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


SUB_SCORE_KEYS = (
    "financial",
    "productivity",
    "intent",
    "complexity",
    "data_platform",
    "measurement",
)

COVERAGE_AREAS = (
    "qualification",
    "value",
    "viability",
    "drivers",
    "instinct",
)

# Sub-sections per discovery area, mirrored from the DISCOVERY INTELLIGENCE
# framework in the system prompt. Joseph emits a `findings` map keyed by these
# slugs so each area card can show what was gathered per sub-section. Keys here
# are authoritative — the parser drops any finding whose key isn't listed.
COVERAGE_SUBSECTIONS: dict[str, tuple[str, ...]] = {
    "qualification": ("solution_fit", "sponsor", "duplication", "scope"),
    "value": ("quantitative", "qualitative"),
    "viability": ("data", "platform", "resources", "money", "time"),
    "drivers": (
        "monetary",
        "regulatory",
        "strategic",
        "ease",
        "dependencies",
        "reversibility",
        "cost_of_delay",
    ),
    "instinct": (
        "politics",
        "track_record",
        "adoption",
        "failure_mode",
        "build_buy",
        "constraints",
    ),
}


@dataclass
class SubScore:
    value: float | None = None
    confidence: str | None = None
    # Two-part rationale shown as separate sections in the "Why this score"
    # panel. `consumed` = the facts/inputs/documents the score rests on;
    # `ranking` = why those facts map to this 1–5 band rather than the
    # adjacent ones.
    consumed: str | None = None
    ranking: str | None = None


@dataclass
class CoverageArea:
    touched: bool = False
    note: str | None = None
    # Per-sub-section gathered info, keyed by the slugs in COVERAGE_SUBSECTIONS.
    # Accumulates across turns — a sub-section stays filled once gathered.
    findings: dict[str, str] = field(default_factory=dict)


@dataclass
class Citation:
    url: str
    title: str | None = None
    publisher: str | None = None
    tier: str = "directional"
    valid: bool = True


@dataclass
class UploadedFile:
    file_id: str
    filename: str
    text: str
    chars: int
    consumed: bool = False  # True once inlined into a user message


@dataclass
class KBSnapshot:
    """A KB search result, copied off the search service so the state has
    everything the UI needs without re-querying."""
    id: str
    title: str
    url: str
    snippet: str
    type: str
    icon: str
    relevance: float


@dataclass
class ConsultingState:
    session_id: str
    scores: dict[str, SubScore] = field(
        default_factory=lambda: {k: SubScore() for k in SUB_SCORE_KEYS}
    )
    coverage: dict[str, CoverageArea] = field(
        default_factory=lambda: {k: CoverageArea() for k in COVERAGE_AREAS}
    )
    citations: list[Citation] = field(default_factory=list)
    current_report: str | None = None
    uploaded_files: dict[str, UploadedFile] = field(default_factory=dict)
    kb_results: list[KBSnapshot] = field(default_factory=list)
    consumed_kb_ids: set[str] = field(default_factory=set)
    kb_search_done: bool = False  # guard so we only auto-search on the first inquiry

    @property
    def axes(self) -> dict[str, float | None]:
        impact_scores = [
            self.scores[k].value
            for k in ("financial", "productivity", "intent")
            if self.scores[k].value is not None
        ]
        speed_scores = [
            self.scores[k].value
            for k in ("complexity", "data_platform", "measurement")
            if self.scores[k].value is not None
        ]
        return {
            "impact": (sum(impact_scores) / len(impact_scores)) if impact_scores else None,
            "speed": (sum(speed_scores) / len(speed_scores)) if speed_scores else None,
        }

    @property
    def quadrant(self) -> str | None:
        axes = self.axes
        impact, speed = axes["impact"], axes["speed"]
        if impact is None or speed is None:
            return None
        impact_band = "high" if impact >= 3.67 else ("medium" if impact >= 2.34 else "low")
        speed_band = "high" if speed >= 3.67 else ("medium" if speed >= 2.34 else "low")
        if impact >= 4.5 and speed_band == "low":
            return "Transformational Value"
        if impact_band == "high" and speed_band == "high":
            return "Quick Win"
        if impact_band == "high" and speed_band == "medium":
            return "Accelerator"
        if impact_band in ("medium", "low") and speed_band == "high":
            return "Incremental Growth"
        return "Defer"

    def to_scores_payload(self) -> dict[str, Any]:
        return {
            "sub_scores": {
                k: {
                    "value": v.value,
                    "confidence": v.confidence,
                    "consumed": v.consumed,
                    "ranking": v.ranking,
                }
                for k, v in self.scores.items()
            },
            "axes": self.axes,
            "quadrant": self.quadrant,
        }

    def to_coverage_payload(self) -> dict[str, Any]:
        return {
            k: {
                "touched": v.touched,
                "note": v.note,
                "findings": dict(v.findings),
            }
            for k, v in self.coverage.items()
        }

    def to_citations_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "url": c.url,
                "title": c.title,
                "publisher": c.publisher,
                "tier": c.tier,
                "valid": c.valid,
            }
            for c in self.citations
        ]

    def to_kb_payload(self) -> dict[str, Any]:
        return {
            "results": [
                {
                    "id": r.id,
                    "title": r.title,
                    "url": r.url,
                    "snippet": r.snippet,
                    "type": r.type,
                    "icon": r.icon,
                    "relevance": r.relevance,
                    "consumed": r.id in self.consumed_kb_ids,
                }
                for r in self.kb_results
            ],
            "consumed_ids": sorted(self.consumed_kb_ids),
        }


_STATE_BY_SESSION: dict[str, ConsultingState] = {}

_current_queue: asyncio.Queue | None = None
_current_session_id: str | None = None


def get_or_create_state(session_id: str) -> ConsultingState:
    if session_id not in _STATE_BY_SESSION:
        _STATE_BY_SESSION[session_id] = ConsultingState(session_id=session_id)
    return _STATE_BY_SESSION[session_id]


def reset_state(session_id: str) -> ConsultingState:
    state = ConsultingState(session_id=session_id)
    _STATE_BY_SESSION[session_id] = state
    return state


def get_state(session_id: str) -> ConsultingState | None:
    return _STATE_BY_SESSION.get(session_id)


def set_request_context(session_id: str, queue: asyncio.Queue) -> None:
    """Called by the router at the start of each chat request so tool
    functions can push SSE events without taking the queue as a parameter."""
    global _current_queue, _current_session_id
    _current_queue = queue
    _current_session_id = session_id


def clear_request_context() -> None:
    global _current_queue, _current_session_id
    _current_queue = None
    _current_session_id = None


def current_queue() -> asyncio.Queue | None:
    return _current_queue


def current_session_id() -> str | None:
    return _current_session_id


def push_state_event(kind: str, payload: Any) -> None:
    """Synchronous helper: enqueue a state event for the active request.

    Strands tools run synchronously, so we use put_nowait. The SSE
    generator on the other end is async.
    """
    if _current_queue is None:
        return
    try:
        _current_queue.put_nowait({"type": "state", "kind": kind, "payload": payload})
    except asyncio.QueueFull:
        pass
