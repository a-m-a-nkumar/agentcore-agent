"""
NORMAL user-guide test: realistic questions a typical user would type, naturally
distributed (main features, broad+specific mix), NOT adversarial sibling-probing.

Scored the way a help bot should be judged: a hit = the pick is a DEFENSIBLE section
(pred intersects the acceptable set) — i.e. recall@pick — plus strict exact for reference.
Broad/ambiguous queries accept the parent OR any reasonable child.

Run from backend root:
    python -m services.vectorless_rag.normal_eval
"""

from __future__ import annotations

from .router import VeloxGuideRouter
from .tree import GuideTree
from pathlib import Path

# (query, acceptable_nodes) — acceptable = any defensible answer for a real user.
CASES = [
    ("How do I get access to Velox?",                          {"request-access"}),
    ("How do I connect my Jira and Confluence?",               {"connect-atlassian"}),
    ("How do I set up a project?",                             {"project-workspace"}),
    ("How do I generate a BRD from my meeting notes?",         {"brd-agent-pm"}),
    ("How do I make a BRD just by talking to the AI?",         {"brd-agent-analyst"}),
    ("How do I edit my BRD?",                                  {"brd-editing", "brd-editing-section", "brd-editing-full"}),
    ("How do I publish my BRD to Confluence?",                 {"brd-push-confluence"}),
    ("How do I generate Jira stories from my BRD?",            {"planning"}),
    ("How do I ask questions about my project?",               {"knowledge-base-chat"}),
    ("How do I sync my documents?",                            {"automated-sync"}),
    ("How do I set up the AI coding assistant in my IDE?",     {"pair-programming"}),
    ("How do I create architecture diagrams?",                 {"arch-diagrams", "architecture"}),
    ("How do I write a solution architecture document?",       {"arch-sad", "architecture"}),
    ("How do I generate test cases?",                          {"testing", "test-scenarios", "testing-mcp-configured", "testing-mcp-not-configured"}),
    ("How do I link my GitHub account?",                       {"testing-github"}),
    ("How do I find out why my pipeline failed?",              {"devops-logs", "troubleshooting-mcp"}),
    ("How do I generate infrastructure code from my design?",  {"iac-generation", "iac-new-project"}),
    ("How do I deploy and monitor my pipelines?",              {"deployment", "devops", "devops-overview", "devops-pipelines", "devops-deployments"}),
    ("What can Velox do?",                                     {"velox-user-guide"}),
    ("How do I keep my BRD in sync with the code?",            {"drift-alignment", "drift-compare-brd", "drift-accessing-brd-sync"}),
]


def main() -> None:
    tree = GuideTree.from_file(Path(__file__).parent / "velox_guide_tree.json")
    r = VeloxGuideRouter(tree=tree, routing_mode="v1", enable_parent_narrow=True)

    print(f"NORMAL test: {len(CASES)} realistic user queries (recall = defensible pick)\n")
    print(f"{'result':<8}{'query':<50}{'pick':<26}acceptable")
    print("-" * 110)
    hits = exact = 0
    for q, ok_set in CASES:
        nodes, _ = r._route(q)
        pred = set(nodes)
        hit = bool(pred & ok_set)          # found a defensible section
        ex = pred == ok_set or (len(pred) == 1 and next(iter(pred)) in ok_set)
        hits += hit
        exact += (len(pred) >= 1 and pred <= ok_set)
        print(f"{'OK ' if hit else 'MISS':<8}{q[:48]:<50}{str(nodes)[:24]:<26}{sorted(ok_set)[:2]}")

    n = len(CASES)
    print("-" * 110)
    print(f"\nRecall (found a defensible section): {hits}/{n} = {hits/n:.0%}")
    print(f"Precise (pick within acceptable set): {exact}/{n} = {exact/n:.0%}")
    print("\n(Realistic distribution, recall-scored — how a help bot should be judged.)")


if __name__ == "__main__":
    main()
