"""
FAQ eval set (spec §9), grouped by query type so the A/B can show where each
backend wins:

  - exact-term   : verbatim/keyword phrasing -> expect bm25 strong.
  - paraphrase   : synonym/rephrase, no shared keywords -> expect embeddings strong.
  - overlapping  : also hits a guide node (guide_ref set) -> exercises dedupe.
  - out-of-scope : not about Velox at all -> must NOT inject (abstention preserved).

`expected` is the ACCEPTABLE set (a hit if any expected id is in top-k). OOS cases
have an empty expected set. `holdout=True` marks rows NOT used to tune the
threshold/weights — faq_eval.py leads with the held-out numbers (spec §9 honesty).
"""

from __future__ import annotations

CASES = [
    # ── exact-term ──────────────────────────────────────────────────────────
    {"query": "how do I generate an Atlassian API token?", "expected": ["atlassian-token-generate"], "type": "exact-term", "holdout": False},
    {"query": "what file formats are supported for transcript upload?", "expected": ["transcript-file-formats"], "type": "exact-term", "holdout": False},
    {"query": "what browsers are supported?", "expected": ["supported-browsers"], "type": "exact-term", "holdout": False},
    {"query": "how long does BRD generation take?", "expected": ["brd-generation-time"], "type": "exact-term", "holdout": False},
    {"query": "can I download the BRD as a Word document?", "expected": ["download-brd-word"], "type": "exact-term", "holdout": True},
    {"query": "what does Sync Docs do?", "expected": ["what-does-sync-docs-do"], "type": "exact-term", "holdout": True},

    # ── paraphrase / synonym ────────────────────────────────────────────────
    {"query": "the dropdowns are blank when I make a project", "expected": ["dropdowns-empty"], "type": "paraphrase", "holdout": False},
    {"query": "login won't work", "expected": ["how-to-login", "authentication-error"], "type": "paraphrase", "holdout": False},
    {"query": "my token stopped working", "expected": ["atlassian-token-expired"], "type": "paraphrase", "holdout": False},
    {"query": "the assistant can't find any of my documents", "expected": ["rag-not-finding-content"], "type": "paraphrase", "holdout": False},
    {"query": "the app is really sluggish today", "expected": ["platform-loading-slowly"], "type": "paraphrase", "holdout": True},
    {"query": "can my whole team work in the same workspace at once?", "expected": ["multiple-members-same-project", "view-others-projects"], "type": "paraphrase", "holdout": True},
    {"query": "I sent the wrong tickets over to Jira, how do I undo that?", "expected": ["pushed-incorrect-jira-stories"], "type": "paraphrase", "holdout": True},

    # ── overlapping (FAQ duplicates a guide node via guide_ref) ──────────────
    {"query": "how do I connect my Atlassian account?", "expected": ["link-atlassian-account"], "type": "overlapping", "holdout": False},
    {"query": "how are Jira epics and stories generated from a BRD?", "expected": ["generate-jira-epics-stories"], "type": "overlapping", "holdout": False},
    {"query": "how does pair programming work?", "expected": ["how-pair-programming-works"], "type": "overlapping", "holdout": False},
    {"query": "how do I push a BRD to Confluence?", "expected": ["push-brd-to-confluence"], "type": "overlapping", "holdout": True},

    # ── out-of-scope (must NOT inject) ──────────────────────────────────────
    {"query": "how do I deploy to a Kubernetes cluster?", "expected": [], "type": "out-of-scope", "holdout": False},
    {"query": "what's the weather in Bangalore today?", "expected": [], "type": "out-of-scope", "holdout": False},
    {"query": "how do I reset my Windows password?", "expected": [], "type": "out-of-scope", "holdout": True},
    {"query": "write me a poem about the ocean", "expected": [], "type": "out-of-scope", "holdout": True},
]
