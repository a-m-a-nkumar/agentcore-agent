"""
Recency scoring for RAG retrieval — time-aware re-ranking.

WHAT THIS DOES
--------------
Computes a multiplicative factor applied to a chunk's RRF score in the merge
step of the multi-query hybrid search. The factor is in [floor_multiplier, 1.0],
so it can only DEMOTE old content, never rescue a low-relevance chunk.

WHEN IT KICKS IN
----------------
Used by services.rag_service._multi_query_search after the existing
appearance + title bonuses. Applied once per unique chunk before the
top-K sort. Two different W_TEMPORAL values:

  - W_TEMPORAL_QA              = 0.35  — for query_with_rag / get_rag_context
  - W_TEMPORAL_PROMPT_ENHANCE  = 0.50  — for get_enhanced_prompt
    (prompt enhancement runs into IDE code generators where wrong context
    means wrong code — stronger recency tilt is justified)

RESEARCH BASIS
--------------
- Li & Croft, "Time-Based Language Models" (CIKM 2003): exponential decay
  document prior; multiplicative combination with relevance.
- CEUR Vol-2038 (2017): half-life parameterisation (h = ln 2 / lambda).
- TDS "RAG Is Blind to Time" (2024): decay floors to protect canonical
  content; the linear-interpolation blend coefficient.
- Milvus exponential decay docs: grace-period (offset) before decay starts.

PARAMETER SOURCES (from user 2026-05-20)
----------------------------------------
- Content lifecycle: medium (BRDs stable 1-3 months) -> HALF_LIFE_DAYS = 90.
- Prompt enhancer should push harder on recency than Q&A -> two W values.
- Evergreen reference content needs strong protection  -> DECAY_FLOOR = 0.5
  (raised from spec default of 0.35).
- QDF intent gate (boost when query says "latest"/"current"): deferred.
  has_recency_intent() exists below but is NOT wired into the merge step;
  enable later if eval shows specific intent-classed queries failing.
"""

import math
import re
from datetime import datetime, timezone
from typing import Optional

# -----------------------------------------------------------------------------
# Tunable constants
# -----------------------------------------------------------------------------

HALF_LIFE_DAYS: float = 90.0
"""
Days for the recency score to halve past the grace period.
  - 90d matches "policy/spec" content profile and Li & Croft TREC tuning (~69d).
  - Increase to 180 if eval shows old-but-valid docs being demoted.
  - Decrease to 45 if stale content still surfaces in user complaints.
"""

DECAY_FLOOR: float = 0.5
"""
Minimum recency score regardless of age. Protects canonical/evergreen content
(architecture docs, glossary, coding standards, security policies).
  - 0.5 means a 2-year-old doc retains 50% of its recency weight.
  - Raised from the spec's 0.35 default per user requirement: SDLC content
    includes long-lived reference material that must not vanish from top-K.
  - Lower to 0.35 only if eval shows old drafts still surface too much.
"""

W_TEMPORAL_QA: float = 0.35
"""
Recency vs relevance weight for the Q&A / context paths
(query_with_rag, get_rag_context).
  - final_multiplier = (1 - W) + W * recency_factor
  - Worst-case multiplier with floor=0.5, W=0.35: 0.65 + 0.35*0.5 = 0.825
  - Best-case multiplier (fresh doc): 1.0
"""

W_TEMPORAL_PROMPT_ENHANCE: float = 0.5
"""
Recency vs relevance weight for the prompt-enhancement path
(get_enhanced_prompt). Stronger than Q&A because the downstream consumer is
an AI coding assistant — wrong context yields wrong code.
  - Worst-case multiplier with floor=0.5, W=0.5: 0.5 + 0.5*0.5 = 0.75
  - Fresh docs still get 1.0; old canonical docs still keep 0.75 of their score.
"""

GRACE_DAYS: float = 7.0
"""
Within the grace period, decay = 1.0 (no penalty). Prevents meaningless
ranking differences between "updated yesterday" and "updated 3 days ago".
Inspired by the Milvus exponential-decay `offset` parameter.
"""


# -----------------------------------------------------------------------------
# Core functions
# -----------------------------------------------------------------------------

def recency_factor(source_updated_at: Optional[datetime]) -> float:
    """
    Compute the raw recency score for a document in [DECAY_FLOOR, 1.0].

    Returns:
        - 1.0 if the document is within the grace period (fresh)
        - 0.5 if exactly one half-life past the grace boundary
        - DECAY_FLOOR if old enough that the floor has clamped
        - DECAY_FLOOR for NULL/unknown timestamps (conservative — never boost)

    Formula:
        effective_age = max(0, age_days - GRACE_DAYS)
        decay         = 0.5 ** (effective_age / HALF_LIFE_DAYS)
        return        = max(DECAY_FLOOR, decay)
    """
    if source_updated_at is None:
        return DECAY_FLOOR

    now = datetime.now(timezone.utc)

    # Tolerate timezone-naive inputs (some callers pass datetimes built from
    # naive strings); assume UTC. This is safe because every Atlassian timestamp
    # we ingest is UTC.
    if source_updated_at.tzinfo is None:
        source_updated_at = source_updated_at.replace(tzinfo=timezone.utc)

    age_days = (now - source_updated_at).total_seconds() / 86400.0

    # Future timestamps (clock skew / bad data) should not penalise.
    if age_days < 0:
        return 1.0

    effective_age = max(0.0, age_days - GRACE_DAYS)
    decay = 0.5 ** (effective_age / HALF_LIFE_DAYS)
    return max(DECAY_FLOOR, decay)


def recency_multiplier(
    source_updated_at: Optional[datetime],
    w_temporal: Optional[float] = None,
) -> float:
    """
    Compute the multiplier to apply to a chunk's RRF score in the merge step.

    Linear interpolation between 1.0 (full score, recency disabled) and
    recency_factor (pure recency). Multiplicative blend means a low-relevance
    chunk cannot be rescued by being fresh.

    Args:
        source_updated_at: Document's last-modified timestamp (TZ-aware preferred).
        w_temporal: Override the recency weight. None -> uses W_TEMPORAL_QA.
                    Pass W_TEMPORAL_PROMPT_ENHANCE for the prompt-enhancer path.

    Returns:
        Float in [(1 - w_temporal) + w_temporal * DECAY_FLOOR, 1.0].
        With QA defaults (W=0.35, floor=0.5): [0.825, 1.0].
        With prompt-enhance defaults (W=0.5, floor=0.5): [0.75, 1.0].
    """
    if w_temporal is None:
        w_temporal = W_TEMPORAL_QA

    r = recency_factor(source_updated_at)
    return (1.0 - w_temporal) + w_temporal * r


# -----------------------------------------------------------------------------
# QDF intent gate (designed but NOT enabled — deferred for v1)
# -----------------------------------------------------------------------------

_RECENCY_INTENT_PATTERN = re.compile(
    r'\b(latest|current|recent|newest|updated|today|now|'
    r'this\s+(?:week|month|quarter|sprint))\b',
    re.IGNORECASE,
)


def has_recency_intent(query: str) -> bool:
    """
    Detect whether a user query explicitly asks for fresh content.

    NOT wired into the merge step yet. To enable: in _multi_query_search, swap
    `w_temporal = recency.W_TEMPORAL_QA` for
    `w_temporal = 0.6 if recency.has_recency_intent(user_query) else recency.W_TEMPORAL_QA`.

    Caveat: known false-positive rate. "The latest decision" can mean either
    "the most recent decision" (true positive) or "the final/only decision
    I care about" (false positive). Measure before enabling.
    """
    return bool(_RECENCY_INTENT_PATTERN.search(query or ""))
