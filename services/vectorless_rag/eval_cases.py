"""Mandatory eval cases — this tree's known stress points (spec §12)."""

CASES = [
    {"query": "generate terraform from my SAD",               "expected": ["iac-new-project"],        "category": "deep-lineage"},
    {"query": "modify my existing terraform code",            "expected": ["iac-existing-project"],   "category": "deep-lineage"},
    {"query": "how do I connect my GitHub account?",          "expected": ["testing-github"],         "category": "hub-vs-specific"},
    {"query": "what's on the My Profile page?",               "expected": ["my-profile"],             "category": "hub-vs-specific"},
    {"query": "what does the Deployment module do?",          "expected": ["deployment"],             "category": "broad"},
    {"query": "why did my pipeline fail / read the logs",     "expected": ["devops-logs"],            "category": "close-pair"},
    {"query": "diagnose a failed pipeline from my IDE",       "expected": ["troubleshooting-mcp"],    "category": "close-pair"},
    {"query": "generate a BRD from my meeting transcript",    "expected": ["brd-agent-pm"],           "category": "sibling-pair"},
    {"query": "build a BRD by chatting, I have no docs",      "expected": ["brd-agent-analyst"],      "category": "sibling-pair"},
    {"query": "run test cases with MCP set up in my IDE",     "expected": ["testing-mcp-configured"], "category": "sibling-pair"},
    {"query": "do testing without MCP configured",            "expected": ["testing-mcp-not-configured"], "category": "sibling-pair"},
    {"query": "generate Jira stories from a BRD",             "expected": ["planning"],               "category": "single"},
    {"query": "ask a question about my project docs",         "expected": ["knowledge-base-chat"],    "category": "single"},
    {"query": "refresh / sync my project docs",               "expected": ["automated-sync"],         "category": "single"},
    {"query": "create a solution architecture document",      "expected": ["arch-sad"],               "category": "single"},
    {"query": "seed a new BRD from code documentation",       "expected": ["drift-seed-brd"],         "category": "abstain"},
    {"query": "compare my code against the BRD",              "expected": ["drift-compare-brd"],      "category": "single"},
    {"query": "how do I link Bitbucket?",                     "expected": ["testing-bitbucket"],      "category": "hub-vs-specific"},
    {"query": "set up pair programming and keep docs synced", "expected": ["pair-programming", "automated-sync"], "category": "multi-part"},
    {"query": "how do I deploy to a Kubernetes cluster?",     "expected": [],                         "category": "out-of-scope"},
]
