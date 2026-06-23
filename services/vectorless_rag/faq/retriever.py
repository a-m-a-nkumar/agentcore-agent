"""
The pluggable FAQ retriever interface (spec §4.1) — the heart of this feature.

`router.py` only ever talks to `FaqRetriever`; swapping backends (bm25 /
embeddings / hybrid) never touches routing or synthesis code.

Score normalization is MANDATORY (spec §4.1): every backend maps its raw score
into [0,1] so `FAQ_THRESHOLD` means the same thing across backends. Without it,
the threshold is meaningless the moment you flip `FAQ_BACKEND`.

IMPORTANT — why NOT min-max over the candidate set:
  min-max would peg the TOP hit to 1.0 for *every* query, including an
  out-of-scope one whose best-of-k candidate is garbage. The threshold gate
  (spec §6) needs the top hit's ABSOLUTE confidence so it can reject weak tops
  (false-injection control). So each backend uses an absolute, monotonic map:
    - embeddings -> raw cosine, clamped to [0,1] (already calibrated)
    - bm25       -> saturating squash s/(s+half) (0->0, half->0.5, ∞->1)
  `FAQ_THRESHOLD` is then calibrated PER BACKEND on the eval set (Phase 6).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .store import FaqEntry

# A search result: (entry, normalized_score in [0,1]).
Hit = tuple[FaqEntry, float]


class FaqRetriever(ABC):
    """One interface every backend implements."""

    name: str = "base"

    @abstractmethod
    def index(self, entries: list[FaqEntry]) -> None:
        """Build the backend's index over the FAQ entries. Called once at startup."""

    @abstractmethod
    def search(self, query: str, k: int) -> list[Hit]:
        """Return top-k (entry, normalized_score), score in [0,1], descending."""


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def saturating_squash(score: float, half: float) -> float:
    """Map an unbounded non-negative score into [0,1) preserving absolute magnitude.

      s = 0      -> 0.0
      s = half   -> 0.5
      s -> ∞     -> 1.0  (asymptote)

    Monotonic (so ranking is preserved) AND absolute (so the threshold gate can
    reject weak tops). `half` is the score that maps to 0.5 — a per-backend
    constant; the eval tunes `FAQ_THRESHOLD` against it, not the other way round.
    """
    if score <= 0.0 or half <= 0.0:
        return 0.0
    return score / (score + half)
