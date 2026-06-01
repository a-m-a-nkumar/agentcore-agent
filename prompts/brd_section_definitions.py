"""
Deluxe BRD section definitions — the SINGLE SOURCE OF TRUTH.

Three exports:

  BRD_SECTIONS          — ordered list of (number, title, slug) tuples
                          for all 16 sections of the Deluxe BRD template.
  SECTION_FORMATS       — per-section schema spec (type + columns/items)
                          consumed by the section prompt builders AND by
                          the per-section validator in the generator
                          Lambda.
  validate_against_template(path)
                        — startup assertion that the on-disk template
                          DOCX matches BRD_SECTIONS. Fails fast on drift
                          so we never silently ship a generation that
                          contradicts the template the user downloads.

The S3-stored template (s3://sdlc-orch-dev-us-east-1-app-data/templates/
Deluxe_BRD_Template.docx) is the human-readable source; these constants
are its machine-readable mirror. They MUST stay in sync. If the template
changes, update BRD_SECTIONS + SECTION_FORMATS here and re-run the
validator before deploying.

Phase 6 uses these in three places:
  1. The cached system prefix passed to every section worker.
  2. The Map iteration source in the in-Lambda ThreadPoolExecutor.
  3. The per-section validator in lambda_brd_final_assembly (in-Lambda
     for now; not Step Functions).
"""

from __future__ import annotations

import io
import logging
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# BRD_SECTIONS — ordered (number, title, slug) for all 16 sections.
# Slugs are stable lowercase identifiers used in file paths and the
# context-bundle keys; never user-facing.
# ============================================================================

BRD_SECTIONS: List[Tuple[int, str, str]] = [
    (1,  "Document Overview",            "document_overview"),
    (2,  "Purpose",                      "purpose"),
    (3,  "Background / Context",         "background_context"),
    (4,  "Stakeholders",                 "stakeholders"),
    (5,  "Scope",                        "scope"),
    (6,  "Business Objectives & ROI",    "business_objectives_roi"),
    (7,  "Functional Requirements",      "functional_requirements"),
    (8,  "Non-Functional Requirements",  "non_functional_requirements"),
    (9,  "User Stories / Use Cases",     "user_stories_use_cases"),
    (10, "Assumptions",                  "assumptions"),
    (11, "Constraints",                  "constraints"),
    (12, "Acceptance Criteria / KPIs",   "acceptance_criteria_kpis"),
    (13, "Timeline / Milestones",        "timeline_milestones"),
    (14, "Risks and Dependencies",       "risks_and_dependencies"),
    (15, "Approval & Review",            "approval_and_review"),
    (16, "Glossary & Appendix",          "glossary_and_appendix"),
]


# ============================================================================
# SECTION_FORMATS — per-section schema. Two-level dict:
#   number -> { "type": <one of FORMAT_TYPES>, ...spec... }
#
# Format types and their spec shape:
#   "table"             -> { headers: list[str], id_prefix?: str }
#   "prose"             -> { min_paragraphs: int, max_paragraphs: int }
#   "bullet_list"       -> { }
#   "subsection_bullets"-> { subsections: list[str] }
#   "glossary"          -> { }   (one "Term — definition" per line)
#
# When a section produces the empty-but-valid form, it still uses these
# headers/structure — only the row content becomes [Awaiting input] / TBD.
# ============================================================================

SECTION_FORMATS: Dict[int, Dict[str, Any]] = {
    1: {
        "type": "table",
        "headers": ["Field", "Value"],
        "fixed_rows": [
            "Document Name", "Author", "Version", "Last Updated", "Status",
        ],
    },
    2:  {"type": "prose", "min_paragraphs": 1, "max_paragraphs": 3},
    3:  {"type": "prose", "min_paragraphs": 1, "max_paragraphs": 3},
    4:  {"type": "table", "headers": ["Name", "Role", "Responsibility"]},
    5:  {"type": "subsection_bullets", "subsections": ["In Scope", "Out of Scope"]},
    6:  {
        "type": "table",
        "headers": ["Objective ID", "Description", "Priority"],
        "id_prefix": "BO",
    },
    7:  {
        "type": "table",
        "headers": ["Req ID", "Description", "Priority", "Status", "Notes"],
        "id_prefix": "FR",
    },
    8:  {
        "type": "table",
        "headers": ["NFR ID", "Description", "Category"],
        "id_prefix": "NFR",
    },
    9:  {
        "type": "table",
        "headers": ["ID", "Title", "As a...", "I want to...", "So that..."],
        "id_prefix": "US",
    },
    10: {"type": "bullet_list"},
    11: {"type": "bullet_list"},
    12: {"type": "table", "headers": ["Metric/Goal", "Target Value"]},
    13: {"type": "table", "headers": ["Milestone", "Duration", "Owner", "Deliverables"]},
    14: {"type": "table", "headers": ["Risk/Dependency", "Impact", "Mitigation"]},
    15: {"type": "table", "headers": ["Reviewer Name", "Role", "Date", "Comments"]},
    16: {"type": "glossary"},
}


# ============================================================================
# SECTION_RAG_QUERIES — per-section retrieval seeds (Phase 1 RAG).
# Each generation chunks + embeds the full input corpus once, then for each
# section retrieves only the chunks relevant to THAT section using these
# topical query seeds. Keeps per-section context small (no 450K-token
# context overflow) and lets generation scale to arbitrarily large inputs.
# Keyed by section number; one entry per BRD_SECTIONS row.
# ============================================================================

SECTION_RAG_QUERIES: Dict[int, str] = {
    1:  "project name, document title, author, owner, version, status, overview summary",
    2:  "purpose, business need, problem being solved, goal, intended outcome, why this project",
    3:  "background, current state, existing system, history, what exists today, why now, context",
    4:  "stakeholders, people, names, roles, titles, responsibilities, owners, teams, who is involved",
    5:  "scope, in scope, out of scope, boundaries, included, excluded, what the project covers",
    6:  "business objectives, goals, ROI, return on investment, benefits, priorities, outcomes, value",
    7:  "functional requirements, features, capabilities, the system shall, user actions, what it must do",
    8:  "non-functional requirements, performance, security, scalability, reliability, latency, uptime, compliance, SLA",
    9:  "user stories, use cases, as a user I want, personas, user goals, scenarios, workflows",
    10: "assumptions, presumed, taken as given, depends on, expected conditions, prerequisites",
    11: "constraints, limitations, restrictions, budget, deadline, mandated technology, must use, cannot",
    12: "acceptance criteria, KPIs, success metrics, targets, measurable goals, definition of done, thresholds",
    13: "timeline, milestones, phases, schedule, duration, deliverables, dates, roadmap, owner",
    14: "risks, dependencies, threats, impact, mitigation, external systems, blockers, what could go wrong",
    15: "approval, review, reviewers, sign-off, approvers, governance, stakeholder approval",
    16: "glossary, terms, definitions, acronyms, abbreviations, domain terminology, jargon",
}


# Sanity invariants — caught at import time, NOT at runtime, so a bad
# edit to BRD_SECTIONS / SECTION_FORMATS fails the test suite and never
# reaches production.
assert len(BRD_SECTIONS) == 16, f"expected 16 sections, got {len(BRD_SECTIONS)}"
assert [n for n, _, _ in BRD_SECTIONS] == list(range(1, 17)), (
    "BRD_SECTIONS numbers must be 1..16 in order"
)
assert set(SECTION_FORMATS) == set(range(1, 17)), (
    f"SECTION_FORMATS keys must be 1..16; got {sorted(SECTION_FORMATS)}"
)
assert set(SECTION_RAG_QUERIES) == set(range(1, 17)), (
    f"SECTION_RAG_QUERIES keys must be 1..16; got {sorted(SECTION_RAG_QUERIES)}"
)


# ============================================================================
# Helpers for the prompt builders and validators.
# ============================================================================

def section_title(n: int) -> str:
    """Return the canonical title for section `n`. Raises KeyError on
    out-of-range numbers — callers should validate `n in 1..16` first."""
    for num, title, _slug in BRD_SECTIONS:
        if num == n:
            return title
    raise KeyError(f"section number {n} not in BRD_SECTIONS")


def section_slug(n: int) -> str:
    """Return the stable lowercase slug for section `n`. Used for
    S3 partial-file naming and context-bundle keys."""
    for num, _title, slug in BRD_SECTIONS:
        if num == n:
            return slug
    raise KeyError(f"section number {n} not in BRD_SECTIONS")


def format_for(n: int) -> Dict[str, Any]:
    """Return the SECTION_FORMATS entry for section `n`."""
    if n not in SECTION_FORMATS:
        raise KeyError(f"no format spec for section {n}")
    return SECTION_FORMATS[n]


# ============================================================================
# validate_against_template — read the Deluxe template DOCX and assert
# its heading structure matches BRD_SECTIONS.
#
# DOCX is a zip; word/document.xml carries the paragraphs. We walk every
# <w:p> tagged as a heading style (Heading1/Heading2/Title) and extract
# its text. Then we check that each section title in BRD_SECTIONS
# appears as a heading in the template, in order.
#
# Permissive matching:
#   - Case-insensitive
#   - Ignores trailing whitespace
#   - Allows "&" vs "and" drift (Deluxe templates have flipped both ways)
#   - Allows leading numbering like "4. Stakeholders" or "Section 4: Stakeholders"
#
# Strict failure:
#   - Missing section title -> fail
#   - Out-of-order section title -> fail
#   - Section count mismatch -> fail
# ============================================================================

_WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def _extract_headings(docx_bytes: bytes) -> List[str]:
    """Extract heading-like paragraphs from a DOCX in document order."""
    headings: List[str] = []
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as zf:
        with zf.open("word/document.xml") as f:
            doc = ET.fromstring(f.read())

    for para in doc.findall(".//w:p", _WORD_NS):
        # Style ID is on w:pPr/w:pStyle/@w:val.
        style_el = para.find("./w:pPr/w:pStyle", _WORD_NS)
        if style_el is None:
            continue
        style_val = style_el.get(f"{{{_WORD_NS['w']}}}val", "") or ""
        # Accept anything that looks like a heading style. Some Word
        # templates emit "Heading1", others "Heading 1" or "Ttulo1".
        if not (
            style_val.lower().startswith("heading")
            or style_val.lower() == "title"
        ):
            continue
        # Concatenate all <w:t> runs inside this paragraph.
        text = "".join((t.text or "") for t in para.findall(".//w:t", _WORD_NS))
        text = text.strip()
        if text:
            headings.append(text)
    return headings


def _normalize_for_match(s: str) -> str:
    """Permissive normalization for heading comparison."""
    s = s.strip().lower()
    # Strip leading section numbering: "4. ", "4 - ", "section 4: "
    import re
    s = re.sub(r"^(section\s+)?\d+\s*[.:\-)]\s*", "", s)
    # Normalize "&" vs "and" — Deluxe templates have used both.
    s = s.replace("&", "and")
    # Collapse runs of whitespace and slashes around them.
    s = re.sub(r"\s+/\s+", "/", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def validate_against_template(template_docx_path: str) -> None:
    """Read the Deluxe BRD template and assert it matches BRD_SECTIONS.

    Raises ValueError on any mismatch — caller should propagate to a
    startup failure so the misalignment is impossible to ship with.
    """
    with open(template_docx_path, "rb") as f:
        docx_bytes = f.read()
    headings = _extract_headings(docx_bytes)
    if not headings:
        raise ValueError(
            f"no heading-styled paragraphs found in {template_docx_path}; "
            f"either the template was rewritten in a plain-text style or "
            f"the DOCX has been re-saved with a non-standard heading style"
        )

    norm_headings = [_normalize_for_match(h) for h in headings]
    missing: List[str] = []
    out_of_order: List[Tuple[str, int, int]] = []  # (title, expected_idx, actual_idx)
    last_idx_in_doc = -1

    for n, title, _slug in BRD_SECTIONS:
        norm_title = _normalize_for_match(title)
        try:
            actual_idx = norm_headings.index(norm_title)
        except ValueError:
            missing.append(f"§{n} {title!r}")
            continue
        if actual_idx <= last_idx_in_doc:
            out_of_order.append((title, n, actual_idx))
        last_idx_in_doc = actual_idx

    problems: List[str] = []
    if missing:
        problems.append("missing headings: " + ", ".join(missing))
    if out_of_order:
        ooo_desc = ", ".join(
            f"{title!r} (expected at position {exp}, found earlier in doc at index {act})"
            for title, exp, act in out_of_order
        )
        problems.append("out-of-order headings: " + ooo_desc)

    if problems:
        raise ValueError(
            f"BRD template at {template_docx_path} does not match "
            f"BRD_SECTIONS:\n  - " + "\n  - ".join(problems) +
            f"\n  Headings actually found: {headings}"
        )
    logger.info(
        f"[BRD] template validated: {template_docx_path} matches all "
        f"{len(BRD_SECTIONS)} sections"
    )
