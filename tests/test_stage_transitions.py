"""
Stage transitions CI test.

Asserts the BRD orchestrator's state machine matches what the plan
documents under "State machine -> Stage transitions". Catches
divergence between the documented behaviour and the implementation.

Two layers of assertion:

  1. Static — the canonical BRD_STAGE_TRANSITIONS table in db_helper
     contains the exact rows the plan documents (no missing, no extra).
     Runs without any orchestrator code.

  2. Dynamic (Phase 2) — once lambda_brd_orchestrator exposes its
     transition function, assert it honours the table for every
     (from_stage, event) row. Scaffolded but skipped until then.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Layer 1 — static table assertions (no orchestrator needed)
# ---------------------------------------------------------------------------

EXPECTED_TRANSITIONS = {
    # Generation entry
    ("NEW",        "generate_accepted"):       "GENERATING",
    ("GATHERING",  "generate_accepted"):       "GENERATING",
    # Generation exit
    ("GENERATING", "generation_success"):      "DRAFTED",
    ("GENERATING", "generation_failure"):      None,   # revert to prior stage
    ("GENERATING", "generation_cancel"):       None,   # revert to prior stage
    # In-flight signal (stage unchanged but documented for completeness)
    ("GENERATING", "generation_in_progress"):  "GENERATING",
    # Refinement entry
    ("DRAFTED",    "first_refinement_action"): "REFINING",
}


def test_stage_transitions_table_matches_plan() -> None:
    from db_helper import BRD_STAGE_TRANSITIONS

    actual = dict(BRD_STAGE_TRANSITIONS)
    expected = dict(EXPECTED_TRANSITIONS)

    missing = set(expected) - set(actual)
    extra = set(actual) - set(expected)
    wrong = {
        k: (actual[k], expected[k])
        for k in expected
        if k in actual and actual[k] != expected[k]
    }

    assert not missing, f"BRD_STAGE_TRANSITIONS missing entries: {sorted(missing)}"
    assert not extra, (
        f"BRD_STAGE_TRANSITIONS has unexpected entries: {sorted(extra)}. "
        f"Either add them to EXPECTED_TRANSITIONS in this test (and update "
        f"the plan), or remove from db_helper."
    )
    assert not wrong, (
        f"BRD_STAGE_TRANSITIONS values disagree with the plan: "
        f"{wrong} (actual_value, expected_value)"
    )


def test_all_transition_from_stages_are_valid_brd_stages() -> None:
    from db_helper import BRD_SESSION_STAGES, BRD_STAGE_TRANSITIONS

    for (from_stage, _event), _to_stage in BRD_STAGE_TRANSITIONS.items():
        assert from_stage in BRD_SESSION_STAGES, (
            f"transition source stage {from_stage!r} is not in BRD_SESSION_STAGES"
        )


def test_all_transition_to_stages_are_valid_or_none() -> None:
    """to_stage = None is allowed (means 'revert to prior stage')."""
    from db_helper import BRD_SESSION_STAGES, BRD_STAGE_TRANSITIONS

    for _key, to_stage in BRD_STAGE_TRANSITIONS.items():
        if to_stage is None:
            continue
        assert to_stage in BRD_SESSION_STAGES, (
            f"transition target stage {to_stage!r} is not in BRD_SESSION_STAGES"
        )


def test_every_stage_is_reachable() -> None:
    """A stage with no inbound transition (except NEW, which is the
    session-creation default) is unreachable and probably a leftover."""
    from db_helper import BRD_SESSION_STAGES, BRD_STAGE_TRANSITIONS

    reached = {"NEW"}  # session-creation entry point
    for (_from, _event), to_stage in BRD_STAGE_TRANSITIONS.items():
        if to_stage is not None:
            reached.add(to_stage)
    # GATHERING is reached via ADD_INFO/GATHER_REQUIREMENTS handlers
    # which don't appear in the explicit transition table (they're
    # handler side-effects, not events). Mark it manually.
    reached.add("GATHERING")
    unreachable = set(BRD_SESSION_STAGES) - reached
    assert not unreachable, (
        f"Stages with no inbound transition: {unreachable}. "
        f"Either add a transition or remove from BRD_SESSION_STAGES."
    )


# ---------------------------------------------------------------------------
# Layer 2 — dynamic orchestrator test (Phase 2)
# ---------------------------------------------------------------------------

def _try_import_transition_fn():
    try:
        from lambda_brd_orchestrator import apply_stage_transition  # type: ignore[import-not-found]
        return apply_stage_transition
    except ImportError:
        return None


@pytest.mark.skipif(
    _try_import_transition_fn() is None,
    reason="lambda_brd_orchestrator.apply_stage_transition not built yet (Phase 2 work)",
)
def test_orchestrator_honors_every_documented_transition() -> None:
    """Phase 2 deliverable: the orchestrator's transition function
    must dispatch every (from_stage, event) entry to the documented
    to_stage (or to prior_stage when to_stage is None)."""
    apply_stage_transition = _try_import_transition_fn()

    for (from_stage, event), expected_to in EXPECTED_TRANSITIONS.items():
        # Stub prior_stage = "GATHERING" for None-revert cases —
        # the orchestrator should look up the real prior stage from
        # the session row.
        actual_to = apply_stage_transition(  # type: ignore[misc]
            current_stage=from_stage,
            event=event,
            prior_stage="GATHERING" if expected_to is None else None,
        )
        if expected_to is None:
            # Revert path: orchestrator should return whatever
            # prior_stage was. We stub it as GATHERING above.
            assert actual_to == "GATHERING", (
                f"transition ({from_stage}, {event}) should revert to prior_stage; got {actual_to}"
            )
        else:
            assert actual_to == expected_to, (
                f"transition ({from_stage}, {event}) expected {expected_to}, got {actual_to}"
            )
