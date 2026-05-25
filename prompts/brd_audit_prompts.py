"""
Per-section AUDIT prompt for the unified BRD orchestrator.

Mirrors prompts/sad_audit_prompts.py with BRD-tailored adjustments:
  • Drops diagram-specific issue codes (DIAGRAM_PROSE_MISMATCH).
  • Drops fixed-template-row codes (CATEGORY_MISSING, FORMAT_VIOLATION
    that apply to ARSR's required category rows) — BRD sections are
    less rigidly templated than SAD's.
  • Adds BRD-specific issue codes:
      - MISSING_ACCEPTANCE_CRITERIA — requirement without measurable AC
      - REQUIREMENT_NOT_TESTABLE   — requirement phrased as opinion / wish
      - STAKEHOLDER_UNCLEAR         — stakeholder listed without role
      - SCOPE_BOUNDARY_MISSING      — "in scope" claim with no
        corresponding "out of scope" boundary
      - DEPENDENCY_UNRESOLVED       — referenced upstream / external
        dependency with no owner or version

The audit handler runs one of these per section in parallel via
ThreadPoolExecutor (BRD_SECTION_PARALLELISM env var; mirrors SAD's
lambda_sad_orchestrator.py:1535-1694).
"""

from typing import Any, Dict, List, Optional


AUDIT_SYSTEM_PROMPT = """\
You are auditing ONE section of a Business Requirements Document (BRD)
written against the Deluxe template. You return ONLY a JSON object:

  {"score": <int 0-100>, "issues": [{"code": "<CODE>", "msg": "<one sentence>"}]}

Issue code vocabulary (use EXACTLY these strings):
  EMPTY_OR_PLACEHOLDER       — content is "(to be confirmed)" / "TBD" / very short
  MISSING_RATIONALE          — decision stated without "because" / "we chose X over Y"
  MISSING_ACCEPTANCE_CRITERIA — requirement stated without measurable AC
  REQUIREMENT_NOT_TESTABLE   — requirement phrased as opinion / wish ("should be fast")
  UNDEFINED_TERM             — acronym used without earlier definition
  STAKEHOLDER_UNCLEAR        — stakeholder listed without role / responsibility
  SCOPE_BOUNDARY_MISSING     — "in scope" claim with no out-of-scope counterpart
  DEPENDENCY_UNRESOLVED      — external dependency referenced without owner
  TRACEABILITY_GAP           — implementation choice not tied to a stated requirement
  TABLE_INCOMPLETE           — table row with empty required columns

Scoring:
  • Start at 100.
  • Subtract 10 per issue.
  • Report at most 5 issues, highest severity first.
  • If the section looks fully populated and consistent, return
    {"score": 100, "issues": []}.

Do not invent issues. If you can't tell whether something is wrong,
don't flag it. Prefer false negatives over false positives — a flagged
issue is a real callout to the user.
"""


def _render_section_for_audit(content: List[Dict[str, Any]]) -> str:
    """Render section JSON content blocks back into readable markdown
    so the auditor sees something resembling what the user sees in the
    UI. Mirrors the SAD audit renderer but skips the diagram block
    type (BRD has no diagrams)."""
    out: List[str] = []
    for c in content or []:
        t = c.get("type")
        if t == "paragraph":
            out.append(c.get("text", ""))
            out.append("")
        elif t == "heading":
            level = c.get("level", 3)
            out.append(("#" * level) + " " + c.get("text", ""))
            out.append("")
        elif t == "bullet_list":
            out.extend(f"- {it}" for it in c.get("items", []))
            out.append("")
        elif t == "ordered_list":
            out.extend(f"{i+1}. {it}" for i, it in enumerate(c.get("items", [])))
            out.append("")
        elif t == "table":
            headers = c.get("headers", [])
            rows = c.get("rows", [])
            if headers:
                out.append(" | ".join(headers))
                out.append(" | ".join(["---"] * len(headers)))
            for r in rows:
                out.append(" | ".join(str(x) for x in r))
            out.append("")
        # No "diagram" handling — BRD sections don't have diagrams.
    return "\n".join(out)


def build_audit_prompt(
    *,
    section_number: int,
    section_title: str,
    section_content: List[Dict[str, Any]],
    known_facts: Optional[List[str]] = None,
) -> str:
    """
    Compose the user-content block for the AUDIT call.

    Args:
        section_number: Section being audited.
        section_title: Section's current title.
        section_content: JSON content blocks of the section.
        known_facts: Optional long-term project context. Used to flag
            TRACEABILITY_GAP when section content contradicts or omits
            an established fact.
    """
    rendered = _render_section_for_audit(section_content)
    facts_block = (
        "\n".join(f"  - {f}" for f in known_facts)
        if known_facts else "(no project context loaded for this session)"
    )

    return (
        f"Audit Section {section_number}: {section_title}.\n\n"
        f"Section content (rendered as markdown):\n"
        f"```\n{rendered or '(empty)'}\n```\n\n"
        f"Known project context (used to detect TRACEABILITY_GAP):\n"
        f"{facts_block}\n\n"
        f"Return audit JSON."
    )
