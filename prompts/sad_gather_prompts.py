"""
Mary-style gathering prompt for the SAD pre-generation phase.

Used when the router classifies an `ADD_INFO` or `INGEST_DOC` turn —
the handler responds with a follow-up question that probes the gap most
likely to make the eventual SAD better.

Different from the analyst's BRD-gathering Mary: this one is biased toward
*architecture* questions (deployment topology, security boundaries, NFR
thresholds, integration touchpoints) instead of *requirements* questions.
"""

from typing import Any, Dict, List


SAD_GATHER_SYSTEM_PROMPT = """\
You are Mary, a senior software architect helping a developer prepare to
generate a Software Architecture Document. The user has just shared one
fact or document. Your job: respond briefly and ask ONE targeted follow-up
question that, if answered, would meaningfully improve the eventual SAD.

Style:
  • Acknowledge the fact in one short sentence ("Got it — noted that …").
  • Then ask exactly one follow-up question, at most one sentence long.
  • Prefer questions that map to concrete SAD sections that are still thin
    (use the section coverage hint provided).
  • Don't ask multi-part questions. Don't repeat earlier questions.
  • If the user has already provided enough context for every section
    category, your response should instead suggest generating the SAD now:
    "I think we have enough to draft a first SAD. Want me to generate it?"

Output: just the prose response — no JSON, no lists, no headers.
"""


def build_gather_prompt(
    *,
    new_fact: str,
    facts_so_far: List[Dict[str, Any]],
    sections_with_facts: List[int],
    last_few_assistant_questions: List[str],
) -> str:
    coverage = (
        ", ".join(str(n) for n in sorted(set(sections_with_facts)))
        if sections_with_facts else "none yet"
    )
    recent_q = (
        "\n".join(f"  • {q}" for q in last_few_assistant_questions[-5:])
        if last_few_assistant_questions else "(none yet)"
    )
    facts_block = (
        "\n".join(
            f"  • [{f.get('provenance','chat')}] {f.get('text','')[:200]}"
            for f in facts_so_far[-15:]
        )
        if facts_so_far else "(no prior facts)"
    )
    return (
        f"New fact from the user:\n  {new_fact}\n\n"
        f"Facts gathered so far (most recent last):\n{facts_block}\n\n"
        f"SAD section numbers that already have at least one targeted fact: {coverage}\n\n"
        f"Questions you've already asked recently — don't repeat:\n{recent_q}\n\n"
        f"Respond with one acknowledgement + one targeted follow-up question."
    )
