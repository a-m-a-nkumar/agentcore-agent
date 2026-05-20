"""Tests for utils.recency — time-aware re-ranking scoring."""

import pytest
from datetime import datetime, timezone, timedelta

from utils.recency import (
    recency_factor,
    recency_multiplier,
    has_recency_intent,
    DECAY_FLOOR,
    W_TEMPORAL_QA,
    W_TEMPORAL_PROMPT_ENHANCE,
    GRACE_DAYS,
    HALF_LIFE_DAYS,
)


# -----------------------------------------------------------------------------
# recency_factor
# -----------------------------------------------------------------------------

class TestRecencyFactor:
    """Test recency_factor() returns correct decay values."""

    def test_none_timestamp_returns_floor(self):
        """Documents with unknown timestamps get the conservative floor."""
        assert recency_factor(None) == DECAY_FLOOR

    def test_fresh_document_within_grace_period(self):
        """A document updated 3 days ago should score 1.0 (inside grace period)."""
        ts = datetime.now(timezone.utc) - timedelta(days=3)
        assert recency_factor(ts) == 1.0

    def test_document_at_exactly_grace_boundary(self):
        """A document exactly at the grace boundary should score ~1.0."""
        ts = datetime.now(timezone.utc) - timedelta(days=GRACE_DAYS)
        result = recency_factor(ts)
        assert result >= 0.99  # effective_age = 0, so decay = 1.0

    def test_document_at_one_half_life(self):
        """A document one half-life past the grace period should score ~0.5."""
        age = GRACE_DAYS + HALF_LIFE_DAYS  # 7 + 90 = 97 days
        ts = datetime.now(timezone.utc) - timedelta(days=age)
        result = recency_factor(ts)
        # With DECAY_FLOOR=0.5, decay clamps exactly at the floor here. So we
        # accept anywhere in [0.49, 0.51] — depending on whether floor took over.
        assert 0.49 <= result <= 0.51

    def test_very_old_document_hits_floor(self):
        """A 2-year-old document should be clamped to the floor."""
        ts = datetime.now(timezone.utc) - timedelta(days=730)
        result = recency_factor(ts)
        assert result == DECAY_FLOOR

    def test_future_timestamp_returns_max(self):
        """Clock skew: a future timestamp should return 1.0, not crash."""
        ts = datetime.now(timezone.utc) + timedelta(hours=2)
        assert recency_factor(ts) == 1.0

    def test_naive_timestamp_treated_as_utc(self):
        """Timezone-naive timestamps should be handled without crashing."""
        ts = datetime.utcnow() - timedelta(days=3)  # naive
        result = recency_factor(ts)
        assert result == 1.0  # within grace period

    def test_monotonic_decay(self):
        """Older documents should always score <= younger documents."""
        scores = []
        for age in [0, 7, 30, 60, 90, 180, 365]:
            ts = datetime.now(timezone.utc) - timedelta(days=age)
            scores.append(recency_factor(ts))
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Monotonicity violated at index {i}: {scores}"
            )


# -----------------------------------------------------------------------------
# recency_multiplier — Q&A weighting
# -----------------------------------------------------------------------------

class TestRecencyMultiplierQA:
    """Q&A path uses W_TEMPORAL_QA by default."""

    def test_fresh_doc_multiplier_is_one(self):
        """A fresh document gets multiplier = 1.0 (no penalty)."""
        ts = datetime.now(timezone.utc) - timedelta(days=1)
        assert recency_multiplier(ts) == pytest.approx(1.0, abs=0.001)

    def test_old_doc_multiplier_is_bounded(self):
        """Worst-case multiplier = (1 - W) + W * DECAY_FLOOR."""
        ts = datetime.now(timezone.utc) - timedelta(days=1000)
        expected_min = (1 - W_TEMPORAL_QA) + W_TEMPORAL_QA * DECAY_FLOOR
        assert recency_multiplier(ts) == pytest.approx(expected_min, abs=0.001)

    def test_none_timestamp_multiplier(self):
        """NULL timestamp gets the floor-based multiplier."""
        expected = (1 - W_TEMPORAL_QA) + W_TEMPORAL_QA * DECAY_FLOOR
        assert recency_multiplier(None) == pytest.approx(expected, abs=0.001)

    def test_multiplier_range_qa(self):
        """All Q&A multipliers should be in [(1-W)+(W*floor), 1.0]."""
        low = (1 - W_TEMPORAL_QA) + W_TEMPORAL_QA * DECAY_FLOOR
        for age in [0, 1, 7, 30, 90, 180, 365, 730]:
            ts = datetime.now(timezone.utc) - timedelta(days=age)
            m = recency_multiplier(ts)
            assert low <= m <= 1.0


# -----------------------------------------------------------------------------
# recency_multiplier — prompt-enhance weighting
# -----------------------------------------------------------------------------

class TestRecencyMultiplierPromptEnhance:
    """Prompt-enhancer path uses the stronger W_TEMPORAL_PROMPT_ENHANCE override."""

    def test_prompt_enhance_pushes_harder_on_old_content(self):
        """Same old doc should get a LOWER multiplier under prompt-enhance weighting."""
        ts = datetime.now(timezone.utc) - timedelta(days=500)
        qa = recency_multiplier(ts, w_temporal=W_TEMPORAL_QA)
        enhance = recency_multiplier(ts, w_temporal=W_TEMPORAL_PROMPT_ENHANCE)
        assert enhance < qa, (
            f"Prompt-enhance multiplier ({enhance:.3f}) should be lower than "
            f"Q&A multiplier ({qa:.3f}) for the same old doc."
        )

    def test_prompt_enhance_does_not_penalize_fresh(self):
        """A fresh doc should still get multiplier = 1.0 under prompt-enhance weighting."""
        ts = datetime.now(timezone.utc) - timedelta(days=2)
        assert recency_multiplier(ts, w_temporal=W_TEMPORAL_PROMPT_ENHANCE) == pytest.approx(1.0, abs=0.001)

    def test_prompt_enhance_floor(self):
        """Worst-case multiplier under prompt-enhance = (1 - W) + W * DECAY_FLOOR."""
        ts = datetime.now(timezone.utc) - timedelta(days=1000)
        expected = (1 - W_TEMPORAL_PROMPT_ENHANCE) + W_TEMPORAL_PROMPT_ENHANCE * DECAY_FLOOR
        assert recency_multiplier(ts, w_temporal=W_TEMPORAL_PROMPT_ENHANCE) == pytest.approx(expected, abs=0.001)


# -----------------------------------------------------------------------------
# Ranking behaviour — the user-visible win
# -----------------------------------------------------------------------------

class TestRankingBehavior:
    """Integration-ish tests verifying recency changes ranking the way we want."""

    def test_newer_version_beats_older_when_relevance_close(self):
        """
        BRD v2 (10 days old, base 0.85) should outscore
        BRD v1 (200 days old, base 0.90) after recency under prompt-enhance weighting.
        This is THE core use case — context-bleed prevention in pair programming.
        """
        v1_base = 0.90
        v2_base = 0.85

        v1_ts = datetime.now(timezone.utc) - timedelta(days=200)
        v2_ts = datetime.now(timezone.utc) - timedelta(days=10)

        v1_final = v1_base * recency_multiplier(v1_ts, w_temporal=W_TEMPORAL_PROMPT_ENHANCE)
        v2_final = v2_base * recency_multiplier(v2_ts, w_temporal=W_TEMPORAL_PROMPT_ENHANCE)

        assert v2_final > v1_final, (
            f"BRD v2 (final={v2_final:.3f}) should rank above "
            f"BRD v1 (final={v1_final:.3f})"
        )

    def test_highly_relevant_old_doc_still_survives(self):
        """
        Architecture spec (400 days old, base 0.95) should still outscore
        a mediocre fresh chunk (5 days old, base 0.50).
        Recency must not override strong relevance — DECAY_FLOOR=0.5 protects this.
        """
        old_base = 0.95
        fresh_base = 0.50

        old_ts = datetime.now(timezone.utc) - timedelta(days=400)
        fresh_ts = datetime.now(timezone.utc) - timedelta(days=5)

        old_final = old_base * recency_multiplier(old_ts)
        fresh_final = fresh_base * recency_multiplier(fresh_ts)

        assert old_final > fresh_final, (
            f"Old-but-canonical doc (final={old_final:.3f}) should still beat "
            f"fresh-but-irrelevant doc (final={fresh_final:.3f})"
        )

    def test_null_timestamp_never_outranks_fresh(self):
        """
        An undated doc (legacy / pre-backfill) with identical base score should
        rank BELOW a fresh doc. Sanity check that NULL is treated conservatively.
        """
        base = 0.80
        fresh_ts = datetime.now(timezone.utc) - timedelta(days=1)
        fresh_final = base * recency_multiplier(fresh_ts)
        null_final  = base * recency_multiplier(None)
        assert fresh_final > null_final


# -----------------------------------------------------------------------------
# QDF intent gate (designed but not enabled)
# -----------------------------------------------------------------------------

class TestRecencyIntent:
    """has_recency_intent() correctness — not yet wired into merge, but tested."""

    @pytest.mark.parametrize("q", [
        "what's the latest payment flow",
        "current architecture for digital payments",
        "show me the newest BRD",
        "what changed this sprint",
        "what is happening today on the merchant onboarding side",
    ])
    def test_positive_intent(self, q):
        assert has_recency_intent(q)

    @pytest.mark.parametrize("q", [
        "how does the payment flow work",
        "explain merchant onboarding",
        "what is digital payments architecture",
        "history of the credit card module",
    ])
    def test_negative_intent(self, q):
        assert not has_recency_intent(q)

    def test_none_and_empty(self):
        assert not has_recency_intent("")
        assert not has_recency_intent(None)
