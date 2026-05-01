"""
Per-section AUDIT prompts.

The Lambda's audit handler runs all 10 of these in parallel. Each returns
a JSON object {"score": 0-100, "issues": [{"code": "...", "msg": "..."}]}
that the frontend renders as a badge + issue list.

Issue codes (small, fixed vocabulary so the UI can render appropriate icons
and the filtering logic stays predictable):
  • EMPTY_OR_PLACEHOLDER  — content is "(to be confirmed)" / "TBD" / very short
  • MISSING_RATIONALE     — claim without "because" / "we chose X over Y"
  • UNMEASURABLE_QA       — quality attribute without numbers / scenarios
  • TRACEABILITY_GAP      — building block not tied to a BRD requirement
  • UNDEFINED_TERM        — acronym used without earlier definition
  • DIAGRAM_PROSE_MISMATCH— prose mentions component(s) not in mxGraph XML
  • CONSTRAINT_VIOLATION  — solution conflicts with a stated constraint
  • TABLE_INCOMPLETE      — table has rows with missing required columns
  • CATEGORY_MISSING      — required category row missing from a fixed table
  • FORMAT_VIOLATION      — output doesn't match the section's required shape

Scoring: start at 100, subtract 10 per issue. Report at most 5 issues
(highest severity first).
"""

from typing import Any, Dict, List, Optional


AUDIT_SYSTEM_PROMPT = """\
You are auditing one section of a Software Architecture Document (SAD)
written against the Deluxe template. You return ONLY a JSON object:

  {"score": <int 0-100>, "issues": [{"code": "<CODE>", "msg": "<one sentence>"}]}

Issue code vocabulary (use exactly these strings):
  EMPTY_OR_PLACEHOLDER, MISSING_RATIONALE, UNMEASURABLE_QA, TRACEABILITY_GAP,
  UNDEFINED_TERM, DIAGRAM_PROSE_MISMATCH, CONSTRAINT_VIOLATION,
  TABLE_INCOMPLETE, CATEGORY_MISSING, FORMAT_VIOLATION

Scoring:
  • Start at 100.
  • Subtract 10 per issue.
  • Report at most 5 issues (highest severity first).
  • If the section looks fully populated and consistent, return
    {"score": 100, "issues": []}.

Do not invent issues. If you can't tell whether something is wrong, don't
flag it.
"""


def _render_section_for_audit(content: List[Dict[str, Any]]) -> str:
    """Render section JSON content blocks back into readable markdown for the auditor."""
    out: List[str] = []
    for c in content or []:
        t = c.get("type")
        if t == "paragraph":
            out.append(c.get("text", ""))
            out.append("")
        elif t == "heading":
            out.append(("#" * (c.get("level", 3))) + " " + c.get("text", ""))
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
        elif t == "diagram":
            out.append(f"[diagram: {c.get('alt', 'architecture diagram')}]")
            out.append("")
    return "\n".join(out)


def build_audit_prompt(
    *,
    section_number: int,
    section_title: str,
    section_content: List[Dict[str, Any]],
    brd_excerpt: str,
    diagram_xml: str,
    required_categories: Optional[List[str]] = None,
) -> str:
    rendered = _render_section_for_audit(section_content)
    extras: List[str] = []
    if required_categories:
        extras.append(
            "REQUIRED categories that MUST appear in any table in this section "
            "(missing → CATEGORY_MISSING):\n"
            + "\n".join(f"  - {c}" for c in required_categories)
        )
    if section_number in (4, 6, 7) and diagram_xml:
        extras.append(
            "Architecture diagram (mxGraph XML — components in this section's "
            "prose should match component labels here; otherwise flag "
            "DIAGRAM_PROSE_MISMATCH):\n"
            f"```xml\n{diagram_xml[:5000]}\n```"
        )
    extras_block = "\n\n".join(extras)

    return (
        f"Audit Section {section_number}: {section_title}.\n\n"
        f"Section content (rendered as markdown):\n"
        f"```\n{rendered or '(empty)'}\n```\n\n"
        f"BRD excerpt (use to detect TRACEABILITY_GAP):\n"
        f"{brd_excerpt or '(no BRD)'}\n\n"
        f"{extras_block}"
    )
