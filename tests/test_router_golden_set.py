"""
Router golden-set CI test.

Runs every case in tests/router_golden_set.csv against the live DLX
gateway and asserts the intent-match accuracy is at or above the
threshold for the configured BRD_ROUTER_MODEL.

This test is SKIPPED by default — it makes real LLM calls and costs
tokens. To run it:

    BRD_GOLDEN_SET_LIVE=1 pytest tests/test_router_golden_set.py -v

Expected pass thresholds (codified, can be tuned):
  - Claude-4.5-Sonnet (default):  >= 90% accuracy
  - Claude-Haiku-4.5:              >= 95% accuracy  (router is its only
    use; if Haiku can't hit this, fall back to Sonnet via env var)

The CSV format (one header row, then case rows, # prefix = comment):
    case_id,user_message,stage,brd_exists,file_attached,template_attached,
    transcript_attached,last_card_type,last_proposed_section,
    expected_intent,notes
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from typing import Dict, List

import pytest


CSV_PATH = Path(__file__).parent / "router_golden_set.csv"

# Per-model accuracy floor. Override via BRD_GOLDEN_SET_MIN_ACCURACY env
# var when iterating on the prompt.
ACCURACY_THRESHOLDS: Dict[str, float] = {
    "Claude-4.5-Sonnet": 0.90,
    "Claude-Haiku-4.5":  0.95,
}
DEFAULT_THRESHOLD = 0.90


# --------------------------------------------------------------------------- #
# CSV loading
# --------------------------------------------------------------------------- #

def _load_cases() -> List[Dict[str, str]]:
    """Parse the golden-set CSV, skipping comment rows (# prefix) and
    blank rows."""
    if not CSV_PATH.exists():
        return []
    cases: List[Dict[str, str]] = []
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("case_id"):
                continue
            if row["case_id"].startswith("#"):
                continue
            cases.append(row)
    return cases


CASES = _load_cases()


# --------------------------------------------------------------------------- #
# Live LLM probe (skipped unless explicitly enabled)
# --------------------------------------------------------------------------- #

_LIVE = os.getenv("BRD_GOLDEN_SET_LIVE", "0") == "1"


@pytest.mark.skipif(not _LIVE, reason="set BRD_GOLDEN_SET_LIVE=1 to run live golden-set")
def test_router_golden_set_accuracy() -> None:
    from llm_gateway import chat_completion
    from prompts.brd_intent_router import (
        BRD_INTENTS,
        build_router_prompt,
        get_router_system_prompt,
    )

    model = os.getenv("BRD_ROUTER_MODEL", "Claude-4.5-Sonnet")
    threshold = float(
        os.getenv(
            "BRD_GOLDEN_SET_MIN_ACCURACY",
            str(ACCURACY_THRESHOLDS.get(model, DEFAULT_THRESHOLD)),
        )
    )
    assert CASES, "No cases loaded from tests/router_golden_set.csv"

    system_prompt = get_router_system_prompt()
    correct = 0
    mismatches: List[Dict[str, str]] = []

    for case in CASES:
        user_content = build_router_prompt(
            user_message=case["user_message"],
            stage=case["stage"],
            brd_exists=case["brd_exists"].lower() == "true",
            available_sections=[],  # CSV doesn't carry available sections
            currently_viewing_section=None,
            file_attached=case["file_attached"].lower() == "true",
            template_attached=case["template_attached"].lower() == "true",
            transcript_attached=case["transcript_attached"].lower() == "true",
            last_assistant_card_type=case.get("last_card_type") or None,
            last_assistant_proposed_section=(
                int(case["last_proposed_section"])
                if case.get("last_proposed_section") and case["last_proposed_section"].isdigit()
                else None
            ),
        )
        try:
            raw = chat_completion(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                model=model,
                temperature=0.0,
                max_tokens=400,
                token_source=f"test_router_golden_set:{case['case_id']}",
            )
            parsed = _extract_json(raw)
            actual = parsed.get("intent", "")
        except Exception as e:
            mismatches.append({"case_id": case["case_id"], "expected": case["expected_intent"], "actual": f"<error: {e}>"})
            continue

        # Validate the intent string is in the allowed enum (defensive)
        if actual not in BRD_INTENTS:
            mismatches.append({"case_id": case["case_id"], "expected": case["expected_intent"], "actual": f"<unknown: {actual!r}>"})
            continue

        if actual == case["expected_intent"]:
            correct += 1
        else:
            mismatches.append({"case_id": case["case_id"], "expected": case["expected_intent"], "actual": actual})

    accuracy = correct / len(CASES)
    print(f"\nRouter golden-set: model={model} cases={len(CASES)} correct={correct} accuracy={accuracy:.1%} threshold={threshold:.0%}")
    if mismatches:
        print(f"Mismatches ({len(mismatches)}):")
        for m in mismatches[:20]:
            print(f"  {m['case_id']}: expected={m['expected']} actual={m['actual']}")
        if len(mismatches) > 20:
            print(f"  ... and {len(mismatches) - 20} more")

    assert accuracy >= threshold, (
        f"Router accuracy {accuracy:.1%} below threshold {threshold:.0%} "
        f"for model {model}. See mismatches above."
    )


# --------------------------------------------------------------------------- #
# Static sanity checks (run on every pytest invocation, no LLM needed)
# --------------------------------------------------------------------------- #

def test_golden_set_csv_is_well_formed() -> None:
    assert CSV_PATH.exists(), f"golden set CSV missing at {CSV_PATH}"
    assert CASES, "golden set has no usable cases"


def test_golden_set_covers_every_intent() -> None:
    """Catches the 'forgot to add tests for INTENT_X' regression."""
    from prompts.brd_intent_router import BRD_INTENTS

    seen = {c["expected_intent"] for c in CASES}
    missing = set(BRD_INTENTS) - seen
    assert not missing, f"Golden set missing cases for intents: {sorted(missing)}"


def test_golden_set_intents_are_valid() -> None:
    """Catches typos in expected_intent column (e.g., 'EDIT' instead
    of 'EDIT_SECTION')."""
    from prompts.brd_intent_router import BRD_INTENTS

    valid = set(BRD_INTENTS)
    invalid: List[str] = []
    for c in CASES:
        if c["expected_intent"] not in valid:
            invalid.append(f"{c['case_id']}: {c['expected_intent']}")
    assert not invalid, f"Invalid expected_intent values in CSV: {invalid}"


def test_golden_set_stages_are_valid() -> None:
    """Catches typos in stage column."""
    from db_helper import BRD_SESSION_STAGES

    invalid: List[str] = []
    for c in CASES:
        if c["stage"] not in BRD_SESSION_STAGES:
            invalid.append(f"{c['case_id']}: {c['stage']}")
    assert not invalid, f"Invalid stage values in CSV: {invalid}"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> Dict:
    """The router prompt insists on bare JSON, but some models still
    wrap in markdown fences or add prose. Try strict first, then
    fall back to regex extraction."""
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(raw)
        if not m:
            raise
        return json.loads(m.group(0))
