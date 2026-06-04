"""
AgentCore Memory long-term `SEMANTIC` strategy override prompt.

Registered once via scripts/configure_brd_memory_strategy.py — after
registration, AgentCore Memory runs this prompt in the background on
every chat event the orchestrator writes under the per-user actor
(actor_id = f"user-{user_id}"). Extraction completes in ~20-40s
asynchronously; the resulting facts are stored in long-term memory
under namespace = f"user-{user_id}:project-{project_id}" and retrieved
by handlers via memory_client.retrieve_memory_records.

The schema MUST stay in lock-step with prompts.brd_doc_relevance_prompts
.DOC_FACTS_SYSTEM_PROMPT — both feed the same facts buffer, so handlers
can iterate over a single canonical shape regardless of source.

Why we override the default SEMANTIC extraction:
  • The built-in extractor is generic ("extract any facts"); it pulls
    chitchat ("the user said hello"), opinions ("I think AWS is fine"),
    and other low-signal noise into long-term memory.
  • For BRD we want a strict 6-category schema mapped to the BRD
    template so retrieval queries can target what they need (e.g.
    "what NFRs has the user established?") without sifting through
    conversational filler.
"""


# ---------------------------------------------------------------------------
# The override prompt — fed verbatim to AgentCore Memory's
# PutMemoryStrategy API call. AgentCore runs this against batches of
# raw conversation events; the JSON it returns becomes a long-term
# memory record stored under the namespace the writes specified.
# ---------------------------------------------------------------------------

BRD_FACTS_EXTRACTION_PROMPT = """\
You extract structured project facts from a Business Requirements
Document gathering conversation. The user is describing a software
project they want a BRD for; Mary (the assistant) is asking follow-up
questions to fill gaps.

Read the conversation and return ONLY a JSON object with these six
keys (each value is a list, possibly empty):

  {
    "stakeholders": [
      {"name": "...", "role": "...", "team": "..."}
    ],
    "non_functional_reqs": [
      {"category": "scale" | "latency" | "availability" | "security" | "compliance" | "other",
       "value": "..."}
    ],
    "constraints": [
      {"type": "deadline" | "budget" | "mandate" | "other",
       "value": "..."}
    ],
    "integrations": [
      {"system": "...", "interaction": "..."}
    ],
    "assumptions": [
      {"statement": "..."}
    ],
    "open_questions": [
      {"question": "...", "blocks_section": "<section title or empty>"}
    ]
  }

Rules — strict:
  • Only extract facts the USER explicitly stated. Do not extract
    questions Mary asked, suggestions Mary made, or speculations.
  • Each fact must be standalone and readable out of context.
    Bad:  "yes, that one"
    Good: "Authentication uses Azure AD with OIDC"
  • Quote the user directly where possible. Do not paraphrase
    aggressively. Do not summarise multiple statements into one fact;
    return them as separate facts.
  • Skip categories the conversation didn't address — empty list is
    fine. Do NOT fabricate facts to fill empty categories.
  • Skip opinions, marketing language, and conversational filler.
    "I think we should consider X" is NOT a fact. "We decided to
    use X" IS a fact.
  • If the user contradicts an earlier statement, return only the
    LATEST version (the user changed their mind). Do not return both.
  • Open questions: only include if the user EXPLICITLY flagged a gap
    ("we haven't decided yet", "TBD"). Don't infer questions from
    Mary's prompts.

These extracted facts will be retrieved in future BRD sessions for the
same (user, project) pair, so accuracy matters more than recall — a
wrong fact in long-term memory contaminates every future session.
"""
