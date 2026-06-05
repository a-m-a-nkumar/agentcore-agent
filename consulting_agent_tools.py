"""
Strands tools for Joseph (the consulting agent).

Eight tools. Five of them push structured state events onto the per-request
SSE queue (via services.consulting_state.push_state_event) so the UI can
render the live scoring panel, coverage indicator, citations strip, and
matrix in real time.

Pattern mirrors `analyst_agent.py` — @tool decorator + typed signature +
substantive docstring (Strands feeds the docstring to the model as the tool
description).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from strands import tool

from services.consulting_state import (
    COVERAGE_AREAS,
    Citation,
    SUB_SCORE_KEYS,
    SubScore,
    current_session_id,
    get_or_create_state,
    push_state_event,
)

logger = logging.getLogger(__name__)

MOCK_DIR = Path(__file__).parent / "mock_data"


def _load_json(filename: str) -> Any:
    path = MOCK_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


@tool
def propose_scores(scores_json: str) -> str:
    """
    Update the live scoring panel with current sub-score hypotheses.

    Call this whenever you form, revise, or firm up a sub-score. Pass the
    FULL current set of all six sub-scores as a JSON string; the UI
    redraws from each call.

    The JSON object must have these six keys, each with value/confidence/rationale:
      Business Impact axis:
        - "financial": net annual value (cost saved + revenue + risk avoided)
        - "productivity": how many people and how much of their work
        - "intent": strategic alignment, sponsor weight, urgency
      Speed to Value axis:
        - "complexity": model + integration + change management
        - "data_platform": data and platform readiness
        - "measurement": ease of measuring success

    Args:
        scores_json: A JSON-encoded string of shape:
            {
              "financial":     {"value": 3, "confidence": "low",    "rationale": "..."},
              "productivity":  {"value": 4, "confidence": "medium", "rationale": "..."},
              "intent":        {"value": 5, "confidence": "high",   "rationale": "..."},
              "complexity":    {"value": 3, "confidence": "low",    "rationale": "..."},
              "data_platform": {"value": 3, "confidence": "medium", "rationale": "..."},
              "measurement":   {"value": 4, "confidence": "high",   "rationale": "..."}
            }
        Use `value: null` if you do not yet have a hypothesis for a sub-score.

    Returns:
        Status string for the agent's own context.
    """
    session_id = current_session_id()
    if not session_id:
        return "no active session"

    try:
        scores = json.loads(scores_json) if isinstance(scores_json, str) else scores_json
    except json.JSONDecodeError as e:
        return f"propose_scores: invalid JSON ({e}). Pass a JSON-encoded string."

    if not isinstance(scores, dict):
        return "propose_scores: expected a JSON object at the top level"

    state = get_or_create_state(session_id)
    for key in SUB_SCORE_KEYS:
        entry = scores.get(key)
        if not isinstance(entry, dict):
            continue
        ss = state.scores[key]
        if "value" in entry:
            raw = entry["value"]
            ss.value = float(raw) if raw is not None else None
        if "confidence" in entry:
            ss.confidence = entry["confidence"]
        if "rationale" in entry:
            ss.rationale = entry["rationale"]

    push_state_event("scores", state.to_scores_payload())
    return f"scores updated; axis impact={state.axes['impact']}, speed={state.axes['speed']}, quadrant={state.quadrant}"


@tool
def coverage_tracker(area: str, note: str) -> str:
    """
    Mark one of the five discovery areas as touched and record a brief note.

    Call this when your discovery touches a new area. The UI uses this to
    show the user that you have a plan — that you are not just running a
    questionnaire.

    Args:
        area: one of "qualification", "value", "viability", "drivers", "instinct"
        note: one-line summary of what you learned about this area in this turn

    Returns:
        Status string for the agent's own context.
    """
    session_id = current_session_id()
    if not session_id:
        return "no active session"

    if area not in COVERAGE_AREAS:
        return f"unknown area '{area}'; valid: {COVERAGE_AREAS}"

    state = get_or_create_state(session_id)
    state.coverage[area].touched = True
    state.coverage[area].note = note

    push_state_event("coverage", state.to_coverage_payload())
    return f"coverage updated for {area}"


@tool
def web_search(query: str) -> str:
    """
    Search the web for benchmarks, regulator publications, vendor claims,
    and other industry context to ground numbers and verify claims.

    Uses real Tavily when TAVILY_API_KEY is set; otherwise returns canned
    industry benchmarks keyed by query keyword (the agent should not treat
    these differently — they are real published numbers).

    Args:
        query: search query, e.g., "AI dispute handling deflection rates banks"

    Returns:
        Formatted list of results with title, url, publisher, and snippet.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if api_key:
        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": 5,
                    "include_raw_content": False,
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            lines = []
            for r in data.get("results", [])[:5]:
                lines.append(
                    f"- {r.get('title', '(no title)')} ({_domain_of(r.get('url', ''))})\n"
                    f"  URL: {r.get('url', '')}\n"
                    f"  {r.get('content', '')[:400]}"
                )
            return "\n\n".join(lines) if lines else "No results."
        except Exception as e:
            logger.warning("Tavily failed, falling back to mock benchmarks: %s", e)

    benchmarks = _load_json("benchmarks.json")
    q_lower = query.lower()
    matched = None
    for keyword, items in benchmarks.items():
        if keyword == "default":
            continue
        if keyword in q_lower:
            matched = items
            break
    if matched is None:
        matched = benchmarks.get("default", [])

    lines = []
    for r in matched:
        lines.append(
            f"- {r['title']} ({r.get('publisher', '')})\n"
            f"  URL: {r['url']}\n"
            f"  {r['content']}"
        )
    return "\n\n".join(lines) if lines else "No results."


@tool
def fetch_url(url: str) -> str:
    """
    Fetch a URL and return its main text content. Use after web_search when
    you need to read a primary source end-to-end to ground a quote or check
    a claim.

    Args:
        url: full URL to fetch

    Returns:
        Plain-text content (best-effort HTML stripping), capped at ~8000 chars.
    """
    try:
        response = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (Joseph/1.0)"},
        )
        response.raise_for_status()
        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(response.text, "html.parser")
            for s in soup(["script", "style", "nav", "header", "footer"]):
                s.decompose()
            text = soup.get_text(separator="\n", strip=True)
        except Exception:
            text = re.sub(r"<[^>]+>", " ", response.text)
            text = re.sub(r"\s+", " ", text).strip()

        if len(text) > 8000:
            text = text[:8000] + "\n\n[...truncated]"
        return text
    except Exception as e:
        return f"Could not fetch URL: {e}"


@tool
def validate_citation(url: str, claimed_publisher: str) -> str:
    """
    Validate a URL before citing it: confirm it returns HTTP 200 and that
    the domain matches the claimed publisher. Classify the source tier
    (primary / secondary / directional) based on a curated allowlist.

    Call this on every URL BEFORE including it in your message's Sources
    block. The UI uses the tier to render a colored badge next to the
    citation.

    Args:
        url: full URL of the source
        claimed_publisher: the publisher you intend to attribute it to,
            e.g., "McKinsey", "Forrester", "Federal Reserve"

    Returns:
        Status string including tier and validity, suitable for the agent
        to read.
    """
    session_id = current_session_id()
    if not session_id:
        return "no active session"

    domains_map = _load_json("source_quality_domains.json")
    primary = set(domains_map.get("primary", []))
    secondary = set(domains_map.get("secondary", []))

    domain = _domain_of(url)
    if any(domain == d or domain.endswith("." + d) for d in primary):
        tier = "primary"
    elif any(domain == d or domain.endswith("." + d) for d in secondary):
        tier = "secondary"
    else:
        tier = "directional"

    valid = True
    try:
        head = requests.head(
            url, timeout=8, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Joseph/1.0)"},
        )
        if head.status_code >= 400:
            try:
                get = requests.get(
                    url, timeout=8, allow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (Joseph/1.0)"},
                )
                valid = get.status_code < 400
            except Exception:
                valid = False
    except Exception:
        valid = False

    publisher_match = bool(
        claimed_publisher
        and any(tok in domain for tok in claimed_publisher.lower().replace(",", "").split())
    )

    state = get_or_create_state(session_id)
    existing = next((c for c in state.citations if c.url == url), None)
    if existing:
        existing.publisher = claimed_publisher
        existing.tier = tier
        existing.valid = valid
    else:
        state.citations.append(
            Citation(
                url=url,
                title=None,
                publisher=claimed_publisher,
                tier=tier,
                valid=valid,
            )
        )

    push_state_event("citations", state.to_citations_payload())

    return (
        f"tier={tier}, valid={valid}, "
        f"publisher_match={publisher_match}, domain={domain}"
    )


@tool
def fetch_confluence(page_id_or_url: str) -> str:
    """
    Fetch a Confluence page by its ID or URL. Use when the user pastes a
    Confluence link or refers to an internal architecture/spec page.

    Args:
        page_id_or_url: a Confluence page ID (numeric) or full URL

    Returns:
        Page title and body text (plain), capped at ~6000 chars.
    """
    page_id = page_id_or_url
    m = re.search(r"/pages/(\d+)", page_id_or_url)
    if m:
        page_id = m.group(1)

    domain = os.getenv("ATLASSIAN_DOMAIN", "").replace("https://", "").replace("http://", "")
    email = os.getenv("ATLASSIAN_EMAIL", "")
    token = os.getenv("ATLASSIAN_API_TOKEN", "")

    if domain and email and token:
        try:
            from services.confluence_service import ConfluenceService

            svc = ConfluenceService(domain=domain, email=email, api_token=token)
            page = svc.get_content_page_by_id(page_id)
            title = page.get("title", "(no title)")
            body_storage = (
                page.get("body", {}).get("storage", {}).get("value", "")
            )
            text = re.sub(r"<[^>]+>", " ", body_storage)
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) > 6000:
                text = text[:6000] + "\n\n[...truncated]"
            return f"# {title}\n\n{text}"
        except Exception as e:
            logger.warning("Confluence fetch failed, using fallback: %s", e)

    fb = _load_json("confluence_fallback.json")
    return f"# {fb['title']}\n\n(Owner: {fb['owner']} · Last updated: {fb['last_updated']})\n\n{fb['body']}"


@tool
def fetch_jira(issue_key: str) -> str:
    """
    Fetch a Jira issue by its key (e.g., "DISP-1247"). Use when the user
    references a Jira ticket — the issue often has the most up-to-date
    sponsor, status, and stakeholder context.

    Args:
        issue_key: Jira issue key like "DISP-1247"

    Returns:
        Issue summary, status, description, and recent comments.
    """
    domain = os.getenv("ATLASSIAN_DOMAIN", "").replace("https://", "").replace("http://", "")
    email = os.getenv("ATLASSIAN_EMAIL", "")
    token = os.getenv("ATLASSIAN_API_TOKEN", "")

    if domain and email and token:
        try:
            from requests.auth import HTTPBasicAuth

            url = f"https://{domain}/rest/api/3/issue/{issue_key}"
            response = requests.get(
                url,
                headers={"Accept": "application/json"},
                auth=HTTPBasicAuth(email, token),
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            fields = data.get("fields", {})
            summary = fields.get("summary", "(no summary)")
            status = fields.get("status", {}).get("name", "(unknown)")
            description = fields.get("description", "") or ""
            if isinstance(description, dict):
                description = json.dumps(description)[:2000]
            return f"# {issue_key}: {summary}\n\nStatus: {status}\n\n{description[:4000]}"
        except Exception as e:
            logger.warning("Jira fetch failed, using fallback: %s", e)

    fb = _load_json("jira_fallback.json")
    comments_text = "\n".join(
        f"  - {c['author']} ({c['created']}): {c['body']}" for c in fb.get("comments", [])
    )
    return (
        f"# {fb['key']}: {fb['summary']}\n\n"
        f"Status: {fb['status']} · Priority: {fb['priority']}\n"
        f"Reporter: {fb['reporter']} · Assignee: {fb['assignee']}\n"
        f"Labels: {', '.join(fb['labels'])}\n\n"
        f"## Description\n{fb['description']}\n\n"
        f"## Comments\n{comments_text}"
    )


@tool
def ingest_document(file_id: str) -> str:
    """
    Retrieve the parsed text of a file the user uploaded earlier in this
    session. The file is parsed server-side (PDF via pypdf, DOCX via
    python-docx, TXT/MD as-is) at upload time.

    Args:
        file_id: the file id returned to the user from the upload endpoint

    Returns:
        The extracted plain text of the file.
    """
    session_id = current_session_id()
    if not session_id:
        return "no active session"

    state = get_or_create_state(session_id)
    f = state.uploaded_files.get(file_id)
    if not f:
        return f"No uploaded file found with id '{file_id}'. Has the user uploaded it?"
    return f"# {f.filename}\n\n{f.text}"


CONSULTING_AGENT_TOOLS = [
    propose_scores,
    coverage_tracker,
    web_search,
    fetch_url,
    validate_citation,
    fetch_confluence,
    fetch_jira,
    ingest_document,
]
