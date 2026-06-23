"""
FAQ A/B harness (spec §9) -- the point of the pluggable design.

Runs the §9 eval set through EACH backend (bm25 / embeddings / hybrid) and reports,
side by side and by query type:
  - recall@1, recall@2        (answerable cases: an expected id in top-1 / top-2)
  - false-injection rate      (out-of-scope cases whose top score clears FAQ_THRESHOLD)
  - abstention-preserved      (out-of-scope cases correctly NOT injected)
  - latency ms                (per search call)

Leads with the HELD-OUT slice (rows the threshold/weights were not tuned on).

Query embeddings are cached to faq_eval_query_cache.json (key sha256(model+query))
so repeated tuning runs are fast + deterministic and latency stays comparable.
Embeddings/hybrid are skipped (with a printed note) if the embedding service can't
be reached -- bm25 always runs.

    python -m services.vectorless_rag.faq.faq_eval
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from pathlib import Path

from .factory import build_retriever
from .faq_eval_cases import CASES
from .store import load_faq

QUERY_CACHE_PATH = Path(__file__).parent / "faq_eval_query_cache.json"

try:
    from environment import FAQ_THRESHOLD, FAQ_TOP_K  # type: ignore
except Exception:
    import os as _os

    FAQ_THRESHOLD = float(_os.getenv("FAQ_THRESHOLD", "0.5"))
    FAQ_TOP_K = int(_os.getenv("FAQ_TOP_K", "3"))


# -- query-embedding cache (makes repeated A/B runs fast + deterministic) -------
def install_query_cache() -> "tuple[callable, dict] | None":
    """Monkeypatch embedding_service.generate_embedding with a disk-cached version.
    Returns (save_fn, cache) or None if the embedding service is unavailable."""
    try:
        from services import embedding_service as es_mod
    except Exception as e:  # noqa: BLE001
        print(f"  (embedding service unavailable: {e})")
        return None

    svc = es_mod.embedding_service
    model = svc.embedding_model_id
    cache: dict = {}
    if QUERY_CACHE_PATH.exists():
        try:
            cache = json.loads(QUERY_CACHE_PATH.read_text())
        except Exception:
            cache = {}

    orig = svc.generate_embedding

    def cached(text: str):
        key = hashlib.sha256(f"{model}\n{text}".encode("utf-8")).hexdigest()
        if key in cache:
            return cache[key]
        vec = orig(text)
        cache[key] = list(vec)
        return vec

    svc.generate_embedding = cached  # type: ignore[assignment]

    def save() -> None:
        QUERY_CACHE_PATH.write_text(json.dumps(cache))

    return save, cache


def _run_backend(name: str, entries) -> list[dict]:
    """Return per-case rows for one backend."""
    retriever = build_retriever(name, entries=entries)
    rows = []
    for c in CASES:
        t0 = time.perf_counter()
        hits = retriever.search(c["query"], FAQ_TOP_K)
        ms = (time.perf_counter() - t0) * 1000
        ids = [e.faq_id for e, _ in hits]
        scores = [s for _, s in hits]
        top_score = scores[0] if scores else 0.0
        rows.append({"case": c, "ids": ids, "top_score": top_score, "ms": ms})
    return rows


def _summarize(rows: list[dict], subset) -> dict:
    """Aggregate metrics over the rows whose case passes `subset(case)`."""
    ans = [r for r in rows if subset(r["case"]) and r["case"]["expected"]]
    oos = [r for r in rows if subset(r["case"]) and not r["case"]["expected"]]

    def recall_at(rows_, n):
        if not rows_:
            return None
        hit = sum(bool(set(r["ids"][:n]) & set(r["case"]["expected"])) for r in rows_)
        return hit / len(rows_)

    injected_oos = sum(r["top_score"] > FAQ_THRESHOLD for r in oos)
    return {
        "n_ans": len(ans),
        "n_oos": len(oos),
        "recall@1": recall_at(ans, 1),
        "recall@2": recall_at(ans, 2),
        "false_injection": (injected_oos / len(oos)) if oos else None,
        "abstention_preserved": ((len(oos) - injected_oos) / len(oos)) if oos else None,
        "latency_ms": statistics.mean(r["ms"] for r in rows) if rows else 0.0,
    }


def _fmt(x) -> str:
    if x is None:
        return "   . "
    if isinstance(x, float):
        return f"{x:>4.2f}"
    return str(x)


def _table(title: str, results: dict[str, dict]) -> None:
    backends = list(results.keys())
    print(f"\n{title}")
    print("-" * (22 + 12 * len(backends)))
    print(f"{'metric':<22}" + "".join(f"{b:>12}" for b in backends))
    for metric in ("recall@1", "recall@2", "false_injection", "abstention_preserved", "latency_ms"):
        cells = "".join(f"{_fmt(results[b][metric]):>12}" for b in backends)
        print(f"{metric:<22}{cells}")


def main() -> None:
    entries = load_faq()
    print(f"FAQ A/B eval -- {len(entries)} entries, {len(CASES)} cases, "
          f"FAQ_THRESHOLD={FAQ_THRESHOLD}, top_k={FAQ_TOP_K}")

    saver = install_query_cache()
    backends = ["bm25"]
    # Probe the embedding path once; only add embeddings/hybrid if it works.
    try:
        from services.embedding_service import embedding_service
        embedding_service.generate_embedding("probe")
        backends += ["embeddings", "hybrid"]
    except Exception as e:  # noqa: BLE001
        print(f"  NOTE: embeddings/hybrid skipped -- embedding service unreachable ({e})")

    rows_by_backend = {b: _run_backend(b, entries) for b in backends}
    if saver:
        saver[0]()

    types = ["exact-term", "paraphrase", "overlapping", "out-of-scope"]

    # Lead with the held-out slice (spec §9).
    held = {b: _summarize(rows_by_backend[b], lambda c: c["holdout"]) for b in backends}
    _table("HELD-OUT SLICE (not tuned on) -- lead with this", held)

    overall = {b: _summarize(rows_by_backend[b], lambda c: True) for b in backends}
    _table("OVERALL", overall)

    for t in types:
        res = {b: _summarize(rows_by_backend[b], lambda c, t=t: c["type"] == t) for b in backends}
        _table(f"BY TYPE: {t}", res)

    # Per-case detail for the winner-picking eyeball + threshold calibration.
    print("\nPER-CASE (top id @ score) - for threshold calibration")
    print("-" * 100)
    for i, c in enumerate(CASES):
        flag = "H" if c["holdout"] else " "
        cells = []
        for b in backends:
            r = rows_by_backend[b][i]
            top = f"{r['ids'][0] if r['ids'] else '-'}@{r['top_score']:.2f}"
            cells.append(f"{b[:4]}:{top}")
        exp = ",".join(c["expected"]) or "(none)"
        print(f"[{flag}] {c['type']:<12} {c['query'][:42]!r:<44} exp={exp}")
        print(f"      " + "  ".join(cells))

    print("\nReading it: bm25 should win exact-term; embeddings/hybrid should win "
          "paraphrase. If bm25 wins everywhere, drop the embeddings dependency. "
          "Calibrate FAQ_THRESHOLD so false_injection->0 without sinking recall.")


if __name__ == "__main__":
    main()
