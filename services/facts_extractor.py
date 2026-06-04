"""Surface-fact extractor for BRD generation (RAG-path only).

Why this exists: the per-section RAG audit showed embedding-similarity
retrieval undercounts SURFACE facts — specific dates, sprint IDs, severity
counts, percentages, named people/orgs, and relational status ("Pablo
incomplete", "S4 moved July→Aug") — because those tokens contribute little
to semantic similarity against generic section prompts. RAG handles
narrative reasoning well; this module fills its blind spot with a
deterministic regex pass (+ optional spaCy NER) routed to gap-heavy sections.

Two layers, used together:

1. **Regex pass** (always-on, free, ~1-2s for 60K corpus):
   dates, sprint IDs, severity counts, percentages, metrics, vendor names,
   person+role pairs, **relational-fact patterns** ("X moved to Y", "blocked
   by", "depends on"), and **status-keyword patterns** ("at risk", "down",
   "delayed"). Catches ~80% of audit-flagged gaps with zero deps.

2. **spaCy NER** (opt-in via BRD_USE_FACT_EXTRACTION=true):
   en_core_web_sm (~13 MB model + ~50 MB spaCy package). Picks up
   PERSON / ORG / DATE entities that lack regex-stable surface forms
   (e.g., a person mentioned without their role token immediately adjacent).
   Defensive: silently no-ops if spacy or the model isn't importable —
   regex layer still produces results, the Lambda never breaks.

We chose spaCy over GLiNER for Lambda compatibility: GLiNER (any version)
requires torch or onnxruntime + tokenizers + a 150 MB+ ONNX model and
busts Lambda's 250 MB unzipped zip limit. spaCy is the largest NER option
that comfortably fits in a Lambda zip while still being useful. Wave 2
revisits ML packaging if regex+spaCy uplift turns out to be insufficient.

Cost shape:
  - $0 LLM cost (no API calls)
  - Sub-threshold inputs: extractor never invoked (gated at caller)
  - Regex-only: ~1-2s per 60K corpus
  - With spaCy: +3-5s per 60K corpus, +~1s one-time model load per container
  - Runs in parallel with embedding indexing → user-visible delay 0 / max-only
"""

from __future__ import annotations

import logging
import os
import re
import tarfile
import threading
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# The ENTIRE spaCy runtime (spacy + thinc + blis + en_core_web_sm + all the
# transitive deps spaCy needs that we don't otherwise use) lives in a single
# S3 tarball. Bundling the stack costs ~140 MB which would consume most of the
# Lambda 250 MB zip budget. Lazy-loading it costs ~10s on the first ≥30k call
# per warm container; sub-30k flows pay nothing.
#
# Tarball is built by .scratch/build_spacy_runtime.py and uploaded by hand
# (see .scratch/SPACY_RUNTIME_SETUP.md). The bucket + key are env-tunable so
# a new model version can be rolled by updating the key without redeploying.
SPACY_RUNTIME_S3_BUCKET = os.getenv("SPACY_RUNTIME_S3_BUCKET", "sdlc-orch-dev-us-east-1-app-data")
# Bucket requires SSE-KMS on every PutObject; the Lambda's IAM role can both
# read and write (the upload itself is human-triggered with SSE-KMS from a
# privileged role — see .scratch/SPACY_RUNTIME_SETUP.md).
# v5: ship the full pip install — no more stripping of "probably unused"
# deps. v1-v4 each broke on a different missing transitive (typer, click,
# spacy.lang.xx, …). The tarball lives in S3 and downloads into /tmp where
# we have 1024 MB ephemeral — there's no real size pressure. The /tmp
# extracted form is now ~220 MB; cold-start S3 download is ~75 MB.
SPACY_RUNTIME_S3_KEY    = os.getenv("SPACY_RUNTIME_S3_KEY", "models/spacy_runtime_v5.tar.gz")
# Use a distinct local dir per version so warm containers from a previous
# tarball don't reuse a stale layout.
SPACY_RUNTIME_LOCAL_DIR = "/tmp/spacy_runtime_v5"
SPACY_RUNTIME_LOCAL_TGZ = "/tmp/spacy_runtime_v5.tar.gz"
# Inside the tarball, the model lives at en_core_web_sm/en_core_web_sm-3.8.0/
# (the standard layout when pip-installing the model wheel).
SPACY_MODEL_PATH_IN_RUNTIME = "en_core_web_sm/en_core_web_sm-3.8.0"

logger.info(
    f"[facts_extractor] module loaded — bucket={SPACY_RUNTIME_S3_BUCKET} "
    f"key={SPACY_RUNTIME_S3_KEY} local_dir={SPACY_RUNTIME_LOCAL_DIR}"
)


# ────────────────────────────────────────────────────────────────────────────
# Fact categories produced by this module.
# Section routing in route_facts_to_sections() is keyed on these constants.
# ────────────────────────────────────────────────────────────────────────────
CAT_DATE             = "date"
CAT_SPRINT           = "sprint"
CAT_SEVERITY         = "severity"
CAT_PERCENTAGE       = "percentage"
CAT_METRIC           = "metric"
CAT_VENDOR           = "vendor"
CAT_PERSON_ROLE      = "person_role"
CAT_PERSON           = "person"            # spaCy PERSON (no role context)
CAT_ORG              = "org"               # spaCy ORG
CAT_RELATIONAL       = "relational"        # "X moved to Y", "X incomplete"
CAT_STATUS_RISK      = "status_risk"       # "X is down", "blocked", "delayed"


# ────────────────────────────────────────────────────────────────────────────
# Regex patterns.
# ────────────────────────────────────────────────────────────────────────────

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|January|February|March|April|June|July|August|September|October|November|December)"
_DATE_PATTERNS = [
    rf"\b{_MONTH}\s+\d{{1,2}}(?:[–\-]\d{{1,2}})?(?:,?\s*\d{{4}})?\b",
    r"\b\d{4}-\d{2}-\d{2}\b",
    r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[–\-]\d{1,2}/\d{1,2}(?:/\d{2,4})?)?\b",
    r"\bQ[1-4](?:\s*\d{4})?\b",
    r"\b(?:EOQ|EOY|EOM)\b",
    r"\bby\s+(?:EOQ|EOY|EOM|Q[1-4])\b",
]
_DATE_RE = re.compile("|".join(_DATE_PATTERNS), re.IGNORECASE)

_SPRINT_RE = re.compile(r"\b(?:Sprint\s*#?\s*\d+|S\d+R\d+)\b", re.IGNORECASE)
_SEVERITY_RE = re.compile(r"\b(?:Sev|Severity)\s*[1-4]\s*[:=]?\s*\d+\b", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%")
_METRIC_RE = re.compile(
    r"\b\d{1,4}\s+(?:bugs|defects|issues|tickets|PRs|pull\s+requests|tests|test\s+cases|incidents|stories|hrs?|hours|minutes|days|weeks|months)\b",
    re.IGNORECASE,
)

_BUILTIN_VENDORS = [
    r"AWS", r"Azure", r"GCP", r"S3", r"EC2", r"Lambda",
    r"JIRA", r"Jira", r"Zephyr", r"Confluence",
    r"SendGrid", r"Send\s*Grid", r"log4j", r"Splunk", r"Datadog",
    r"Hitachi", r"TCS", r"GFL", r"D24/7", r"IronPay", r"Quorum",
    r"ConsenSys", r"JPMC", r"JPMorgan", r"Citi", r"Fiserv", r"Alogent",
    r"Bedrock", r"Anthropic", r"OpenAI", r"Claude",
    r"PowerBI", r"Power\s*BI", r"Kinesis", r"DynamoDB", r"RDS",
]
_VENDOR_RE = re.compile(r"\b(?:" + "|".join(_BUILTIN_VENDORS) + r")\b")

_ROLE_KEYWORDS = (
    r"VP|GM|CTO|CEO|CIO|CFO|Director|Manager|Engineer|Architect|Lead|"
    r"Owner|Sr|Senior|Junior|Principal|Staff|Head|Chief|Analyst|"
    r"Developer|Designer|Scrum\s+Master|Product\s+Manager|Tech\s+Lead"
)
_PERSON_ROLE_RE = re.compile(
    rf"\b([A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+)\s*[,\-(]?\s*"
    rf"((?:{_ROLE_KEYWORDS})\b[^,;.\n]{{0,80}})",
)

# Relational facts — status assertions, transitions, ownership.
# Each pattern captures (subject, qualifier) implicitly via the full match;
# the surrounding-snippet window gives the model enough context to interpret.
_RELATIONAL_PATTERNS = [
    # "X moved from Y to Z" / "X moved to Z"
    r"\b[\w\s\.\-]{2,40}?\bmoved\s+(?:from\s+[\w\s\d\.\-/]{1,30}\s+)?to\s+[\w\s\d\.\-/]{1,30}\b",
    # "X complete/incomplete" — author shorthand for status assertions
    r"\b[A-Z]\w+\s+(?:complete|incomplete)\b",
    # "X owns Y" / "X owned by Y"
    r"\b[\w\s\.\-]{2,30}?\bown(?:s|ed\s+by)\s+[\w\s\.\-]{1,30}\b",
    # "blocked by", "blocked on"
    r"\bblocked\s+(?:by|on)\s+[\w\s\.\-]{1,40}\b",
    # "depends on"
    r"\bdepends\s+on\s+[\w\s\.\-]{1,40}\b",
    # "deferred to", "deferred until"
    r"\bdeferred\s+(?:to|until)\s+[\w\s\d\.\-/]{1,30}\b",
]
_RELATIONAL_RE = re.compile("|".join(_RELATIONAL_PATTERNS), re.IGNORECASE)

# Status / risk keywords — surrounding sentence becomes the fact.
_STATUS_RISK_PATTERNS = [
    r"\b(?:at\s+risk|critical\s+delay|delays?\s+on|will\s+not\s+go\s+live|"
    r"highly\s+unlikely|unable\s+to|cannot|down|outage|blocker|bottleneck|"
    r"behind\s+schedule|slipping|on\s+hold|paused|deprecated)\b",
]
_STATUS_RISK_RE = re.compile("|".join(_STATUS_RISK_PATTERNS), re.IGNORECASE)


# ────────────────────────────────────────────────────────────────────────────
# Section routing — matches the plan's section→categories table.
# Sections 2, 3, 5, 9 receive no ledger injection (narrative-heavy → RAG).
# ────────────────────────────────────────────────────────────────────────────
_CATEGORY_TO_SECTIONS: Dict[str, List[int]] = {
    CAT_DATE:        [1, 13, 14],          # doc dates, timeline, risk deadlines
    CAT_SPRINT:      [7, 13],              # functional cadence, timeline
    CAT_SEVERITY:    [12, 14],             # KPIs, risks
    CAT_PERCENTAGE:  [6, 8, 12],           # ROI, NFR, KPIs
    CAT_METRIC:      [6, 8, 12],           # ROI, NFR, KPIs
    CAT_VENDOR:      [4, 10, 14, 16],      # stakeholders, assumptions, risks, glossary
    CAT_PERSON_ROLE: [4, 15],              # stakeholders, approval
    CAT_PERSON:      [4, 15],              # GLiNER-derived
    CAT_ORG:         [4, 10, 14, 16],      # GLiNER-derived
    CAT_RELATIONAL:  [6, 7, 10, 11, 13, 14],  # ROI, FR, assumptions, constraints, timeline, risks
    CAT_STATUS_RISK: [14],                 # risks only
}

_MAX_FACTS_PER_SECTION = 30


# ────────────────────────────────────────────────────────────────────────────
# Public API.
# ────────────────────────────────────────────────────────────────────────────

def extract_facts(
    corpus: str,
    *,
    known_vendors: Optional[List[str]] = None,
    use_spacy: bool = False,
) -> Dict[str, List[Tuple[str, str]]]:
    """Run regex + (optional) spaCy NER over the corpus.

    Returns ``{category: [(match, snippet), ...]}`` where ``match`` is the
    text matched and ``snippet`` is ±100 chars of surrounding context.
    Duplicates within a category are de-duped on the lowercased match.

    The regex pass always runs (zero deps). spaCy runs only if
    ``use_spacy=True`` AND ``spacy`` + ``en_core_web_sm`` are importable.
    Defensive: failures here are logged and swallowed — the caller gets
    whatever facts were extractable.
    """
    if not corpus or not corpus.strip():
        logger.info("[facts_extractor] empty corpus — returning {}")
        return {}

    import time
    t_extract = time.time()
    logger.info(
        f"[facts_extractor] extract_facts START: corpus={len(corpus)} chars, "
        f"use_spacy={use_spacy}"
    )

    out: Dict[str, List[Tuple[str, str]]] = {}
    seen: Dict[str, Set[str]] = {}

    def _add(cat: str, match: str, snippet: str) -> None:
        key = match.strip().lower()
        if not key:
            return
        bucket = seen.setdefault(cat, set())
        if key in bucket:
            return
        bucket.add(key)
        out.setdefault(cat, []).append((match.strip(), snippet))

    def _snippet(m: "re.Match[str]") -> str:
        s = max(0, m.start() - 100)
        e = min(len(corpus), m.end() + 100)
        snip = corpus[s:e].strip()
        return re.sub(r"\s+", " ", snip)

    # Regex pass — unconditional.
    for m in _DATE_RE.finditer(corpus):
        _add(CAT_DATE, m.group(0), _snippet(m))
    for m in _SPRINT_RE.finditer(corpus):
        _add(CAT_SPRINT, m.group(0), _snippet(m))
    for m in _SEVERITY_RE.finditer(corpus):
        _add(CAT_SEVERITY, m.group(0), _snippet(m))
    for m in _PERCENT_RE.finditer(corpus):
        _add(CAT_PERCENTAGE, m.group(0), _snippet(m))
    for m in _METRIC_RE.finditer(corpus):
        _add(CAT_METRIC, m.group(0), _snippet(m))
    for m in _VENDOR_RE.finditer(corpus):
        _add(CAT_VENDOR, m.group(0), _snippet(m))
    if known_vendors:
        custom_re = re.compile(
            r"\b(?:" + "|".join(re.escape(v) for v in known_vendors) + r")\b"
        )
        for m in custom_re.finditer(corpus):
            _add(CAT_VENDOR, m.group(0), _snippet(m))
    for m in _PERSON_ROLE_RE.finditer(corpus):
        _add(CAT_PERSON_ROLE, f"{m.group(1)} — {m.group(2)}", _snippet(m))
    for m in _RELATIONAL_RE.finditer(corpus):
        _add(CAT_RELATIONAL, m.group(0), _snippet(m))
    for m in _STATUS_RISK_RE.finditer(corpus):
        _add(CAT_STATUS_RISK, m.group(0), _snippet(m))

    t_regex_done = time.time()
    regex_counts = {k: len(v) for k, v in out.items()}
    logger.info(
        f"[facts_extractor] regex pass done in {t_regex_done-t_extract:.2f}s — "
        f"counts={regex_counts}"
    )

    # spaCy NER pass — opt-in. Adds PERSON/ORG/DATE entities the regex
    # patterns can't catch (e.g., names without an adjacent role token).
    if use_spacy:
        try:
            ner = _spacy_ner(corpus)
            added = 0
            for ent_text, ent_label, ent_snip in ner:
                before = len(out.get(CAT_PERSON, [])) + len(out.get(CAT_ORG, [])) + len(out.get(CAT_DATE, []))
                if ent_label == "PERSON":
                    _add(CAT_PERSON, ent_text, ent_snip)
                elif ent_label == "ORG":
                    _add(CAT_ORG, ent_text, ent_snip)
                elif ent_label == "DATE":
                    _add(CAT_DATE, ent_text, ent_snip)
                after = len(out.get(CAT_PERSON, [])) + len(out.get(CAT_ORG, [])) + len(out.get(CAT_DATE, []))
                if after > before:
                    added += 1
            logger.info(
                f"[facts_extractor] spaCy added {added} new entities "
                f"(after de-dup against regex-found ones)"
            )
        except Exception as e:
            logger.warning(f"[facts_extractor] spaCy pass failed — regex-only: {e}")
    else:
        logger.info("[facts_extractor] spaCy pass skipped (use_spacy=False)")

    final_counts = {k: len(v) for k, v in out.items()}
    logger.info(
        f"[facts_extractor] extract_facts DONE in {time.time()-t_extract:.2f}s — "
        f"final counts={final_counts}"
    )
    return out


def route_facts_to_sections(
    facts: Dict[str, List[Tuple[str, str]]],
) -> Dict[int, List[str]]:
    """Map ``{category: [(match, snippet)]}`` → ``{section_number: [rendered_lines]}``.

    Each line: ``"- [{cat}] {match}  ·  {snippet}"``. Capped per section so
    the per-section user message stays bounded.
    """
    section_facts: Dict[int, List[str]] = {}
    for cat, items in facts.items():
        targets = _CATEGORY_TO_SECTIONS.get(cat, [])
        if not targets:
            continue
        for match, snippet in items:
            line = f"- [{cat}] {match}  ·  {snippet}"
            for sec_n in targets:
                bucket = section_facts.setdefault(sec_n, [])
                if len(bucket) < _MAX_FACTS_PER_SECTION:
                    bucket.append(line)
    return section_facts


# ────────────────────────────────────────────────────────────────────────────
# spaCy NER loader.
#
# The en_core_web_sm model is too big to bundle (~14 MB, would push the Lambda
# zip over the 250 MB unzipped limit). Instead we lazy-fetch it from S3 on the
# first ≥30k corpus call per warm container:
#   1. Check /tmp/en_core_web_sm-3.8.0/ already extracted? Skip download.
#   2. Else: boto3 download s3://…/models/en_core_web_sm-3.8.0.tar.gz → /tmp
#   3. Extract tarball into /tmp/
#   4. spacy.load(/tmp/en_core_web_sm-3.8.0, exclude=[...]) — load by path
#      since the model package isn't pip-installed.
#   5. Cache on module global; subsequent calls reuse (no S3, no spaCy load).
#
# Double-checked locking because the generator runs section workers in a
# ThreadPoolExecutor; without the lock multiple threads could trigger
# parallel S3 downloads.
#
# Defensive: any failure (boto3 missing, S3 denial, model corrupt, spacy
# import fails) returns None → caller falls back to regex-only output.
# The Lambda never breaks.
# ────────────────────────────────────────────────────────────────────────────

_SPACY_NLP = None
_SPACY_LOCK = threading.Lock()
_SPACY_TRIED = False

# We exclude every pipeline component except `tok2vec` (token vectorizer that
# NER depends on) and `ner` itself. Excluded components are NOT loaded — their
# model files don't need to be importable, but they DO need to exist on disk
# for spacy.load to read the config.cfg without error.
_SPACY_EXCLUDE = [
    "lemmatizer", "tagger", "parser", "attribute_ruler",
    "morphologizer", "senter", "sentencizer",
]


def _spacy_load_from_s3():
    """Download the FULL spaCy runtime tarball from S3, extract, inject on
    sys.path, and load the NER pipeline. Returns loaded nlp or None.

    Each step logs at INFO so you can trace cold-start behavior in
    CloudWatch:

      [step 1/6] boto3 import OK
      [step 2/6] /tmp marker missing — downloading runtime tarball
      [step 3/6] downloaded 61.4 MiB in 8.4s
      [step 4/6] extracted 2310 files in 1.9s
      [step 5/6] sys.path += /tmp/spacy_runtime
      [step 6/6] spacy.load OK (pipeline=['tok2vec', 'ner']) total=10.6s

    Warm-container re-entry skips steps 2-4 (marker dir already exists)
    and only repeats step 6 — that path logs `[warm] reused /tmp runtime`.
    """
    import sys
    import time

    t_start = time.time()

    # Step 1/6 — boto3
    try:
        import boto3
        logger.info("[facts_extractor] [step 1/6] boto3 import OK")
    except Exception as e:
        logger.error(f"[facts_extractor] [step 1/6] boto3 import FAILED: {e}")
        return None

    # Step 2-4 — fetch + extract (skipped on warm container)
    marker = os.path.join(SPACY_RUNTIME_LOCAL_DIR, "spacy")
    if os.path.isdir(marker):
        logger.info(
            f"[facts_extractor] [warm] reused /tmp runtime "
            f"(skip download/extract): {SPACY_RUNTIME_LOCAL_DIR}"
        )
    else:
        try:
            os.makedirs(SPACY_RUNTIME_LOCAL_DIR, exist_ok=True)
            logger.info(
                f"[facts_extractor] [step 2/6] cold container — fetching "
                f"s3://{SPACY_RUNTIME_S3_BUCKET}/{SPACY_RUNTIME_S3_KEY}"
            )
            t_dl = time.time()
            boto3.client("s3").download_file(
                SPACY_RUNTIME_S3_BUCKET, SPACY_RUNTIME_S3_KEY,
                SPACY_RUNTIME_LOCAL_TGZ,
            )
            tgz_mb = os.path.getsize(SPACY_RUNTIME_LOCAL_TGZ) / (1024 * 1024)
            logger.info(
                f"[facts_extractor] [step 3/6] downloaded {tgz_mb:.1f} MiB in "
                f"{time.time()-t_dl:.1f}s"
            )
            t_ex = time.time()
            file_count = 0
            with tarfile.open(SPACY_RUNTIME_LOCAL_TGZ, "r:gz") as tf:
                tf.extractall(SPACY_RUNTIME_LOCAL_DIR)
                file_count = sum(1 for _ in tf)
            logger.info(
                f"[facts_extractor] [step 4/6] extracted {file_count} files "
                f"in {time.time()-t_ex:.1f}s → {SPACY_RUNTIME_LOCAL_DIR}"
            )
        except Exception as e:
            logger.error(
                f"[facts_extractor] [step 2-4] S3 runtime fetch FAILED: {type(e).__name__}: {e}"
            )
            return None

    # Step 5/6 — sys.path
    if SPACY_RUNTIME_LOCAL_DIR not in sys.path:
        sys.path.append(SPACY_RUNTIME_LOCAL_DIR)
        logger.info(f"[facts_extractor] [step 5/6] sys.path += {SPACY_RUNTIME_LOCAL_DIR}")
    else:
        logger.info(f"[facts_extractor] [step 5/6] sys.path already contains runtime")

    # Step 6/6 — import + load
    try:
        import spacy
        logger.info(f"[facts_extractor] [step 6/6a] spacy module import OK (version={spacy.__version__})")
        model_path = os.path.join(SPACY_RUNTIME_LOCAL_DIR, SPACY_MODEL_PATH_IN_RUNTIME)
        if not os.path.isdir(model_path):
            logger.error(
                f"[facts_extractor] [step 6/6] model_path does NOT exist: {model_path}. "
                f"Tarball layout may have changed — verify SPACY_MODEL_PATH_IN_RUNTIME."
            )
            return None
        t_load = time.time()
        nlp = spacy.load(model_path, exclude=_SPACY_EXCLUDE)
        logger.info(
            f"[facts_extractor] [step 6/6b] spacy.load OK in {time.time()-t_load:.1f}s "
            f"(pipeline={nlp.pipe_names}) — TOTAL cold load: {time.time()-t_start:.1f}s"
        )
        return nlp
    except Exception as e:
        logger.error(
            f"[facts_extractor] [step 6/6] spacy import/load FAILED: {type(e).__name__}: {e}"
        )
        return None


def _spacy_ner(corpus: str) -> List[Tuple[str, str, str]]:
    """Return [(text, label, snippet), …] for PERSON/ORG/DATE entities,
    or [] if spaCy/model isn't available.

    Logs per-chunk timing + entity counts so CloudWatch shows whether the
    spaCy path is actually finding things on your real corpora.
    """
    import time

    global _SPACY_NLP, _SPACY_TRIED
    if _SPACY_NLP is None and not _SPACY_TRIED:
        with _SPACY_LOCK:
            if _SPACY_NLP is None and not _SPACY_TRIED:
                _SPACY_TRIED = True
                logger.info("[facts_extractor] spaCy first-use — triggering S3 lazy-load")
                _SPACY_NLP = _spacy_load_from_s3()
                if _SPACY_NLP is None:
                    logger.warning(
                        "[facts_extractor] spaCy load returned None — every future "
                        "extract_facts(use_spacy=True) call will skip the NER pass and "
                        "rely on regex-only output. Inspect logs above for the failure."
                    )
    if _SPACY_NLP is None:
        return []

    results: List[Tuple[str, str, str]] = []
    WINDOW = 200_000
    t_total = time.time()
    chunk_count = 0
    per_label = {"PERSON": 0, "ORG": 0, "DATE": 0}
    for i in range(0, len(corpus), WINDOW):
        chunk = corpus[i : i + WINDOW]
        chunk_count += 1
        t_chunk = time.time()
        try:
            doc = _SPACY_NLP(chunk)
        except Exception as e:
            logger.warning(f"[facts_extractor] spaCy chunk #{chunk_count} (offset {i}) FAILED: {e}")
            continue
        chunk_ents = 0
        for ent in doc.ents:
            if ent.label_ not in ("PERSON", "ORG", "DATE"):
                continue
            s = max(0, ent.start_char - 100)
            e = min(len(chunk), ent.end_char + 100)
            snip = re.sub(r"\s+", " ", chunk[s:e].strip())
            results.append((ent.text.strip(), ent.label_, snip))
            per_label[ent.label_] += 1
            chunk_ents += 1
        logger.info(
            f"[facts_extractor] spaCy chunk #{chunk_count} ({len(chunk)} chars) "
            f"-> {chunk_ents} entities in {time.time()-t_chunk:.2f}s"
        )

    logger.info(
        f"[facts_extractor] spaCy pass total: {len(results)} entities across "
        f"{chunk_count} chunks in {time.time()-t_total:.1f}s "
        f"(PERSON={per_label['PERSON']} ORG={per_label['ORG']} DATE={per_label['DATE']})"
    )
    return results
