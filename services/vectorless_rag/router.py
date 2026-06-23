"""
Velox vectorless RAG router (spec §2-§6).

Flat one-shot routing -> code-derived mode + adornments -> woven synthesis.
~2 LLM calls/query (route + synth). No embeddings, no descent.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .faq.prompts_faq import render_faq_block
from .llm import GatewayLLM
from .prompts import (
    CANDIDATE_SYSTEM,
    NARROW_SYSTEM,
    PARENT_NARROW_SYSTEM,
    ROUTING_SYSTEM,
    ROUTING_SYSTEM_V2,
    SUFFICIENCY_SYSTEM,
    SYNTHESIS_SYSTEM,
)
from .tree import GuideTree, Node

TREE_PATH = Path(__file__).parent / "velox_guide_tree.json"

MAX_NODES = 3                       # cap on routed nodes (spec §2)
INCLUDE_CROSSREFS_IN_ROUTING = False  # spec §2 — v1 off
ENABLE_SUFFICIENCY_CHECK = False      # spec §3 — default off

# ── FAQ integration config (single switch lives in env_vdi/env_local) ──────────
try:
    from environment import (  # type: ignore
        FAQ_ENABLED,
        FAQ_THRESHOLD,
        FAQ_TOP_K,
        FAQ_TOP_N_INJECT,
    )
except Exception:  # standalone (eval scripts / smoke tests) — read env directly
    import os as _os

    FAQ_ENABLED = _os.getenv("FAQ_ENABLED", "true").lower() == "true"
    FAQ_TOP_K = int(_os.getenv("FAQ_TOP_K", "3"))
    FAQ_THRESHOLD = float(_os.getenv("FAQ_THRESHOLD", "0.5"))
    FAQ_TOP_N_INJECT = int(_os.getenv("FAQ_TOP_N_INJECT", "1"))

_OVERVIEW_RE = re.compile(r"\b(what is|what's|what does|overview of|tell me about|explain)\b", re.I)

_UNSET = object()  # "not provided" sentinel — distinct from an explicit None (FAQ off)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


class VeloxGuideRouter:
    def __init__(
        self,
        tree: Optional[GuideTree] = None,
        llm: Optional[GatewayLLM] = None,
        enable_sufficiency: bool = ENABLE_SUFFICIENCY_CHECK,
        routing_mode: str = "hybrid",  # "hybrid" LLM+BM25 (best); also "v1"/"v2"/"v3"
        enable_parent_narrow: bool = False,  # LLM parent-narrow; hybrid handles its own
        faq_retriever=_UNSET,  # FaqRetriever already indexed; None=off; omitted=lazy-build
    ) -> None:
        self.tree = tree or GuideTree.from_file(TREE_PATH)
        self.llm = llm or GatewayLLM()
        self.enable_sufficiency = enable_sufficiency
        self.routing_mode = routing_mode
        self.enable_parent_narrow = enable_parent_narrow
        # Provided (even as None) -> use as-is. Omitted -> lazy-build per config.
        self._faq_provided = faq_retriever is not _UNSET
        self._faq_retriever = None if faq_retriever is _UNSET else faq_retriever
        self._faq_built = self._faq_provided

    # ── public: full pipeline ──────────────────────────────────────
    def ask(self, query: str) -> dict:
        t0 = time.perf_counter()
        self.llm.reset()
        trace: list[str] = []

        # FAQ search runs ALONGSIDE user-guide routing (spec §5.2). For bm25 it's
        # pure-code (µs); for embeddings/hybrid the query-embed network call
        # overlaps the routing Haiku call, so it adds no wall-clock on the hot path.
        with ThreadPoolExecutor(max_workers=1) as ex:
            faq_future = ex.submit(self._faq_search, query) if self.faq is not None else None
            node_ids, thinking = self._route(query)
            faq_candidates = faq_future.result() if faq_future else []

        trace.append(f"thinking:{thinking}")
        trace.append("picked:" + (",".join(node_ids) or "(none)"))

        # Threshold gate (spec §6): top FAQ score must clear FAQ_THRESHOLD to inject.
        faq_top = faq_candidates[0] if faq_candidates else None
        faq_included = bool(faq_top and faq_top[1] > FAQ_THRESHOLD)
        faq_inject = faq_candidates[:FAQ_TOP_N_INJECT] if faq_included else []
        faq_info = self._faq_info(faq_candidates, faq_top, faq_included)

        # Empty guide pick. Two sub-cases (spec §6 abstain-rescue):
        #  - an FAQ cleared the threshold -> answer from the FAQ alone (1 Sonnet call);
        #  - otherwise -> out of scope, no synthesis call (preserve abstention, spec §12).
        if not node_ids:
            if faq_included:
                trace.append(f"faq:+{faq_top[0].faq_id}")
                trace.append("faq:abstain_rescue")
                answer = self._synthesize(query, [], "specific", [], [], [], faq_inject=faq_inject)
                return self._envelope(
                    answer=answer, nodes=[], mode="faq_only", breadcrumb=[], drill_down=[],
                    related=[], guide_links=[], trace=trace, t0=t0,
                    faq_info=faq_info, sources_fired=["faq"], overlap=False,
                )
            return self._envelope(
                answer="I couldn't find anything in the Velox user guide that covers that.",
                nodes=[], mode="abstain", breadcrumb=[], drill_down=[], related=[],
                guide_links=[], trace=trace + ["mode:out_of_scope"], t0=t0,
                faq_info=faq_info, sources_fired=[], overlap=False,
            )

        picked = [self.tree.get(n) for n in node_ids]
        mode = self._derive_mode(picked, query)
        trace.append(f"mode:{mode}")

        breadcrumb = self.tree.breadcrumb(picked[0].node_id)
        drill_down = self._drill_down(picked[0]) if mode == "broad" else []
        related = self._related(picked, exclude=set(node_ids) | {d["node_id"] for d in drill_down})

        # §3 sufficiency check (optional) — may append one cross-ref node.
        if self.enable_sufficiency and mode == "specific":
            extra = self._sufficiency(query, picked)
            if extra and extra.node_id not in node_ids:
                picked.append(extra)
                node_ids.append(extra.node_id)
                trace.append(f"sufficiency:+{extra.node_id}")

        # Dedupe signal (spec §7): FAQ duplicates THIS guide node -> tell synthesis
        # to merge into one answer with a single guide_link.
        overlap = bool(faq_included and faq_top[0].guide_ref == picked[0].node_id)
        if faq_included:
            trace.append(f"faq:+{faq_top[0].faq_id}" + (" overlap" if overlap else ""))

        answer = self._synthesize(
            query, picked, mode, breadcrumb, drill_down, related,
            faq_inject=faq_inject, overlap=overlap,
        )

        sources_fired = ["user_guide"] + (["faq"] if faq_included else [])
        guide_links = self._guide_links(picked)
        return self._envelope(
            answer=answer, nodes=node_ids, mode=mode, breadcrumb=breadcrumb,
            drill_down=drill_down, related=related, guide_links=guide_links,
            trace=trace, t0=t0, faq_info=faq_info, sources_fired=sources_fired,
            overlap=overlap,
        )

    @staticmethod
    def _faq_info(faq_candidates: list, faq_top, faq_included: bool) -> dict:
        """The FAQ instrumentation slice of the envelope (spec §8)."""
        return {
            "faq_top_score": round(faq_top[1], 4) if faq_top else None,
            "faq_included": faq_included,
            "faq_id": faq_top[0].faq_id if faq_top else None,
            "faq_candidates": [
                {"faq_id": e.faq_id, "score": round(s, 4)} for e, s in faq_candidates
            ],
        }

    # ── public: deep-dive (spec §5) — known id, NO routing ─────────
    def expand(self, node_id: str) -> dict:
        t0 = time.perf_counter()
        self.llm.reset()
        node = self.tree.get(node_id)
        if node is None:
            return self._envelope(
                answer=f"Unknown section '{node_id}'.", nodes=[], mode="abstain",
                breadcrumb=[], drill_down=[], related=[], guide_links=[],
                trace=[f"expand:unknown:{node_id}"], t0=t0,
            )
        mode = "abstain" if node.coming_soon else "specific"
        breadcrumb = self.tree.breadcrumb(node_id)
        related = self._related([node], exclude={node_id})
        answer = self._synthesize(f"Tell me about {node.title}", [node], mode, breadcrumb, [], related)
        return self._envelope(
            answer=answer, nodes=[node_id], mode=mode, breadcrumb=breadcrumb,
            drill_down=[], related=related, guide_links=self._guide_links([node]),
            trace=[f"expand:{node_id}", f"mode:{mode}"], t0=t0,
        )

    # ── 1. routing (spec §2) ───────────────────────────────────────
    @property
    def bm25(self):
        if getattr(self, "_bm25", None) is None:
            from .bm25 import BM25Index
            self._bm25 = BM25Index(self.tree)
        return self._bm25

    @property
    def faq(self):
        """The active FAQ retriever (or None). Built once per config when not
        supplied; the API supplies a shared, pre-indexed instance so the index
        isn't rebuilt per request."""
        if not self._faq_built:
            self._faq_built = True
            if FAQ_ENABLED and self._faq_retriever is None:
                try:
                    from .faq import build_retriever
                    self._faq_retriever = build_retriever()
                except Exception:  # FAQ must never break the guide path
                    self._faq_retriever = None
        return self._faq_retriever

    def _faq_search(self, query: str) -> list[tuple]:
        """Top-k FAQ hits as [(entry, norm_score), ...]; [] on any failure or when
        disabled. Never raises — the FAQ source is additive, not load-bearing."""
        r = self.faq
        if r is None:
            return []
        try:
            return r.search(query, FAQ_TOP_K)
        except Exception:
            return []

    def _route(self, query: str) -> tuple[list[str], str]:
        if self.routing_mode == "v3":
            return self._route_recall_narrow(query)
        if self.routing_mode == "hybrid":
            return self._route_hybrid(query)
        sections = self.tree.routing_list()
        user = (
            "SECTIONS:\n" + json.dumps(sections, ensure_ascii=False)
            + f"\n\nQUESTION:\n{query}"
        )
        system = ROUTING_SYSTEM_V2 if self.routing_mode == "v2" else ROUTING_SYSTEM
        try:
            resp = self.llm.json_call(system, user)
        except Exception:
            resp = {}
        thinking = str(resp.get("thinking") or "").strip()
        # v2 scope gate: empty pick ONLY when explicitly out of scope, not from uncertainty.
        if self.routing_mode == "v2" and resp.get("in_scope") is False:
            return [], thinking
        raw_ids = resp.get("node_list") or []
        valid: list[str] = []
        for nid in raw_ids:
            if self.tree.get(nid) is not None and nid not in valid:
                valid.append(nid)
            if len(valid) >= MAX_NODES:
                break
        if self.enable_parent_narrow:
            valid = self._apply_parent_narrow(query, valid)
        return valid, thinking

    def _apply_parent_narrow(self, query: str, node_ids: list[str]) -> list[str]:
        """If a pick is a parent (has children), drop one level to the right child unless
        the question is a whole-section overview. Cheap: one call per parent pick only."""
        out: list[str] = []
        for nid in node_ids:
            node = self.tree.get(nid)
            child = self._narrow_into_children(query, node) if (node and node.child_ids) else nid
            if child not in out:
                out.append(child)
        return out

    def _narrow_into_children(self, query: str, parent: Node) -> str:
        children = self.tree.children(parent.node_id)
        listing = "\n".join(f"- id={c.node_id} | {c.title} :: {c.summary}" for c in children)
        user = f"QUESTION:\n{query}\n\nCHILDREN:\n{listing}"
        try:
            resp = self.llm.json_call("", user)
            nid = resp.get("node_id")
        except Exception:
            nid = None
        return nid if nid in {c.node_id for c in children} else parent.node_id

    def _route_recall_narrow(self, query: str) -> tuple[list[str], str]:
        """v3: stage 1 casts a wide net (recall), stage 2 narrows to the best (precision).
        2 routing calls; abstains via the scope gate or when the shortlist truly fits nothing."""
        sections = self.tree.routing_list()
        cand_user = "SECTIONS:\n" + json.dumps(sections, ensure_ascii=False) + f"\n\nQUESTION:\n{query}"
        try:
            cand = self.llm.json_call(CANDIDATE_SYSTEM, cand_user)
        except Exception:
            cand = {}
        thinking = str(cand.get("thinking") or "").strip()
        if cand.get("in_scope") is False:
            return [], thinking

        cand_ids: list[str] = []
        for nid in (cand.get("node_list") or []):
            if self.tree.get(nid) is not None and nid not in cand_ids:
                cand_ids.append(nid)
            if len(cand_ids) >= 6:
                break
        if not cand_ids:
            return [], thinking
        if len(cand_ids) == 1:
            return cand_ids, thinking

        # Stage 2 — narrow on just the shortlist's summaries.
        shortlist = [
            {"node_id": n.node_id, "title": n.title, "summary": n.summary}
            for n in (self.tree.get(i) for i in cand_ids)
        ]
        narrow_user = f"QUESTION:\n{query}\n\nSHORTLIST:\n{json.dumps(shortlist, ensure_ascii=False)}"
        try:
            nar = self.llm.json_call(NARROW_SYSTEM, narrow_user)
        except Exception:
            nar = {}
        best: list[str] = []
        for nid in (nar.get("node_list") or []):
            if nid in cand_ids and nid not in best:
                best.append(nid)
            if len(best) >= MAX_NODES:
                break
        return best, (str(nar.get("thinking") or "").strip() or thinking)

    def _route_hybrid(self, query: str) -> tuple[list[str], str]:
        """LLM one-shot (intent) + BM25 (exact-term keyword), no extra LLM calls.
        BM25 narrows parents, breaks sibling ties, and rescues clear false-abstentions."""
        # 1) LLM one-shot pick (v1 prompt).
        sections = self.tree.routing_list()
        user = "SECTIONS:\n" + json.dumps(sections, ensure_ascii=False) + f"\n\nQUESTION:\n{query}"
        try:
            resp = self.llm.json_call(ROUTING_SYSTEM, user)
        except Exception:
            resp = {}
        thinking = str(resp.get("thinking") or "").strip()
        llm_ids = [n for n in (resp.get("node_list") or []) if self.tree.get(n) is not None][:MAX_NODES]

        ranked = self.bm25.rank(query)
        top_id, top_score = ranked[0] if ranked else (None, 0.0)
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0

        # 2) LLM abstained -> rescue ONLY if BM25 has a clear keyword winner (else stay empty).
        if not llm_ids:
            if top_score > 0 and (second_score == 0 or top_score >= 1.5 * second_score):
                return [top_id], thinking
            return [], thinking

        # 3) Refine ONLY where BM25 helps: break ties among deep (d2+) leaf siblings.
        #    Leave module-level (d1) and parent/overview picks to the LLM — BM25 has no
        #    intent signal there and only does harm (it broke "what can Velox do" + "sync docs").
        out: list[str] = []
        for nid in llm_ids:
            node = self.tree.get(nid)
            if not node.child_ids and node.depth >= 2 and node.parent_id:
                sibs = [c.node_id for c in self.tree.children(node.parent_id)]
                best_sib, sib_s = self.bm25.best_among(query, sibs)
                cur_s = self.bm25.score(query, nid)
                out.append(best_sib if (best_sib and best_sib != nid and sib_s >= 1.5 * max(cur_s, 1e-9)) else nid)
            else:
                out.append(nid)  # module (d1) leaf or parent/overview pick -> trust the LLM
        # de-dupe
        seen, deduped = set(), []
        for n in out:
            if n not in seen:
                seen.add(n); deduped.append(n)
        return deduped, thinking

    # ── 2. mode (spec §4, code-derived) ────────────────────────────
    def _derive_mode(self, picked: list[Node], query: str) -> str:
        first = picked[0]
        if first.coming_soon:
            return "abstain"
        if len(picked) > 1:
            return "specific"  # multi-node -> weave the specifics together
        leaned_broad = bool(first.child_ids) and (first.depth <= 1 or bool(_OVERVIEW_RE.search(query)))
        return "broad" if leaned_broad else "specific"

    # ── 3. adornments (spec §4) ────────────────────────────────────
    def _drill_down(self, parent: Node) -> list[dict]:
        return [{"node_id": c.node_id, "label": c.title} for c in self.tree.children(parent.node_id)]

    def _related(self, picked: list[Node], exclude: set[str]) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set(exclude)
        for n in picked:
            for ref in self.tree.resolve_cross_refs(n.node_id):
                if ref.node_id not in seen:
                    seen.add(ref.node_id)
                    out.append({"node_id": ref.node_id, "label": ref.title})
        return out

    def _guide_links(self, picked: list[Node]) -> list[dict]:
        links, seen = [], set()
        for n in picked:
            gl = n.guide_link
            if gl and gl.get("url") and gl["url"] not in seen:
                seen.add(gl["url"])
                links.append(gl)
        return links

    # ── 4. synthesis (spec §4 / §11.2) ─────────────────────────────
    def _synthesize(self, query, picked, mode, breadcrumb, drill_down, related,
                    faq_inject: Optional[list] = None, overlap: bool = False) -> str:
        sections = [
            {"title": n.title, "details": n.details or n.summary, "guide_link": n.guide_link}
            for n in picked
        ]
        user = (
            f"QUESTION:\n{query}\n\n"
            f"MODE: {mode}\n\n"
            f"BREADCRUMB: {breadcrumb}\n\n"
            f"SECTIONS:\n{json.dumps(sections, ensure_ascii=False)}\n\n"
            f"DRILL_DOWN:\n{json.dumps(drill_down, ensure_ascii=False)}\n\n"
            f"RELATED:\n{json.dumps(related, ensure_ascii=False)}"
        )
        # FAQ block folds into THIS same call (no second LLM call, spec §6).
        if faq_inject:
            user += f"\n\nOVERLAP: {str(bool(overlap)).lower()}\n\n" + render_faq_block(faq_inject)
        try:
            return self.llm.text_call(SYNTHESIS_SYSTEM, user)
        except Exception as e:  # noqa: BLE001
            return f"(synthesis failed: {e})"

    # ── 5. sufficiency (spec §3, optional) ─────────────────────────
    def _sufficiency(self, query: str, picked: list[Node]) -> Optional[Node]:
        candidates = []
        for n in picked:
            for ref in self.tree.resolve_cross_refs(n.node_id):
                candidates.append({"node_id": ref.node_id, "title": ref.title, "summary": ref.summary})
        if not candidates:
            return None
        details = "\n\n".join(f"[{n.title}]\n{n.details or n.summary}" for n in picked)
        user = (
            f"QUESTION:\n{query}\n\nSECTION DETAILS:\n{details}\n\n"
            f"CANDIDATE_CROSSREFS:\n{json.dumps(candidates, ensure_ascii=False)}"
        )
        try:
            resp = self.llm.json_call(SUFFICIENCY_SYSTEM, user)
        except Exception:
            return None
        if resp.get("status") == "insufficient":
            return self.tree.get(resp.get("fetch_next"))
        return None

    # ── output envelope (spec §6 / §8) ─────────────────────────────
    def _envelope(self, *, answer, nodes, mode, breadcrumb, drill_down, related,
                  guide_links, trace, t0, faq_info: Optional[dict] = None,
                  sources_fired: Optional[list] = None, overlap: bool = False) -> dict:
        info = faq_info or {
            "faq_top_score": None, "faq_included": False,
            "faq_id": None, "faq_candidates": [],
        }
        return {
            "answer": answer,
            "nodes": nodes,
            "mode": mode,
            "breadcrumb": breadcrumb,
            "drill_down": drill_down,
            "related": related,
            "guide_links": guide_links,
            "trace": trace,
            "latency_ms": _ms(t0),
            "llm_calls": self.llm.total_calls,
            # ── FAQ instrumentation (spec §8) ──
            "faq_backend": (self.faq.name if self.faq is not None else None),
            "faq_top_score": info["faq_top_score"],
            "faq_included": info["faq_included"],
            "faq_id": info["faq_id"],
            "faq_candidates": info["faq_candidates"],
            "sources_fired": sources_fired if sources_fired is not None else (
                ["user_guide"] if nodes else []
            ),
            "overlap": overlap,
        }
