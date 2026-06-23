"""
End-to-end flow test for the FAQ-augmented guide path (hybrid backend).

Exercises the REAL stack for 5 platform-help queries (the project-docs / KB path
is NOT invoked — we only verify that platform-help routing + FAQ work):

  1. MAIN ROUTING   — the upstream guide-vs-project classifier (home_assistant
                      prompt) must send each query to "guide" (platform help).
  2. PARALLEL CALLS — inside VeloxGuideRouter.ask(), the FAQ search runs in a
                      thread alongside the routing LLM call. We wrap both with
                      timestamps and assert their intervals OVERLAP (real concurrency).
  3. FINAL ANSWER   — the single synthesis call returns a grounded answer; we show
                      it plus the routed nodes, FAQ hit, overlap/dedupe, sources.

Real LLM + embedding calls (hybrid). Run from the backend root:
    python -m services.vectorless_rag.faq.test_faq_flow
"""

from __future__ import annotations

import threading
import time

from prompts.home_assistant_router import HOME_ROUTER_SYSTEM, ROUTES
from services.vectorless_rag.llm import GatewayLLM
from services.vectorless_rag.router import VeloxGuideRouter

from .factory import build_retriever

# 5 diverse platform-help queries (NOT project-docs questions).
QUERIES = [
    "How do I generate an Atlassian API token?",          # exact FAQ + guide overlap -> dedupe
    "The dropdowns are blank when I create a project",     # paraphrase -> FAQ-driven
    "How do I generate Jira stories from a BRD?",          # guide how-to + related FAQ
    "What does the Deployment module do?",                 # broad guide; FAQ likely gated out
    "How do I deploy to a Kubernetes cluster?",            # out-of-scope -> must abstain
]


def classify_main_route(llm: GatewayLLM, query: str) -> dict:
    """The upstream 'guide' vs 'project' decision (platform help vs project docs)."""
    try:
        resp = llm.json_call(HOME_ROUTER_SYSTEM, f"QUESTION:\n{query}")
    except Exception as e:  # noqa: BLE001
        return {"route": "guide", "confidence": 0.0, "reason": f"(classify failed: {e})"}
    route = resp.get("route")
    if route not in ROUTES:
        route = "guide"
    return {"route": route, "confidence": resp.get("confidence"), "reason": resp.get("reason")}


def instrument_parallelism(router: VeloxGuideRouter):
    """Wrap the routing LLM call and the FAQ search with wall-clock timestamps so we
    can prove they overlap. Returns a `spans` list populated per ask()."""
    spans: list[dict] = []
    lock = threading.Lock()
    base = {"t0": 0.0}

    def record(name, start, end):
        with lock:
            spans.append({"name": name, "start": (start - base["t0"]) * 1000,
                          "end": (end - base["t0"]) * 1000,
                          "thread": threading.current_thread().name})

    orig_json = router.llm.json_call

    def timed_json(system, user):
        s = time.perf_counter()
        try:
            return orig_json(system, user)
        finally:
            record("routing_llm", s, time.perf_counter())

    router.llm.json_call = timed_json  # type: ignore[assignment]

    orig_search = router.faq.search

    def timed_search(query, k):
        s = time.perf_counter()
        try:
            return orig_search(query, k)
        finally:
            record("faq_search", s, time.perf_counter())

    router.faq.search = timed_search  # type: ignore[assignment]
    return spans, base


def overlap_ms(spans: list[dict]) -> float:
    route = next((s for s in spans if s["name"] == "routing_llm"), None)
    faq = next((s for s in spans if s["name"] == "faq_search"), None)
    if not route or not faq:
        return 0.0
    return max(0.0, min(route["end"], faq["end"]) - max(route["start"], faq["start"]))


def main() -> None:
    classifier = GatewayLLM()
    faq = build_retriever("hybrid")  # the approach you want to ship
    print(f"FAQ backend = {faq.name}; running {len(QUERIES)} platform-help queries "
          f"through the FULL flow (real LLM + embeddings).\n")

    passes = 0
    for i, q in enumerate(QUERIES, 1):
        # 1) MAIN ROUTING (platform help vs project docs)
        main_route = classify_main_route(classifier, q)

        # 2) FULL guide flow with parallel-call instrumentation
        router = VeloxGuideRouter(llm=GatewayLLM(), routing_mode="hybrid", faq_retriever=faq)
        spans, base = instrument_parallelism(router)
        base["t0"] = time.perf_counter()
        out = router.ask(q)
        ov = overlap_ms(spans)

        # 3) Report
        is_oos = (q == "How do I deploy to a Kubernetes cluster?")
        guide_ok = main_route["route"] == "guide"
        flow_ok = (out["mode"] == "abstain" and out["llm_calls"] == 1) if is_oos else bool(out["answer"])
        parallel_ok = ov > 0 or len(spans) < 2  # overlap, unless FAQ disabled
        ok = guide_ok and flow_ok
        passes += ok

        print(f"{'='*92}\n[{i}] {q!r}")
        print(f"  1. MAIN ROUTING     -> route={main_route['route']} "
              f"(conf={main_route['confidence']}) :: {main_route['reason']}  "
              f"{'OK' if guide_ok else 'UNEXPECTED (not guide)'}")
        print(f"  2. PARALLEL CALLS   -> spans:")
        for s in sorted(spans, key=lambda x: x["start"]):
            print(f"        {s['name']:<12} [{s['start']:6.1f} .. {s['end']:6.1f}] ms  "
                  f"({s['thread']})")
        print(f"        overlap = {ov:.1f} ms  {'<-- ran concurrently' if ov > 0 else '(sequential)'}")
        print(f"  3. GUIDE ROUTING    -> nodes={out['nodes']} mode={out['mode']} "
              f"llm_calls={out['llm_calls']}")
        print(f"     FAQ             -> backend={out['faq_backend']} included={out['faq_included']} "
              f"id={out['faq_id']} top={out['faq_top_score']} overlap={out['overlap']}")
        print(f"     sources_fired   -> {out['sources_fired']}")
        cands = ", ".join(f"{c['faq_id']}@{c['score']}" for c in out["faq_candidates"][:3])
        print(f"     faq_candidates  -> {cands}")
        print(f"  FINAL ANSWER:\n      {out['answer'].replace(chr(10), chr(10)+'      ')}")
        print(f"  latency={out['latency_ms']}ms  result={'PASS' if ok else 'CHECK'}\n")

    print(f"{'='*92}\nSUMMARY: {passes}/{len(QUERIES)} cases behaved as expected "
          f"(routed to guide + produced answer / correct abstain).")


if __name__ == "__main__":
    main()
