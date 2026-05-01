"""
Per-section drafting prompts for SAD generation.

Each section has its own content shape (paragraph vs table vs ordered list
vs bullets vs diagram caption) and pulls from a different mix of inputs
(BRD, mxGraph XML, RAG chunks, facts, uploaded docs). The build_*_prompt
helpers compose the per-section user message; SECTION_SYSTEM_PROMPT is
shared across all sections and instructs the model to emit JSON content
blocks compatible with sad_structure.json.

Output schema for every section worker is the same — the model returns a
JSON array of content blocks:

  [
    {"type": "paragraph", "text": "..."},
    {"type": "heading", "level": 3, "text": "..."},
    {"type": "ordered_list", "items": ["...", "..."]},
    {"type": "bullet_list", "items": ["...", "..."]},
    {"type": "table", "headers": ["..."], "rows": [["..."]]},
    {"type": "diagram", "s3_key": "sessions/{id}/diagram/logical.svg",
     "alt": "..."}
  ]

Workers that produce the diagram-bearing sections (4, 6, 7) are responsible
for prepending the {"type": "diagram", ...} block themselves; the LLM
generates only the surrounding text.
"""

from typing import Any, Dict, List, Optional


# ============================================
# Shared system prompt
# ============================================

SECTION_SYSTEM_PROMPT = """\
You are an enterprise software architect drafting one section of a Software
Architecture Document (SAD) that follows Deluxe's standard template.

# Output schema (strict)

  • Output ONLY a JSON array of content blocks. No prose before or after.
  • Each block is one of:
      {"type": "paragraph", "text": "..."}
      {"type": "heading", "level": 2|3|4, "text": "..."}
      {"type": "ordered_list", "items": ["...", "..."]}
      {"type": "bullet_list", "items": ["...", "..."]}
      {"type": "table", "headers": ["..."], "rows": [["..."], ["..."]]}
  • Do NOT emit diagram blocks; the orchestrator inserts them where needed.
  • For tables, every row has exactly the same number of columns as the
    headers. Empty cells use "" (not null).
  • Do NOT include section numbers or section titles in your output —
    the template owns those.
  • If you cite a component name, use the exact label from the diagram
    XML when one is provided.

# Grounding (do NOT invent)

You MUST treat the inputs as the only source of truth. Allowed inputs:
  1. The user-provided facts buffer.
  2. The uploaded documents (RAG context).
  3. The BRD excerpt for this section.
  4. The mxGraph diagram XML for this session.
  5. The current draft of this section (when regenerating).

You MUST NOT:
  • Invent product names, vendor names, technologies, frameworks, AWS
    services, region names, sizing numbers, SLAs, compliance posture
    (SOC2 / HIPAA / PCI etc.), or team names that do not appear in the
    inputs above.
  • Pattern-match to a "typical AWS architecture" and fill blanks with
    common defaults. The blank IS the answer when nothing in the inputs
    speaks to it.
  • Make a one-or-the-other decision the user hasn't made (e.g. "ECS
    Fargate" vs "Lambda" — if the diagram doesn't show one, don't pick).

When information is missing, use ONE of these literal phrases inline:
  • "(to be confirmed)"   — preferred. Use when the inputs are silent
                            on something the section needs.
  • "(to be assumed)"     — use only when the document MUST contain a
                            value to be coherent, and you are taking a
                            reasonable assumption from adjacent context
                            (e.g. inferring "us-east-1" because every
                            other component in the diagram is us-east-1).
                            Pair with the inferred value, e.g.
                            "us-east-1 (to be assumed)".

# Regeneration: merge, don't overwrite

When a "Current draft of this section" block is provided, you are
regenerating because new inputs have arrived (a doc, a fact, an updated
diagram). Treat that draft as authored content the user has already
seen and may have lightly edited. Your job:

  1. Carry forward every fact, table row, list item, and decision from
     the current draft UNLESS it directly conflicts with the new inputs.
  2. If the new inputs (facts / RAG / diagram) state something that
     contradicts the current draft, the NEW INPUTS WIN. Replace the
     conflicting bit in-place; keep the surrounding structure.
  3. Add any new fact / row / item that the new inputs introduce and
     that the current draft does not yet cover.
  4. Do NOT delete content from the current draft just because the new
     inputs don't mention it — silence is not a conflict.
  5. Preserve the section's existing structure (heading levels, table
     column order, ordered_list ordering) unless the new inputs force a
     reorganisation.

If you have no new inputs that change anything, return the current
draft's content unchanged (re-emit it verbatim).
"""


# ============================================
# Per-section user-message builders
# ============================================

def _format_brd_excerpt(brd: Optional[Dict[str, Any]], section_keys: Optional[List[str]] = None) -> str:
    """Render a digest of the BRD's relevant sections as plain text."""
    if not brd:
        return "(no BRD available)"
    parts: List[str] = []
    for sec in brd.get("sections", []) or []:
        if section_keys and sec.get("key") not in section_keys:
            continue
        parts.append(f"## {sec.get('title','')}\n")
        for c in sec.get("content", []) or []:
            t = c.get("type")
            if t == "paragraph":
                parts.append(c.get("text", "") + "\n")
            elif t == "bullet_list":
                parts.extend(f"  • {it}\n" for it in c.get("items", []))
            elif t == "ordered_list":
                parts.extend(f"  {i+1}. {it}\n" for i, it in enumerate(c.get("items", [])))
            elif t == "table":
                parts.append("[table omitted]\n")
        parts.append("\n")
    return "".join(parts) or "(BRD has no usable content)"


def _format_facts(facts: List[Dict[str, Any]], section_number: Optional[int] = None) -> str:
    if not facts:
        return "(no user-provided facts yet)"
    out: List[str] = []
    for f in facts:
        if section_number and f.get("suggested_section") not in (None, section_number):
            continue
        provenance = f.get("provenance", "chat")
        out.append(f"  • [{provenance}] {f.get('text','')}")
    return "\n".join(out) or "(no facts targeted to this section)"


def _format_rag(chunks: List[Dict[str, Any]]) -> str:
    if not chunks:
        return "(no RAG context)"
    out: List[str] = []
    for c in chunks[:6]:  # cap to keep prompt size predictable
        out.append(f"<source title=\"{c.get('title','?')}\">\n{c.get('content','')[:1500]}\n</source>")
    return "\n".join(out)


def _diagram_xml_excerpt(xml: str, max_chars: int = 6000) -> str:
    if not xml:
        return "(no diagram available — leave diagram-dependent fields as TBD)"
    return xml[:max_chars]


def _format_previous_content(blocks: Optional[List[Dict[str, Any]]]) -> str:
    """Render the section's existing content blocks back to a digest the LLM
    can read when regenerating. Skips diagram blocks (the orchestrator
    re-attaches them). Returns empty string when there's nothing to merge."""
    if not blocks:
        return ""
    lines: List[str] = []
    for b in blocks:
        t = b.get("type")
        if t == "paragraph":
            lines.append(b.get("text", ""))
        elif t == "heading":
            level = b.get("level", 3)
            lines.append(("#" * max(2, min(level, 5))) + " " + b.get("text", ""))
        elif t == "ordered_list":
            for i, it in enumerate(b.get("items", []) or []):
                lines.append(f"  {i + 1}. {it}")
        elif t == "bullet_list":
            for it in b.get("items", []) or []:
                lines.append(f"  • {it}")
        elif t == "table":
            headers = b.get("headers", []) or []
            rows = b.get("rows", []) or []
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join("---" for _ in headers) + " |")
            for row in rows:
                cells = [str(c).replace("\n", " ").replace("|", "/") for c in row]
                lines.append("| " + " | ".join(cells) + " |")
        elif t == "diagram":
            continue
        lines.append("")
    return "\n".join(lines).strip()


def _previous_block(previous_content: Optional[List[Dict[str, Any]]]) -> str:
    """Helper for the builders — returns the standard 'Current draft' block
    when there is one, else empty string."""
    rendered = _format_previous_content(previous_content)
    if not rendered:
        return ""
    return (
        "\nCurrent draft of this section (merge with new inputs per the "
        "system prompt's regeneration rules — preserve everything that "
        "doesn't conflict with the new inputs):\n"
        f"```markdown\n{rendered}\n```\n"
    )


# 1. Summary — single paragraph
def build_summary_prompt(
    *,
    brd: Dict[str, Any],
    facts: List[Dict[str, Any]],
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 1: SUMMARY of the SAD.\n"
        "One paragraph (3-5 sentences) that captures: what this solution does, "
        "who it's for, the problem it solves, and the headline approach.\n\n"
        "BRD purpose / overview:\n"
        f"{_format_brd_excerpt(brd, ['summary', 'purpose', 'overview', 'document_overview', 'background'])}\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 1)}\n"
        f"{_previous_block(previous_content)}\n"
        "Output: a single JSON array with one paragraph block."
    )


# 2. Problem Statement — single paragraph
def build_problem_statement_prompt(
    *,
    brd: Dict[str, Any],
    facts: List[Dict[str, Any]],
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 2: PROBLEM STATEMENT of the SAD.\n"
        "One paragraph (3-5 sentences) describing: why is this needed, what "
        "collaboration / scalability / compliance gaps does it address, what "
        "happens if we don't build it.\n\n"
        "BRD background / problem context:\n"
        f"{_format_brd_excerpt(brd, ['background', 'problem', 'context', 'purpose'])}\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 2)}\n"
        f"{_previous_block(previous_content)}\n"
        "Output: a single JSON array with one paragraph block."
    )


# 3. ARSR — two tables (In Scope and Out of Scope)
ARSR_IN_SCOPE_CATEGORIES = [
    "Frontend Development",
    "API Decisions",
    "Data Storage Decisions",
    "Access & Authentication and Authorization",
    "Scalability",
    "Deployment",
    "Backup and Recovery",
    "Monitoring and Logging",
    "DR Strategy",
    "Load Balancing",
    "Agent Runtime",
    "Processing Layer",
    "AI / LLM Layer",
    "Object Storage",
    "API Protection",
    "Identity & Access Management",
    "Networking",
]
ARSR_OUT_OF_SCOPE_CATEGORIES = [
    "Mobile Development",
    "End-User Administrative Access",
]


def build_arsr_prompt(
    *,
    brd: Dict[str, Any],
    facts: List[Dict[str, Any]],
    diagram_xml: str,
    rag: List[Dict[str, Any]],
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    in_scope_rows = "\n".join(f"  - {c}" for c in ARSR_IN_SCOPE_CATEGORIES)
    out_scope_rows = "\n".join(f"  - {c}" for c in ARSR_OUT_OF_SCOPE_CATEGORIES)
    return (
        "Draft Section 3: ARCHITECTURAL SIGNIFICANT REQUIREMENTS.\n"
        "Output FOUR blocks in this order:\n"
        "  1. heading level=3, text='In Scope and Architecture Significant Decisions'\n"
        "  2. table with headers ['Category', 'Details', 'Comments'] containing\n"
        f"     EXACTLY these category rows in this order:\n{in_scope_rows}\n"
        "     For each row, fill 'Details' with a concise bullet-list of decisions "
        "(use line breaks via newlines inside the cell where needed) and 'Comments' "
        "with optional rationale. If unknown, write '(to be confirmed)'.\n"
        "  3. heading level=3, text='Out of Scope'\n"
        "  4. table with headers ['Category', 'Details', 'Comments'] containing\n"
        f"     EXACTLY these category rows in this order:\n{out_scope_rows}\n\n"
        "BRD non-functional + scope:\n"
        f"{_format_brd_excerpt(brd, ['nfrs', 'non_functional_requirements', 'scope', 'in_scope', 'out_of_scope', 'functional_requirements'])}\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 3)}\n\n"
        "Architecture diagram (mxGraph XML — components and labels):\n"
        f"```xml\n{_diagram_xml_excerpt(diagram_xml)}\n```\n\n"
        "RAG context from project docs:\n"
        f"{_format_rag(rag)}\n"
        f"{_previous_block(previous_content)}"
    )


# 4. Logical Architecture Diagram — narrative flow following the diagram
def build_logical_diagram_prompt(
    *,
    brd: Dict[str, Any],
    facts: List[Dict[str, Any]],
    diagram_xml: str,
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 4: LOGICAL ARCHITECTURE DIAGRAM FLOW (narrative).\n"
        "The diagram itself will be inserted by the orchestrator — your job is the\n"
        "numbered narrative that follows it.\n\n"
        "Output two blocks:\n"
        "  1. heading level=4, text='Logical Architecture Diagram Flow'\n"
        "  2. ordered_list with 8-14 items. Each item is one step describing how a\n"
        "     request / data flow moves through the components shown in the diagram.\n"
        "     Reference component names exactly as labeled in the diagram XML.\n"
        "     Use the same numbered-flow style as the Deluxe sample SAD: each step\n"
        "     is one full sentence (or two), describing the behavior at that hop.\n\n"
        "Architecture diagram (mxGraph XML — components, edges, labels):\n"
        f"```xml\n{_diagram_xml_excerpt(diagram_xml, max_chars=10000)}\n```\n\n"
        "BRD overview for context:\n"
        f"{_format_brd_excerpt(brd, ['summary', 'purpose', 'functional_requirements'])}\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 4)}\n"
        f"{_previous_block(previous_content)}"
    )


# 5. Pending Decisions — table
def build_pending_decisions_prompt(
    *,
    facts: List[Dict[str, Any]],
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 5: PENDING DECISIONS.\n"
        "Output ONE table with headers ['Category', 'Details', 'Comments'].\n"
        "Include ONE row for each genuinely undecided architectural choice the user\n"
        "has flagged. If there are none, return a table with the headers and a\n"
        "single empty row '['', '', '']'.\n\n"
        "User-provided facts (look for 'undecided', 'TBD', 'open question'):\n"
        f"{_format_facts(facts, 5)}\n"
        f"{_previous_block(previous_content)}"
    )


# 6. Security View — bullets list of significant security decisions
def build_security_view_prompt(
    *,
    brd: Dict[str, Any],
    facts: List[Dict[str, Any]],
    diagram_xml: str,
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 6: SECURITY VIEW — ARCHITECTURAL SIGNIFICANT HIGHLIGHTS.\n"
        "The diagram itself is inserted by the orchestrator. Your output is two blocks:\n"
        "  1. heading level=4, text='Security View Architectural Significant Highlights'\n"
        "  2. bullet_list of 5-8 concrete security claims. Each bullet is a single\n"
        "     sentence describing a decision (encryption, TLS, KMS, IAM, identity\n"
        "     provider, network isolation, secrets handling). Examples from the\n"
        "     Deluxe template:\n"
        "       'All databases are encrypted (KMS)'\n"
        "       'All database access is controlled via Security Group to a dedicated subnet'\n"
        "       'S3 contents encrypted at rest (KMS)'\n"
        "       'SSL/TLS 1.2 required for all API endpoints'\n"
        "       'Authentication & authorized by Entra ID'\n"
        "     Do NOT invent compliance posture (SOC2, HIPAA) unless it's in the inputs.\n\n"
        "BRD security NFRs:\n"
        f"{_format_brd_excerpt(brd, ['nfrs', 'non_functional_requirements', 'security'])}\n\n"
        "Architecture diagram (look for IAM, KMS, encryption, gateway labels):\n"
        f"```xml\n{_diagram_xml_excerpt(diagram_xml)}\n```\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 6)}\n"
        f"{_previous_block(previous_content)}"
    )


# 7. Infrastructure Architecture Diagram — short prose notes
def build_infra_diagram_prompt(
    *,
    brd: Dict[str, Any],
    facts: List[Dict[str, Any]],
    diagram_xml: str,
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 7: INFRASTRUCTURE ARCHITECTURE — supporting prose for the\n"
        "embedded diagram.\n\n"
        "Output ONE paragraph block (3-5 sentences) describing: VPC layout, public\n"
        "vs private subnets, where compute lives (ECS / Lambda), where data lives\n"
        "(RDS / S3), and how the layers are isolated. If a detail is genuinely\n"
        "missing from the inputs, write '(to be confirmed)' inline rather than\n"
        "inventing.\n\n"
        "Architecture diagram (mxGraph XML):\n"
        f"```xml\n{_diagram_xml_excerpt(diagram_xml)}\n```\n\n"
        "BRD deployment / infra NFRs:\n"
        f"{_format_brd_excerpt(brd, ['deployment', 'infrastructure', 'nfrs'])}\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 7)}\n"
        f"{_previous_block(previous_content)}"
    )


# 8. Risks and Mitigations — table
def build_risks_prompt(
    *,
    brd: Dict[str, Any],
    facts: List[Dict[str, Any]],
    rag: List[Dict[str, Any]],
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 8: ARCHITECTURE RISKS AND MITIGATIONS.\n"
        "Output ONE table with headers ['Risk Category', 'Description',\n"
        "'Mitigation', 'Comments']. Include 3-7 rows. Each row covers one\n"
        "concrete risk. Use the exact column order. Examples of categories:\n"
        "Resiliency, Vendor Lock-in, Data Loss, Latency, Cost Overrun,\n"
        "Compliance, AI Hallucination, Performance Degradation, Security.\n\n"
        "BRD risks:\n"
        f"{_format_brd_excerpt(brd, ['risks', 'risks_and_dependencies', 'dependencies'])}\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 8)}\n\n"
        "RAG context:\n"
        f"{_format_rag(rag)}\n"
        f"{_previous_block(previous_content)}"
    )


# 9. Non-Functional Requirements — numbered list grouped under categories
def build_nfrs_prompt(
    *,
    brd: Dict[str, Any],
    facts: List[Dict[str, Any]],
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 9: NON-FUNCTIONAL REQUIREMENTS (NFRs).\n"
        "Output blocks in this order, one heading + one ordered_list per category.\n"
        "Categories MUST appear in this exact order:\n"
        "  1. Performance & Scalability\n"
        "  2. Security\n"
        "  3. Maintainability\n"
        "  4. Observability\n"
        "  5. Backup & Disaster Recovery\n"
        "Under each, list 1-4 numbered NFRs in the format 'NFR-X.Y: <statement>'.\n"
        "Each NFR must be measurable or testable (numbers, percentages, named\n"
        "tools where appropriate). Use the BRD's NFR section as the primary\n"
        "source.\n\n"
        "BRD NFRs:\n"
        f"{_format_brd_excerpt(brd, ['nfrs', 'non_functional_requirements'])}\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 9)}\n"
        f"{_previous_block(previous_content)}"
    )


# 10. Infra Cost Estimate — placeholder paragraph + URL row
def build_cost_prompt(
    *,
    facts: List[Dict[str, Any]],
    previous_content: Optional[List[Dict[str, Any]]] = None,
) -> str:
    return (
        "Draft Section 10: INFRA COST ESTIMATE.\n"
        "Output ONE paragraph block. If the user has provided an AWS pricing\n"
        "calculator URL or numeric cost estimate in the facts, include it\n"
        "verbatim. Otherwise write a single sentence: 'Cost estimate URL to be\n"
        "added once the AWS Pricing Calculator export is finalized.'\n\n"
        "User-provided facts:\n"
        f"{_format_facts(facts, 10)}\n"
        f"{_previous_block(previous_content)}"
    )


# ============================================
# Dispatch helper used by the Lambda's section workers
# ============================================

SECTION_PROMPT_BUILDERS = {
    1: build_summary_prompt,
    2: build_problem_statement_prompt,
    3: build_arsr_prompt,
    4: build_logical_diagram_prompt,
    5: build_pending_decisions_prompt,
    6: build_security_view_prompt,
    7: build_infra_diagram_prompt,
    8: build_risks_prompt,
    9: build_nfrs_prompt,
    10: build_cost_prompt,
}
