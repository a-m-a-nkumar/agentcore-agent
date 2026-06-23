"""
Iterative greedy descent router (Approach B) — ported from
development/velox_vectorless_rag/router_descent.py, adapted to this package's
tree + gateway LLM. Used ONLY to benchmark against the flat one-shot router.

Decompose -> per sub-question greedy single-select descent (parallel) -> dedup
-> synthesize. MAX_HOPS=5 so a root->depth-4 leaf is reachable.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .llm import GatewayLLM
from .prompts import SYNTHESIS_SYSTEM
from .tree import GuideTree, Node

TREE_PATH = Path(__file__).parent / "velox_guide_tree.json"
MAX_HOPS = 5
MAX_WORKERS = 4

DECOMPOSE_SYSTEM = """You split a user's question into the minimum set of independent sub-questions.
Most questions are single-part — return them unchanged as one sub-question. Only split when the
question asks about two or more SEPARATE topics that live in different parts of a guide and do not
depend on each other. Do NOT split a single topic into steps, and do NOT split a comparison of two
things — that is ONE sub-question.
Return JSON {"sub_questions": ["..."]}. One element if single-part."""

DESCENT_SYSTEM = """You navigate a tree-structured product user guide one level at a time to find the section that
answers a sub-question. You see the CURRENT node, its CHILDREN (more specific), and CROSS_REFS
(related sections to jump to sideways). Reason first, then pick ONE.

Choose:
- "descend": the answer is under a specific child -> set target_id to that child.
- "follow_crossref": a cross-referenced section fits better -> set target_id to it.
- "answer_here": the current node is the right place (covers it / it's a leaf / nothing better).

Return JSON:
{"thinking": "<brief>", "action": "descend|follow_crossref|answer_here", "target_id": "<id or null>"}."""


class DescentRouter:
    name = "B:greedy-descent"

    def __init__(self, tree: Optional[GuideTree] = None, llm: Optional[GatewayLLM] = None,
                 max_hops: int = MAX_HOPS) -> None:
        self.tree = tree or GuideTree.from_file(TREE_PATH)
        self.llm = llm or GatewayLLM()
        self.max_hops = max_hops

    def ask(self, query: str) -> dict:
        t0 = time.perf_counter()
        self.llm.reset()

        subqs = self._decompose(query)
        if len(subqs) == 1:
            results = [self._descend(subqs[0])]
        else:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
                results = list(ex.map(self._descend, subqs))

        nodes: list[Node] = []
        seen: set[str] = set()
        trace: list[dict] = []
        for sq, (node, hops) in zip(subqs, results):
            trace.append({"sub_question": sq, "hops": hops, "landing": node.node_id})
            if node.node_id not in seen:
                seen.add(node.node_id)
                nodes.append(node)

        answer = self._synthesize(query, nodes)
        return {
            "answer": answer,
            "nodes": [n.node_id for n in nodes],
            "trace": trace,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            "llm_calls": self.llm.total_calls,
            "sub_questions": len(subqs),
        }

    def _decompose(self, query: str) -> list[str]:
        user = f"QUERY: {query}\n\nReturn JSON {{\"sub_questions\": [...]}}."
        try:
            resp = self.llm.json_call(DECOMPOSE_SYSTEM, user)
            subs = [s.strip() for s in (resp.get("sub_questions") or []) if isinstance(s, str) and s.strip()]
        except Exception:
            subs = []
        return subs or [query.strip()]

    def _descend(self, sub_question: str) -> tuple[Node, list[dict]]:
        current = self.tree.root
        visited = {current.node_id}
        hops: list[dict] = []
        for _ in range(self.max_hops):
            children = self.tree.children(current.node_id)
            crefs = [c for c in self.tree.resolve_cross_refs(current.node_id) if c.node_id not in visited]
            decision = self._route(sub_question, current, children, crefs)
            hops.append({"node": current.node_id, "action": decision["action"], "target": decision["target_id"]})
            if decision["action"] == "answer_here":
                break
            nxt = self.tree.get(decision["target_id"]) if decision["target_id"] else None
            if nxt is None or nxt.node_id in visited:
                break
            current = nxt
            visited.add(current.node_id)
            if current.is_leaf:
                break
        return current, hops

    def _route(self, sub_question: str, current: Node, children: list[Node], crefs: list[Node]) -> dict:
        lines = [f"SUB_QUESTION: {sub_question}", "",
                 f"CURRENT: id={current.node_id} | {current.title} :: {current.summary}"]
        if children:
            lines.append("\nCHILDREN (descend into ONE if more specific):")
            for c in children:
                lines.append(f"- id={c.node_id} | {c.title} :: {c.summary}")
        else:
            lines.append("\nCHILDREN: none — this is a leaf.")
        if crefs:
            lines.append("\nCROSS_REFS (jump sideways):")
            for c in crefs:
                lines.append(f"- id={c.node_id} | {c.title} :: {c.summary}")
        try:
            raw = self.llm.json_call(DESCENT_SYSTEM, "\n".join(lines))
        except Exception:
            raw = {}
        return self._normalize(raw, children, crefs)

    def _normalize(self, raw: dict, children: list[Node], crefs: list[Node]) -> dict:
        child_ids = {c.node_id for c in children}
        cref_ids = {c.node_id for c in crefs}
        action = raw.get("action")
        target = raw.get("target_id")
        if action not in ("answer_here", "descend", "follow_crossref"):
            action, target = "answer_here", None
        elif action == "descend" and target not in child_ids:
            action, target = "answer_here", None
        elif action == "follow_crossref" and target not in cref_ids:
            action, target = "answer_here", None
        if action == "answer_here":
            target = None
        return {"action": action, "target_id": target}

    def _synthesize(self, query: str, nodes: list[Node]) -> str:
        if not nodes:
            return "I couldn't find anything in the Velox user guide that covers that."
        sections = [{"title": n.title, "details": n.details or n.summary, "guide_link": n.guide_link} for n in nodes]
        user = (
            f"QUESTION:\n{query}\n\nMODE: specific\n\nBREADCRUMB: []\n\n"
            f"SECTIONS:\n{json.dumps(sections, ensure_ascii=False)}\n\nDRILL_DOWN:\n[]\n\nRELATED:\n[]"
        )
        try:
            return self.llm.text_call(SYNTHESIS_SYSTEM, user)
        except Exception as e:  # noqa: BLE001
            return f"(synthesis failed: {e})"
