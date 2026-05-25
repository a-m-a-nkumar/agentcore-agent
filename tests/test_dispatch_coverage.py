"""
Dispatch coverage CI test.

Asserts that every intent in `prompts.brd_intent_router.BRD_INTENTS`
has a corresponding handler registered in the orchestrator's dispatch
table AND maps to a recognised card type. Catches the
"added a new intent but forgot to wire the handler" regression class
that the planning review (item 1) caught manually.

Until lambda_brd_orchestrator.py lands in Phase 2, the test runs in
"scaffold mode" — it verifies the intent enum is internally consistent
and skips the orchestrator-binding assertions with a clear message.
Once the orchestrator ships, replace the SCAFFOLD_MODE branch with the
real import.
"""

from __future__ import annotations

import pytest


def _try_import_orchestrator():
    """Attempt to import the orchestrator's dispatch table. Returns
    (module, intent_handler_map, valid_card_types) on success or
    (None, None, None) if the orchestrator isn't built yet."""
    try:
        import lambda_brd_orchestrator as orch  # type: ignore[import-not-found]
    except ImportError:
        return None, None, None

    intent_map = getattr(orch, "INTENT_HANDLER_MAP", None)
    card_types = getattr(orch, "VALID_CARD_TYPES", None)
    if intent_map is None or card_types is None:
        return orch, None, None
    return orch, intent_map, card_types


def test_router_intents_match_valid_stages_map() -> None:
    """The router's INTENT_VALID_STAGES dict must cover every intent
    in BRD_INTENTS — otherwise the orchestrator would crash on stage
    validation for whichever intent is missing."""
    from prompts.brd_intent_router import BRD_INTENTS, INTENT_VALID_STAGES

    missing = set(BRD_INTENTS) - set(INTENT_VALID_STAGES.keys())
    extra = set(INTENT_VALID_STAGES.keys()) - set(BRD_INTENTS)
    assert not missing, f"INTENT_VALID_STAGES missing entries for: {sorted(missing)}"
    assert not extra, (
        f"INTENT_VALID_STAGES has extra entries not in BRD_INTENTS: {sorted(extra)} "
        f"(probably an old intent that was renamed without updating the stage map)"
    )


def test_intent_count_matches_documentation() -> None:
    """The plan and the prompt module both reference '12 intents'. If
    we change the count we must update both — this test catches drift."""
    from prompts.brd_intent_router import BRD_INTENTS

    assert len(BRD_INTENTS) == 12, (
        f"Expected 12 intents per plan; got {len(BRD_INTENTS)}. "
        f"Update tests/test_dispatch_coverage.py AND the plan AND the "
        f"intent router docstring."
    )


def test_intents_are_unique() -> None:
    """A duplicate intent in the enum would silently shadow itself."""
    from prompts.brd_intent_router import BRD_INTENTS

    assert len(BRD_INTENTS) == len(set(BRD_INTENTS)), (
        f"BRD_INTENTS contains duplicates: {BRD_INTENTS}"
    )


def test_intents_use_screaming_snake_case() -> None:
    """Catches typos like 'edit_section' or 'EditSection' that would
    drift from the router output JSON schema."""
    from prompts.brd_intent_router import BRD_INTENTS

    bad = [i for i in BRD_INTENTS if not i.isupper() or "-" in i or " " in i]
    assert not bad, f"Intents must be SCREAMING_SNAKE_CASE: bad ones = {bad}"


# --------------------------------------------------------------------------- #
# Orchestrator-binding tests — skipped while the orchestrator is
# under construction. Phase 2 deliverable.
# --------------------------------------------------------------------------- #

_ORCH, _HANDLERS, _CARDS = _try_import_orchestrator()


@pytest.mark.skipif(
    _ORCH is None, reason="lambda_brd_orchestrator not built yet (Phase 2 work)"
)
def test_every_intent_has_a_handler() -> None:
    from prompts.brd_intent_router import BRD_INTENTS

    assert _HANDLERS is not None, (
        "Orchestrator module imports but exposes no INTENT_HANDLER_MAP. "
        "Phase 2 task: define INTENT_HANDLER_MAP: dict[str, callable]."
    )
    missing = [i for i in BRD_INTENTS if i not in _HANDLERS]
    assert not missing, f"Orchestrator dispatch missing handlers for: {missing}"


@pytest.mark.skipif(
    _ORCH is None or _CARDS is None,
    reason="VALID_CARD_TYPES not exported yet (Phase 2 work)",
)
def test_every_handler_returns_known_card_type() -> None:
    """Calls each handler with a minimal stub event and asserts the
    returned card type is in VALID_CARD_TYPES. Lightweight smoke test
    that catches the 'returns {} instead of a card' regression class."""
    from prompts.brd_intent_router import BRD_INTENTS

    # Phase 2: replace this with actual handler invocations once
    # the orchestrator defines a stub-event factory.
    pytest.skip("Phase 2: implement handler-stub invocation in test")
