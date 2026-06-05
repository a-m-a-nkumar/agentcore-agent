"""
Marker parser for Joseph's structured event protocol.

Joseph emits `[[JOSEPH_EVENT:<kind>]] ... JSON ... [[/JOSEPH_EVENT]]` blocks
inside his text response. This module finds them, updates the per-session
ConsultingState, pushes SSE state events to the UI, then strips the markers
from the text so the user sees only clean prose.

This replaces the Strands tool-use round-trip mechanism (which the DLX AI
Gateway can't translate correctly to Claude's native tool-use protocol).
With markers we only ever make ONE model call per turn, never sending the
gateway a `role: "tool"` follow-up message.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from services.consulting_state import (
    COVERAGE_AREAS,
    Citation,
    SUB_SCORE_KEYS,
    get_or_create_state,
    push_state_event,
)

logger = logging.getLogger(__name__)

EVENT_PATTERN = re.compile(
    r"\[\[JOSEPH_EVENT:([a-zA-Z_]+)\]\](.*?)\[\[/JOSEPH_EVENT\]\]",
    re.DOTALL,
)

MOCK_DIR = Path(__file__).parent.parent / "mock_data"

_DOMAIN_MAP_CACHE: dict | None = None


def _load_domain_map() -> dict:
    global _DOMAIN_MAP_CACHE
    if _DOMAIN_MAP_CACHE is None:
        try:
            with open(MOCK_DIR / "source_quality_domains.json", "r", encoding="utf-8") as f:
                _DOMAIN_MAP_CACHE = json.load(f)
        except Exception as e:
            logger.warning("Could not load source_quality_domains.json: %s", e)
            _DOMAIN_MAP_CACHE = {"primary": [], "secondary": []}
    return _DOMAIN_MAP_CACHE


def _classify_domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
    except Exception:
        return "directional"

    m = _load_domain_map()
    primary = m.get("primary", [])
    secondary = m.get("secondary", [])

    for d in primary:
        if host == d or host.endswith("." + d):
            return "primary"
    for d in secondary:
        if host == d or host.endswith("." + d):
            return "secondary"
    return "directional"


def parse_and_fire_events(session_id: str, text: str) -> str:
    """
    Find every [[JOSEPH_EVENT:...]] block in `text`, mutate session state,
    push corresponding SSE state events, then return the text with all
    marker blocks removed and whitespace tidied.

    Safe to call with text that contains no markers — returns it unchanged.
    """
    if not text or "[[JOSEPH_EVENT:" not in text:
        return text

    state = get_or_create_state(session_id)
    fired = {"scores": False, "coverage": False, "citations": False}

    for match in EVENT_PATTERN.finditer(text):
        kind = match.group(1).lower()
        payload_raw = match.group(2).strip()

        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError as e:
            logger.warning("Joseph emitted '%s' event with invalid JSON: %s\nRaw: %s",
                           kind, e, payload_raw[:200])
            continue

        try:
            if kind == "scores":
                _handle_scores(state, payload)
                fired["scores"] = True
            elif kind == "coverage":
                _handle_coverage(state, payload)
                fired["coverage"] = True
            elif kind == "citation":
                _handle_citation(state, payload)
                fired["citations"] = True
            else:
                logger.info("Joseph emitted unknown event kind: %s", kind)
        except Exception as e:
            logger.warning("Failed to handle Joseph event '%s': %s", kind, e)

    if fired["scores"]:
        push_state_event("scores", state.to_scores_payload())
    if fired["coverage"]:
        push_state_event("coverage", state.to_coverage_payload())
    if fired["citations"]:
        push_state_event("citations", state.to_citations_payload())

    cleaned = EVENT_PATTERN.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _handle_scores(state, payload):
    if not isinstance(payload, dict):
        return
    for key in SUB_SCORE_KEYS:
        entry = payload.get(key)
        if not isinstance(entry, dict):
            continue
        ss = state.scores[key]
        if "value" in entry:
            raw = entry["value"]
            try:
                ss.value = float(raw) if raw is not None else None
            except (TypeError, ValueError):
                ss.value = None
        if "confidence" in entry:
            conf = entry["confidence"]
            if conf in ("low", "medium", "high"):
                ss.confidence = conf
        if "rationale" in entry:
            ss.rationale = str(entry["rationale"]) if entry["rationale"] is not None else None


def _handle_coverage(state, payload):
    if not isinstance(payload, dict):
        return
    area = str(payload.get("area", "")).lower().strip()
    if area not in COVERAGE_AREAS:
        logger.info("Joseph emitted coverage for unknown area: %s", area)
        return
    state.coverage[area].touched = True
    note = payload.get("note")
    if note is not None:
        state.coverage[area].note = str(note)


def _handle_citation(state, payload):
    if not isinstance(payload, dict):
        return
    url = str(payload.get("url", "")).strip()
    if not url:
        return

    tier = _classify_domain(url)
    title = payload.get("title")
    publisher = payload.get("publisher")

    existing = next((c for c in state.citations if c.url == url), None)
    if existing:
        if publisher:
            existing.publisher = str(publisher)
        if title:
            existing.title = str(title)
        existing.tier = tier
    else:
        state.citations.append(
            Citation(
                url=url,
                title=str(title) if title else None,
                publisher=str(publisher) if publisher else None,
                tier=tier,
                valid=True,  # no live HEAD check in markers mode
            )
        )
