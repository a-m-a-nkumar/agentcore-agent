# FAQ Retrieval — Learnings & Design Notes

A readable companion to the code in this folder. It explains the **architecture we
chose, the alternatives we rejected, and *why*** — so the reasoning survives even if
the code changes. Read top-to-bottom once; skim the headings later.

---

## 0. The one-paragraph mental model

A user question flows through **two routing layers**. First an upstream **classifier**
decides *which knowledge source* should answer (the product **user-guide** vs the
user's **project docs**). If it's a guide question, a second **retrieval router**
decides *which guide section* answers it — and, **in parallel**, an **FAQ retriever**
looks for a matching canned Q&A. Both feed **one** answer-writing (synthesis) LLM call.
The FAQ is an **additive second source**, gated by a confidence threshold, that never
breaks the guide path and adds **zero** LLM calls in the default backend.

```
question
  │
  ▼  LAYER 1 — classifier (1 cheap LLM call): "guide" vs "project"
  │        └─ project → project-docs RAG        [separate, untouched]
  ▼  guide
  ├──────────────┐  (run concurrently)
  ▼              ▼
guide router   FAQ retriever  ── pluggable: bm25 | embeddings | hybrid
(node pick)    (top-k + score)
  └──────┬───────┘
         ▼  assemble prompt in pure code (threshold gate + dedupe)
   ONE synthesis LLM call  → grounded answer
```

---

## 1. Routing — the ideas worth keeping

### 1a. Two layers, two different jobs
- **Classifier (intent):** "is this about the product, or the user's own data?" It is
  **permissive** — when unsure it leans to `guide` (which can safely abstain).
- **Retrieval router (location):** "which section actually answers this?" It is the
  **strict scope gate** — it returns *nothing* (abstains) when the guide genuinely
  doesn't cover the topic.

**Why split them?** Mixing "is it in scope?" with "which doc?" is the classic router
bug. We saw it live: *"how do I deploy to a Kubernetes cluster?"* → the classifier sends
it to `guide` (it *looks* like a deploy task), but the guide router correctly **abstains**
because no section covers Kubernetes. **Permissive classifier + strict retriever** gives
you both high recall (don't wrongly bounce in-scope questions) and honest abstention.

### 1b. Hybrid routing = LLM intent + BM25 keyword
The guide router blends an LLM's one-shot pick (good at *intent/paraphrase*) with BM25
(good at *exact technical terms* — "MCP", "PAT", "Sync Docs"). BM25 only breaks ties
among sibling leaves and rescues clear keyword winners; it never overrides the LLM on
broad/overview picks where it has no intent signal. **Lesson:** keyword search and
semantic models fail in *different* places — combine them where each is strong, not
blindly.

---

## 2. The pluggable retriever — Strategy pattern + let-the-numbers-decide

We didn't *guess* whether keyword or semantic search is better for the FAQ. We built
**three interchangeable backends behind one interface** and let an eval pick the winner.

```python
class FaqRetriever(ABC):
    def index(self, entries) -> None: ...
    def search(self, query, k) -> list[(entry, score)]: ...   # score in [0,1], desc
```

One config value (`FAQ_BACKEND`) selects the active one; routing/synthesis code never
changes. This is the **Strategy pattern**, and it's the single most reusable idea here:
*when you have several plausible algorithms and no proof which is best, hide them behind
one interface and A/B them.*

| Backend | How | Wins at | Loses at | Cost |
|---|---|---|---|---|
| **bm25** | keyword TF-IDF | exact terms, jargon | paraphrase ("blank" vs "empty") | ~0 ms, no deps |
| **embeddings** | dense cosine | paraphrase / synonyms | exact rare tokens, wrong-qualifier siblings | ~400 ms/query |
| **hybrid** | weighted sum of both | both (best recall) | — | ~5 ms (cached) |

**Our measured result** (53 FAQs, real Titan-v2): bm25 recall@1 0.94 (paraphrase 0.86,
0.07 ms); embeddings/hybrid 1.00 incl. paraphrase. Hybrid is the sweet spot *if* you
pre-cache embeddings. **Decision rule we wrote down:** if bm25 ever wins everywhere,
delete the embedding dependency — don't carry complexity the data doesn't justify.

---

## 3. Score normalization — the subtle, important part

You can't threshold ("inject the FAQ only if score > X") if each backend's raw scores
live on a different scale. BM25 is unbounded `[0, ∞)`; cosine is `[-1, 1]`. So **every
backend maps its score into [0,1]** before returning.

**The trap we avoided:** *min-max over the candidate set* pegs the top hit to **1.0 for
every query** — including an out-of-scope one whose best-of-k is garbage. That destroys
the gate. Instead we use **absolute** maps that preserve "how good is this *really*":
- embeddings → raw cosine, clamped to [0,1] (already calibrated).
- bm25 → **saturating squash** `s/(s+half)`: 0→0, half→0.5, ∞→1. Monotonic (ranking
  preserved) **and** absolute (gate can reject weak tops).

**Consequence (a real finding):** Titan cosine scores cluster **lower** than BM25 — many
*correct* embedding hits sit at 0.4–0.5 while out-of-scope sits at 0.06–0.19. So the
right `FAQ_THRESHOLD` is **~0.25 for embeddings vs ~0.5 for bm25**. Same knob, **tuned
per backend**. The lesson: normalization makes thresholds *comparable in meaning*, not
*identical in value* — you still calibrate per scorer on real data.

### 3a. Tiny-corpus gotcha: stopwords
On ~49 docs, common words ("how do I to a") still carry small IDF and leak BM25 mass
into out-of-scope queries (Kubernetes scored 0.52 → would falsely inject). A light
**stopword filter** drops them, so an OOS query shares **no content tokens → score 0**.
**Lesson:** statistics that are stable on large corpora (IDF) are noisy on small ones —
compensate explicitly.

---

## 4. FAQ storage — why a flat file beats a database here

```
faq.json              ← the corpus (flat array, dev-authored, version-controlled)
faq_embeddings.json   ← precomputed embedding sidecar (generated artifact)
```

**Decision: store the FAQ as a flat JSON file, not a DB table, not pgvector.**

| Option | Verdict | Why |
|---|---|---|
| `document_embeddings` (existing pgvector) | ❌ | That table is **per-project, per-user** synced content keyed by `project_id`. FAQ is **global, static, dev-authored** with no project — mixing pollutes it and complicates its queries. |
| New pgvector table | ❌ | 53 vectors fit in memory; brute-force cosine is sub-ms. A DB round-trip (~1–10 ms) buys nothing and adds ops surface. |
| **Flat JSON + in-memory** | ✅ | Small, slow-changing, edited in commits. Loads once at startup. |

**Storage is decoupled from retrieval on purpose:** every backend reads the same
in-memory `list[FaqEntry]`. So the migration path is painless — *if* the FAQ ever grows
to thousands of entries or needs non-dev live editing, move records to a table and
nothing downstream changes. **Match the storage to the data's size and change-rate**, not
to what sounds "production-grade."

### 4a. The embedding sidecar — cache expensive *deterministic* work
Embedding 53 entries on every process start is wasteful (and costs gateway calls). So we
**precompute once** into `faq_embeddings.json`, each vector stamped with:
- `model` — guards against mixing 1024-dim (VDI) and 1536-dim (local) vectors.
- `text_hash` (sha256 of the indexed text) — **content-addressed invalidation**: on
  load we re-embed *only* entries whose text or model changed.

So per-process `index()` is a disk load, not N network calls. **General principle:**
*cache deterministic, expensive computations keyed by a hash of their inputs* — embeddings
are a textbook case.

### 4b. Query-embedding cache (eval only)
The A/B harness runs the **same** query set across all three backends repeatedly while
tuning the threshold. We cache query vectors keyed by `sha256(model+query)` so reruns are
fast and **deterministic**, and latency stays comparable. **Lesson:** make experiments
cheap and reproducible or you won't run them enough.

---

## 5. Integration architecture — fold in, don't bolt on

### 5a. One synthesis call, not two
The FAQ block is appended to the **existing** answer-writing prompt. We did **not** add a
second LLM call or a separate "FAQ answerer." **Why:** the synthesis call already exists
and the model is happy to weave one more grounded source into the prose. **Cost discipline:
reuse the call you're already paying for** before adding a new one.

### 5b. The one allowed exception — abstain-rescue
If the guide finds nothing **but** an FAQ clears the threshold, we *do* run synthesis on
the FAQ alone (`mode=faq_only`). This adds exactly **one** Sonnet call, and **only** in
that case. It's the difference between *"I couldn't find anything"* and an actual answer
(e.g. "what browsers are supported?"). **Lesson:** make the expensive exception explicit,
narrow, and measurable — not the default.

### 5c. Deduplication (the main risk)
Some FAQs duplicate a guide section (Atlassian token). Two-part mitigation:
1. **Code signal:** if the top FAQ's `guide_ref` equals the picked guide node_id →
   `overlap=True`.
2. **Prompt instruction:** when overlapping, produce **one** answer, prefer the FAQ's
   concise phrasing, emit the guide link **once**, never repeat steps.
The **grounding contract** is untouched: the model may use only supplied content/links,
never invent. **Lesson:** combine a cheap deterministic signal (code) with a soft one
(prompt) — don't rely on the LLM alone to notice duplication.

### 5d. Parallelism without async
The stack is synchronous. The FAQ search runs in a `ThreadPoolExecutor` **alongside** the
routing LLM call, so the embedding network call (~1.2 s) overlaps routing (~2–3 s) and
adds ~0 wall-clock. The retriever is built **once** at startup and shared read-only across
requests (not rebuilt per call). **Lesson:** I/O-bound work overlaps fine with threads
even in sync code; build heavy indices once, not per request.

### 5e. Graceful degradation
The FAQ is **additive, never load-bearing**: `_faq_search` swallows all errors → `[]`;
a failed retriever build → FAQ disabled, guide path unaffected; `FAQ_ENABLED=false`
turns it off entirely. **Lesson:** a *secondary* feature must never be able to take down
the *primary* path.

### 5f. Observability is part of the feature
Every response carries `faq_backend`, `faq_top_score`, `faq_included`, `faq_id`,
`faq_candidates`, `sources_fired`, `overlap`, plus trace markers. **You cannot tune a
threshold you can't see.** Instrumentation isn't optional polish — it's what makes the
A/B and the calibration possible.

---

## 6. Eval discipline — how we let data decide

- **Group cases by type** (exact-term / paraphrase / overlapping / out-of-scope) so you
  see *where* a backend wins, not just an average. The winner can be type-dependent.
- **Metrics that match the goal:** recall@1/@2 (did we surface the right FAQ?),
  **false-injection rate** (did we add a FAQ when we shouldn't?), **abstention preserved**
  (did out-of-scope stay out?), latency.
- **Held-out slice:** a hand-authored eval can overfit. We mark rows the threshold was
  *not* tuned on and **lead with that number** — the honest one.
- **Tune the gate to the curve:** pick `FAQ_THRESHOLD` so false-injection → 0 without
  sinking recall; the per-case score dump is the calibration data.

---

## 7. Operational lessons (the ones that bite in real life)

- **A running server holds the OLD code until restarted.** We shipped correct code but
  the frontend kept abstaining because its backend (port 8000) was a process started days
  earlier, before the feature existed, launched without `--reload`. *Code on disk ≠ code
  in the running process.* For dev, run uvicorn with `--reload`; after any backend change,
  **restart**.
- **Don't add a dependency the environment lacks.** numpy wasn't installed, so the
  embeddings backend is **pure-Python** (cosine over ~49 vectors is sub-ms anyway). This
  also kept the whole feature **zero-new-dependency**, matching the existing BM25 ethos.
- **One config switch, defaults safe.** `FAQ_BACKEND` defaults to `bm25` (zero cost, no
  network); embeddings/hybrid are opt-in. Ship the cheap, safe default; make the powerful
  path explicit.

---

## 8. The transferable principles (take these to other problems)

1. **Strategy + A/B:** competing algorithms → one interface, one config switch, an eval
   that picks the winner with evidence.
2. **Normalize before you threshold** across heterogeneous scorers — and prefer *absolute*
   normalization when the threshold must judge confidence, not rank.
3. **Calibrate per scorer on real data** — a comparable knob is not an identical value.
4. **Cache deterministic expensive work by a hash of its inputs** (embeddings → sidecar).
5. **Match storage to data shape** (size × change-rate), not to fashion; decouple storage
   from retrieval so migration is cheap.
6. **Reuse the call you're already paying for** before adding a new LLM call; make
   exceptions narrow and measurable.
7. **Secondary features degrade gracefully** — additive, error-swallowing, switchable.
8. **You can't tune what you can't see** — instrument first.
9. **Permissive classifier + strict retriever** beats one over-eager router.
10. **Restart the process** — disk edits are not live until reload.

---

### File map (where each idea lives)
| File | Responsibility |
|---|---|
| `store.py` | corpus model + `index_text`/`text_hash` |
| `retriever.py` | `FaqRetriever` interface + absolute normalization helpers |
| `backend_bm25.py` | keyword backend (+ stopwords, saturating squash) |
| `backend_embeddings.py` + `build_embeddings.py` | dense backend + sidecar cache |
| `backend_hybrid.py` | weighted-sum fusion |
| `factory.py` | the `FAQ_BACKEND` switch |
| `prompts_faq.py` | RELATED_FAQ block + dedupe addendum |
| `faq_eval.py` + `faq_eval_cases.py` | the A/B harness |
| `test_faq_flow.py` | end-to-end flow + parallelism proof |
| `../router.py` | wiring: parallel search, gate, rescue, dedupe, instrumentation |
