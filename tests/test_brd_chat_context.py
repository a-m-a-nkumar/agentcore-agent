"""Tests for the BRD chat-context renderer, section-aware fact decomposition,
and the chat-history leak guard.

These three pieces are the new surface area Fix 1 / Fix 2 added:

  - `services.brd_orchestrator_utils.render_brd_for_chat`
        determinism contract + gap extraction + tiered truncation
  - `lambda_brd_generator._section_to_memory_facts`
        per-section strategy dispatch (per_row / summary / per_bullet /
        paragraph) for the post-generation AgentCore Memory push
  - `services.brd_orchestrator_utils.read_memory_history`
        raise-based leak guard rejecting BRD-snapshot sessionIds

The determinism test is the critical one — if it ever fails, the BRD
prompt cache (cache_control after the <current_brd> block) silently goes
to 0% hit rate and Mary's per-session cost reverts from ~$0.037 to ~$0.27.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_brd_structure.json"


@pytest.fixture
def sample_brd_structure():
    """Hand-crafted small BRD covering paragraph / table / bullet content
    types, [TBD] markers, blank rows, and the §1/§4/§7/§14 strategies."""
    with open(FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# render_brd_for_chat — determinism contract + structural assertions
# =============================================================================

class TestRenderBrdForChat:
    def test_returns_text_and_gaps(self, sample_brd_structure):
        from services.brd_orchestrator_utils import render_brd_for_chat
        text, gaps = render_brd_for_chat(sample_brd_structure)
        assert isinstance(text, str)
        assert isinstance(gaps, list)
        assert text  # not empty
        assert "### §1 Document Overview" in text
        assert "### §2 Purpose" in text
        assert "### §4 Stakeholders" in text

    def test_byte_deterministic_two_calls(self, sample_brd_structure):
        """🚨 CRITICAL: same structure in -> byte-identical output.

        Without this, the BRD prompt cache silently misses every turn
        because the `cache_control` breakpoint requires byte-stable bytes.
        """
        from services.brd_orchestrator_utils import render_brd_for_chat
        a_text, a_gaps = render_brd_for_chat(sample_brd_structure)
        b_text, b_gaps = render_brd_for_chat(sample_brd_structure)
        assert a_text == b_text, (
            "render_brd_for_chat must be byte-deterministic — same `structure` "
            "in must yield identical bytes out. If this fails, someone added "
            "a timestamp or unstable sort to the renderer and the BRD prompt "
            "cache will silently miss every turn."
        )
        assert a_gaps == b_gaps, (
            "gap-list ordering must be stable across calls"
        )

    def test_byte_deterministic_after_input_dict_mutation(self, sample_brd_structure):
        """The renderer must NOT mutate its input dict in a way that
        affects subsequent calls. (We test this by calling twice; if
        the first call mutates `sections` order via .sort() on the
        same list, the second call's output could change.)"""
        from services.brd_orchestrator_utils import render_brd_for_chat
        a_text, _ = render_brd_for_chat(sample_brd_structure)
        # second call with same dict
        b_text, _ = render_brd_for_chat(sample_brd_structure)
        assert a_text == b_text

    def test_gaps_include_tbd_paragraph_and_blank_row(self, sample_brd_structure):
        from services.brd_orchestrator_utils import render_brd_for_chat
        _text, gaps = render_brd_for_chat(sample_brd_structure)
        # §1 has a [TBD] row for "Last Updated"
        gap_sections = {g.split(":")[0].strip() for g in gaps}
        assert any("§1" in s for s in gap_sections), f"expected a §1 gap in {gaps}"
        # §4 has all-[TBD] row
        assert any("§4" in s for s in gap_sections), f"expected a §4 gap in {gaps}"
        # §14 has blank row
        assert any("§14" in s for s in gap_sections), f"expected a §14 gap in {gaps}"

    def test_renders_table_with_headers_and_rows(self, sample_brd_structure):
        from services.brd_orchestrator_utils import render_brd_for_chat
        text, _ = render_brd_for_chat(sample_brd_structure)
        assert "| Name | Role | Responsibility |" in text
        assert "| Alice | Product Owner | Approve scope and priorities |" in text

    def test_renders_bullets_with_in_out_scope(self, sample_brd_structure):
        from services.brd_orchestrator_utils import render_brd_for_chat
        text, _ = render_brd_for_chat(sample_brd_structure)
        assert "- Build chat handler" in text
        assert "- Deploy to Lambda" in text
        assert "- Custom dashboards" in text

    def test_tier1_keeps_long_paragraph_full(self, sample_brd_structure):
        """The fixture's §3 paragraph is ~600 chars. Tier 1 (≤12k) keeps it whole."""
        from services.brd_orchestrator_utils import render_brd_for_chat
        text, _ = render_brd_for_chat(sample_brd_structure)
        # Tier 1 is in effect; the §3 paragraph should appear in full.
        # We assert one phrase from the middle of the long paragraph survives.
        assert "stress-test the tier-2 truncation path" in text

    def test_tier3_collapse_when_sections_huge(self):
        """Stuff §7, §8, §9 each with 200 long-description rows so Tier-2's
        15-row cap still produces an output bigger than 12k chars, forcing
        Tier 3 to collapse §7/§8/§9 into one-line synopses while leaving §4
        (not in the Tier-3 candidate set) untouched."""
        from services.brd_orchestrator_utils import render_brd_for_chat
        pad = "x" * 600  # 600-char pad per row keeps Tier-2 above 12k
        def big_table(prefix):
            return {
                "type": "table",
                "headers": [f"{prefix} ID", "Description", "Priority"],
                "rows": [
                    [f"{prefix}-{i:03d}", f"Requirement #{i} {pad}", "MUST"]
                    for i in range(1, 201)
                ],
            }
        structure = {
            "sections": [
                {"number": 4, "title": "Stakeholders", "content": [{
                    "type": "table",
                    "headers": ["Name", "Role"],
                    "rows": [["Alice", "PO"], ["Bob", "Eng"]],
                }]},
                {"number": 7, "title": "Functional Requirements", "content": [big_table("FR")]},
                {"number": 8, "title": "Non-Functional Requirements", "content": [big_table("NFR")]},
                {"number": 9, "title": "User Stories", "content": [big_table("US")]},
            ]
        }
        text, _ = render_brd_for_chat(structure)
        # Tier 3 collapsed §7/§8/§9 into a synopsis line each
        assert "### §7 Functional Requirements" in text
        assert "200 entries" in text
        assert "FR-001..FR-200" in text
        assert "NFR-001..NFR-200" in text
        assert "US-001..US-200" in text
        # §4 stays full (not a Tier-3 candidate)
        assert "| Alice | PO |" in text
        # Tier 3 collapse means individual FR rows are GONE from output
        assert "Requirement #50" not in text
        # And the final size must be back under the ceiling
        assert len(text) <= 20_000

    def test_empty_structure_handled(self):
        from services.brd_orchestrator_utils import render_brd_for_chat
        text, gaps = render_brd_for_chat({"sections": []})
        assert isinstance(text, str)
        assert gaps == []


# =============================================================================
# _section_to_memory_facts — section-aware decomposition
# =============================================================================

class TestSectionToMemoryFacts:
    def _facts_for(self, sec, project_name="Sample Project", brd_id="brd-test"):
        # Avoid importing the full generator module (which triggers heavy
        # boto3 init). Import only the helper.
        from lambda_brd_generator import _section_to_memory_facts
        return _section_to_memory_facts(
            sec, project_name=project_name, brd_id=brd_id
        )

    def test_per_row_stakeholders_one_fact_per_real_row(self, sample_brd_structure):
        s4 = next(s for s in sample_brd_structure["sections"] if s["number"] == 4)
        facts = self._facts_for(s4)
        # 3 rows; one is all-[TBD] and must be skipped -> 2 facts
        assert len(facts) == 2
        assert all(f.startswith("BRD for project Sample Project, Stakeholders") for f in facts)
        assert any("Alice" in f and "Product Owner" in f for f in facts)
        assert any("Bob" in f and "Tech Lead" in f for f in facts)
        # The [TBD] row MUST NOT have leaked through
        assert not any("[TBD]" in f for f in facts)

    def test_summary_strategy_for_section_7(self, sample_brd_structure):
        s7 = next(s for s in sample_brd_structure["sections"] if s["number"] == 7)
        facts = self._facts_for(s7)
        assert len(facts) >= 1
        # Main fact must surface the count + ID range
        head = facts[0]
        assert "Functional Requirements" in head
        assert "5 entries" in head
        assert "FR-001..FR-005" in head

    def test_paragraph_strategy_for_section_2(self, sample_brd_structure):
        s2 = next(s for s in sample_brd_structure["sections"] if s["number"] == 2)
        facts = self._facts_for(s2)
        assert len(facts) == 1
        assert facts[0].startswith("BRD for project Sample Project, Purpose:")
        assert "sample system" in facts[0]

    def test_per_bullet_strategy_for_scope(self, sample_brd_structure):
        s5 = next(s for s in sample_brd_structure["sections"] if s["number"] == 5)
        facts = self._facts_for(s5)
        # 2 In Scope + 1 Out of Scope = 3 facts
        assert len(facts) == 3
        assert all(f.startswith("BRD for project Sample Project, Scope") for f in facts)

    def test_unknown_section_number_returns_empty(self):
        facts = self._facts_for({
            "number": 99, "title": "Bogus", "content": [],
        })
        assert facts == []


# =============================================================================
# Leak guard — read_memory_history must raise on _brd_snapshot_* sessionId
# =============================================================================

class TestLeakGuard:
    def test_raises_on_brd_snapshot_sessionid(self):
        from services.brd_orchestrator_utils import (
            read_memory_history,
            BRD_SNAPSHOT_SESSION_PREFIX,
        )
        leak_sid = f"{BRD_SNAPSHOT_SESSION_PREFIX}brd-abc12345"
        with pytest.raises(ValueError) as exc:
            read_memory_history(session_id=leak_sid, user_id="u1")
        assert "leak" in str(exc.value).lower() or "snapshot" in str(exc.value).lower()

    def test_normal_sessionid_passes_guard(self, monkeypatch):
        """Real chat session IDs (without the snapshot prefix) must not
        raise; they should fall through to the normal early-return path."""
        from services import brd_orchestrator_utils as utils
        # Force the AGENTCORE_MEMORY_ID-empty early return so we don't
        # actually hit boto3 from this unit test.
        monkeypatch.setattr(utils, "AGENTCORE_MEMORY_ID", "")
        result = utils.read_memory_history(session_id="brd-real-session-id", user_id="u1")
        assert result == []
