"""
Build and deploy Lambda functions with all local dependencies.

Each Lambda gets:
  - Its own .py file (e.g. lambda_brd_chat.py)
  - llm_gateway.py           (Deluxe gateway proxy wrapper)
  - services/s3_service.py   (centralized S3 + KMS helper)
  - services/__init__.py
  - prompts/                  (if needed by that lambda)
  - openai + deps             (pip-installed into package)

Usage:
  python deploy_lambdas.py                  # build + deploy all
  python deploy_lambdas.py --build-only     # build zips only (no deploy)
  python deploy_lambdas.py --name brd-chat  # build + deploy one lambda
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
# Build outside the OneDrive-synced workspace by default. OneDrive aggressively
# locks files in its watched tree during pip install / shutil.rmtree, which
# produces flaky "[WinError 32] file in use" errors on rebuild. Override with
# LAMBDA_BUILD_DIR to use a specific path if needed.
BUILD_DIR = os.environ.get(
    "LAMBDA_BUILD_DIR",
    os.path.join(os.environ.get("TEMP", REPO_ROOT), "agentcore_lambda_builds"),
)
PROFILE = "590184044598_PowerUser"
REGION = "us-east-1"
KMS_KEY_ARN = "arn:aws:kms:us-east-1:590184044598:key/mrk-29bf4d8d90604305976882df6c91149e"

# When AWS_ACCESS_KEY_ID is exported (env-based credentials, e.g. SSO temp keys),
# skip --profile so AWS CLI picks up the env credentials instead.
_USE_ENV_CREDS = bool(os.getenv("AWS_ACCESS_KEY_ID"))


def _aws_common_args():
    """Common AWS CLI args; omit --profile when env credentials are present."""
    args = ["--region", REGION]
    if not _USE_ENV_CREDS:
        args += ["--profile", PROFILE]
    return args

# Lambda definitions: name -> {function_name, handler_file, needs_prompts}
#
# Note: the legacy brd-chat + brd-retriever Lambdas were removed from
# this map in Phase 5. The AWS Lambda functions still exist but are no
# longer maintained from source -- the unified orchestrator
# (lambda_brd_orchestrator.py) supersedes them. AWS-side
# decommissioning happens once the legacy /api/analyst-* endpoints
# stop calling them.
LAMBDAS = {
    # Unified BRD agent (features/aman) -- replaces the dead PM + Analyst
    # Runtimes and the legacy /api/analyst-* endpoints. Needs the new
    # services.brd_orchestrator_utils module + all brd_* prompt modules.
    # db_helper is bundled because verify_session_owned imports it lazily.
    "brd-orchestrator": {
        "function_name": "sdlc-dev-brd-orchestrator",
        "handler_file": "lambda_brd_orchestrator.py",
        "needs_prompts": True,
        "extra_shared_files": ["db_helper.py"],
        "extra_shared_dir_files": {"services": ["brd_orchestrator_utils.py"]},
        # db_helper top-imports psycopg2; without this the orchestrator
        # crashes the moment it tries to verify session ownership.
        # psycopg2-binary ships manylinux2014_x86_64 wheels so it
        # cross-compiles into the Lambda zip from any host OS.
        "extra_pip_packages": ["psycopg2-binary"],
    },
    "brd-from-history": {
        "function_name": "sdlc-dev-brd-from-history",
        "handler_file": "lambda_brd_from_history.py",
        "needs_prompts": True,
        # Phase 6: the parallel history-path lazy-imports a few helpers
        # from lambda_brd_generator (_prime_cache, _estimate_cost_usd,
        # _validate_section_against_format). Bundle it so the import
        # resolves at runtime. Lambda only uses the .lambda_handler from
        # lambda_brd_from_history.py — the additional module is library
        # code only.
        "extra_shared_files": ["lambda_brd_generator.py"],
    },
    "brd-generator": {
        "function_name": "sdlc-dev-brd-generator",
        "handler_file": "lambda_brd_generator.py",
        "needs_prompts": True,
        # Phase 1 per-section RAG: the generator lazily imports
        # services.embedding_service (chunker + batched Titan-v2 embeddings via
        # the same gateway it already uses for chat) and numpy for in-memory
        # cosine retrieval. embedding_service top-imports langchain_text_splitters
        # + python-dotenv, so bundle those. NO db_helper/psycopg2/RDS — the RAG
        # index is in-memory only (never the pgvector store).
        #
        # Post-audit hybrid extraction: facts_extractor.py runs regex + spaCy
        # NER over the corpus on the RAG path so surface facts (dates, names,
        # vendors, severity counts, percentages, status keywords, PERSON/ORG
        # entities) survive the embedding-similarity filter.
        #
        # spaCy bundle: spacy itself (~50 MB) + en_core_web_sm model (~15 MB)
        # installed via the wheel URL below (the model isn't on PyPI — it ships
        # from spaCy's GitHub releases). The wheel is platform-agnostic
        # (py3-none-any) so it installs cleanly under our linux_x86_64 pin.
        # spaCy's compiled deps (thinc, blis, cymem, murmurhash, preshed) ship
        # cp312 manylinux2014 wheels — pip resolves them automatically.
        # brd_orchestrator_utils bundled so lambda_brd_generator can call
        # write_memory_event for the post-generation BRD-to-memory push.
        # Pure stdlib + boto3 deps; no psycopg2 / db_helper transitive pull.
        "extra_shared_dir_files": {"services": ["embedding_service.py", "facts_extractor.py", "brd_orchestrator_utils.py"]},
        "extra_pip_packages": [
            "langchain-text-splitters", "numpy", "python-dotenv",
            # tiktoken: when present, embedding_service uses Anthropic's actual
            # tokenizer for chunk boundaries instead of the 1800-char heuristic
            # fallback. ~5 MB Rust extension, big precision win for per-section
            # RAG chunk sizing (the gateway was getting overshoot/undershoot on
            # 1800-char chunks per the BRD-gen logs). Verified bundled.
            "tiktoken",
            # The ENTIRE spaCy runtime (spacy + thinc + blis + en_core_web_sm
            # + transitive deps ≈ 140 MB) is NOT bundled. Sub-30k flows never
            # touch it; ≥30k flows lazy-fetch a single tarball from
            # s3://sdlc-orch-dev-us-east-1-app-data/models/spacy_runtime_v2.tar.gz
            # to /tmp on first invocation per warm container. Trade-off: ~10s
            # cold-start cost for the rare ≥30k flow, ~140 MB of permanent
            # bundle headroom for everything else. See facts_extractor._spacy_load_from_s3
            # and .scratch/build_spacy_runtime.py for the one-time setup.
            # v2 keeps typer/rich/click/pygments (spacy 3.8 needs typer at
            # import time of language.py — v1 dropped them and caused
            # `ModuleNotFoundError: No module named 'typer'` at spacy.load).
        ],
        # slim_spacy retired — spaCy is no longer in the bundle so there's
        # nothing to slim. If we ever re-bundle, restore the slim_spacy hook.
    },
    "requirements-gathering": {
        "function_name": "sdlc-dev-requirements-gathering",
        "handler_file": "lambda_requirements_gathering.py",
        "needs_prompts": True,
    },
    "sad-orchestrator": {
        "function_name": "sdlc-dev-sad-orchestrator",
        "handler_file": "lambda_sad_orchestrator.py",
        "needs_prompts": True,
    },
}

# Shared local files every lambda needs
SHARED_FILES = [
    "llm_gateway.py",
    "environment.py",
    "env_vdi.py",
    "db_config.py",  # imported transitively by env_vdi
]

# Shared directories (copied as-is). Per-lambda extra entries live on the
# LAMBDAS dict so a Lambda that needs e.g. services/brd_orchestrator_utils.py
# can opt in without bloating every other zip.
SHARED_DIRS = {
    "services": ["__init__.py", "s3_service.py"],
}

# Pip packages to install into each package
PIP_PACKAGES = ["openai"]


def build_lambda(name: str, config: dict) -> str:
    """Build a single lambda zip. Returns the zip path."""
    print(f"\n{'='*60}")
    print(f"Building: {name} ({config['function_name']})")
    print(f"{'='*60}")

    pkg_dir = os.path.join(BUILD_DIR, name)
    zip_path = os.path.join(BUILD_DIR, f"{name}.zip")

    # Clean previous build (handle Windows/OneDrive permission issues)
    if os.path.exists(pkg_dir):
        shutil.rmtree(pkg_dir, onexc=lambda func, path, exc: (os.chmod(path, 0o777), func(path)))
    os.makedirs(pkg_dir)

    # 1. Copy handler file
    src = os.path.join(REPO_ROOT, config["handler_file"])
    if not os.path.exists(src):
        print(f"  ERROR: {config['handler_file']} not found!")
        return None
    shutil.copy2(src, pkg_dir)
    print(f"  + {config['handler_file']}")

    # 2. Copy shared files (+ per-lambda extra_shared_files)
    extra_files = config.get("extra_shared_files", [])
    for f in SHARED_FILES + extra_files:
        src = os.path.join(REPO_ROOT, f)
        if os.path.exists(src):
            shutil.copy2(src, pkg_dir)
            print(f"  + {f}")

    # 3. Copy shared directories (services/) + per-lambda extras. The extras
    # map appends extra file names to the same directory the SHARED_DIRS
    # entry already targets.
    extra_dir_files = config.get("extra_shared_dir_files", {})
    for dir_name, files in SHARED_DIRS.items():
        merged = list(files) + list(extra_dir_files.get(dir_name, []))
        dest_dir = os.path.join(pkg_dir, dir_name)
        os.makedirs(dest_dir, exist_ok=True)
        for f in merged:
            src = os.path.join(REPO_ROOT, dir_name, f)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(dest_dir, f))
                print(f"  + {dir_name}/{f}")
    # Lambdas may also need dirs that don't exist in SHARED_DIRS at all --
    # handle those here.
    for dir_name, files in extra_dir_files.items():
        if dir_name in SHARED_DIRS:
            continue
        dest_dir = os.path.join(pkg_dir, dir_name)
        os.makedirs(dest_dir, exist_ok=True)
        for f in files:
            src = os.path.join(REPO_ROOT, dir_name, f)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(dest_dir, f))
                print(f"  + {dir_name}/{f}")

    # 4. Copy prompts/ if needed
    if config["needs_prompts"]:
        prompts_src = os.path.join(REPO_ROOT, "prompts")
        prompts_dest = os.path.join(pkg_dir, "prompts")
        if os.path.exists(prompts_src):
            shutil.copytree(prompts_src, prompts_dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            print(f"  + prompts/")

    # 5. Install pip packages (target Linux x86_64 for Lambda runtime).
    #    Per-lambda extras (e.g. psycopg2-binary for the orchestrator)
    #    are merged with the global set so only Lambdas that need a
    #    package pay its zip-size cost.
    pkgs = PIP_PACKAGES + list(config.get("extra_pip_packages", []))
    print(f"  Installing pip packages: {pkgs} (linux x86_64)")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install", *pkgs,
            "-t", pkg_dir,
            "--quiet", "--upgrade",
            "--platform", "manylinux2014_x86_64",
            "--only-binary=:all:",
            "--python-version", "3.12",
            "--implementation", "cp",
        ],
        check=True,
    )

    # 6. Remove unnecessary files to reduce zip size.
    # Earlier version filtered `dirs[:]` to skip walking into them but never
    # deleted the dirs themselves — they shipped in the zip. Two-pass approach
    # now: collect, then delete, to avoid mutating the os.walk iterator.
    to_delete: list[str] = []
    for root, dirs, files in os.walk(pkg_dir):
        for d in dirs:
            if d in ("__pycache__", "tests", "test"):
                to_delete.append(os.path.join(root, d))
            elif d.endswith(".dist-info") or d.endswith(".egg-info"):
                to_delete.append(os.path.join(root, d))
        for f in files:
            if f.endswith(".pyc"):
                os.remove(os.path.join(root, f))
    for p in to_delete:
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)

    # 6a. Drop zstandard ALWAYS (independent of slim_spacy). It's transitively
    # pulled in by langchain_core → langsmith and used only for compressed
    # LangSmith trace uploads, which we don't enable. ~23 MB saving across
    # every Lambda that has langchain_text_splitters as a dep. langsmith
    # lazy-imports zstandard only inside trace-upload code paths, so dropping
    # the package wholesale doesn't break import-time module resolution.
    zstd_path = os.path.join(pkg_dir, "zstandard")
    if os.path.isdir(zstd_path):
        shutil.rmtree(zstd_path, ignore_errors=True)

    # 6b. Per-lambda heavy-pruning hook. spaCy ships ~80 language modules
    # under spacy/lang/ but we only do English NER — strip the others.
    # Also drop training/kb/cli/displacy and the parser-internals Cython
    # extensions (we exclude tagger/parser/lemmatizer at load time so their
    # .so files never execute). Empirically saves ~80 MB on brd-generator.
    if config.get("slim_spacy"):
        spacy_root = os.path.join(pkg_dir, "spacy")
        lang_root  = os.path.join(spacy_root, "lang")
        if os.path.isdir(lang_root):
            # Top-level .py files under spacy/lang/ are language-agnostic
            # helpers (punctuation, char_classes, tokenizer_exceptions, etc.)
            # imported by spacy.language at module load. Keep ALL files;
            # only delete sibling language SUBDIRECTORIES other than `en`.
            keep_dirs = {"en"}
            for entry in os.listdir(lang_root):
                p = os.path.join(lang_root, entry)
                if os.path.isdir(p) and entry not in keep_dirs:
                    shutil.rmtree(p, ignore_errors=True)
        # NOTE: spacy/training, spacy/kb, spacy/pipeline/_parser_internals
        # ALL stay — they're imported by the core spacy.language chain at
        # module-load (spacy.scorer → spacy.training.example → _parser_internals).
        # Stripping any of them breaks `spacy.load()` empirically (verified in
        # py3.12 container). Only the safe-to-cut leaves remain:
        for sub in ("cli", "displacy"):
            p = os.path.join(spacy_root, sub)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)

        # Transitive deps we drop wholesale (verified not in our runtime path):
        #   zstandard  — langsmith uses it only for compressed trace uploads;
        #                we don't enable LANGCHAIN_TRACING_V2 → never called.
        #   pygments   — required by rich (CLI prettification).
        #   rich       — required by typer (CLI framework).
        #   typer      — used only by spacy's CLI (we stripped spacy/cli).
        #   click      — required by typer.
        # Net: ~33 MB savings. spaCy module-load doesn't import any of these.
        for dropped in ("zstandard", "pygments", "rich", "typer", "click"):
            p = os.path.join(pkg_dir, dropped)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)

    # 7. Create zip
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(pkg_dir):
            for f in files:
                file_path = os.path.join(root, f)
                arcname = os.path.relpath(file_path, pkg_dir)
                zf.write(file_path, arcname)

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"  => {zip_path} ({size_mb:.1f} MB)")
    return zip_path


def _function_exists(func_name: str) -> bool:
    """True if the Lambda function exists in the target account."""
    try:
        subprocess.run(
            [
                "aws", "lambda", "get-function",
                "--function-name", func_name,
                *_aws_common_args(),
            ],
            check=True,
            capture_output=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


# Defaults used when CREATING a new Lambda. Mirrors what the other
# Lambdas in this account already use (looked up via
# get-function-configuration). Hard-coded here rather than env-driven
# because they're account-level constants, not per-deploy choices.
DEFAULT_LAMBDA_ROLE = "arn:aws:iam::590184044598:role/sdlc-orch-dev-us-east-1-lambda-role"
DEFAULT_LAMBDA_RUNTIME = "python3.12"
DEFAULT_LAMBDA_MEMORY = 1024
DEFAULT_LAMBDA_TIMEOUT = 600  # 10 minutes -- generation paths can use it


def _create_function(name: str, config: dict, bucket: str, key: str) -> None:
    """Create a new Lambda function from the already-staged S3 zip. Used
    the first time a new lambda lands in LAMBDAS. The caller (deploy_lambda)
    handles staging so we don't double-upload.
    """
    func_name = config["function_name"]
    handler_module = config["handler_file"].replace(".py", "")
    handler = f"{handler_module}.lambda_handler"
    memory = config.get("memory_size", DEFAULT_LAMBDA_MEMORY)
    print(f"  Function {func_name} does not exist yet -- creating from s3://{bucket}/{key}...")
    subprocess.run(
        [
            "aws", "lambda", "create-function",
            "--function-name", func_name,
            "--runtime", DEFAULT_LAMBDA_RUNTIME,
            "--role", DEFAULT_LAMBDA_ROLE,
            "--handler", handler,
            "--code", f"S3Bucket={bucket},S3Key={key}",
            "--memory-size", str(memory),
            "--timeout", str(DEFAULT_LAMBDA_TIMEOUT),
            "--description", f"Auto-created by deploy_lambdas.py for {name}",
            *_aws_common_args(),
        ],
        check=True,
        capture_output=True,
    )
    print(f"  Created {func_name}.")
    # Newly-created functions need a moment before update-function-
    # configuration calls work cleanly.
    subprocess.run(
        [
            "aws", "lambda", "wait", "function-active-v2",
            "--function-name", func_name,
            *_aws_common_args(),
        ],
        check=True,
        capture_output=True,
    )


DEPLOY_BUCKET = "sdlc-orch-dev-us-east-1-app-data"
DEPLOY_PREFIX = "lambda-artifacts"   # writable on this bucket with SSE-KMS


def _stage_zip_to_s3(name: str, zip_path: str) -> tuple[str, str]:
    """Upload the zip to S3 (SSE-KMS) and return (bucket, key) for
    update-function-code. We always stage rather than --zip-file because the
    direct upload path fails behind the Deluxe network firewall for anything
    larger than ~10 MB. The bucket policy denies plain PutObject so we MUST
    pass --sse aws:kms --sse-kms-key-id; the KMS_KEY_ARN constant already
    matches the bucket's required key. Returns the S3 location.
    """
    key = f"{DEPLOY_PREFIX}/{name}.zip"
    print(f"  Staging zip -> s3://{DEPLOY_BUCKET}/{key} (SSE-KMS)")
    subprocess.run(
        [
            "aws", "s3", "cp", zip_path, f"s3://{DEPLOY_BUCKET}/{key}",
            "--sse", "aws:kms",
            "--sse-kms-key-id", KMS_KEY_ARN,
            *_aws_common_args(),
        ],
        check=True,
        capture_output=True,
    )
    print(f"  Staged.")
    return DEPLOY_BUCKET, key


def deploy_lambda(name: str, config: dict, zip_path: str):
    """Deploy a lambda zip to AWS. Creates the function on first run
    if it doesn't exist; otherwise updates the existing function's code.

    All updates go through S3 staging (Deluxe network blocks direct
    --zip-file upload for anything non-trivial). The staged object lives at
    s3://sdlc-orch-dev-us-east-1-app-data/lambda-artifacts/<name>.zip and is
    overwritten on every deploy.
    """
    func_name = config["function_name"]
    print(f"\nDeploying {name} -> {func_name}...")

    bucket, key = _stage_zip_to_s3(name, zip_path)

    if not _function_exists(func_name):
        _create_function(name, config, bucket, key)
    else:
        subprocess.run(
            [
                "aws", "lambda", "update-function-code",
                "--function-name", func_name,
                "--s3-bucket", bucket,
                "--s3-key", key,
                *_aws_common_args(),
            ],
            check=True,
            capture_output=True,
        )
        print(f"  Code updated from s3://{bucket}/{key}.")

    # Wait for update to complete
    print(f"  Waiting for update to complete...")
    subprocess.run(
        [
            "aws", "lambda", "wait", "function-updated-v2",
            "--function-name", func_name,
            *_aws_common_args(),
        ],
        check=True,
        capture_output=True,
    )

    # Add KMS_KEY_ARN to env vars (merge with existing)
    result = subprocess.run(
        [
            "aws", "lambda", "get-function-configuration",
            "--function-name", func_name,
            "--query", "Environment.Variables",
            "--output", "json",
            *_aws_common_args(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    env_vars = json.loads(result.stdout) if result.stdout.strip() != "null" else {}
    env_vars["KMS_KEY_ARN"] = KMS_KEY_ARN

    # Backend callback so Lambdas can attribute LLM token usage to a user.
    # Read from environment so secrets aren't checked in. Skip if unset.
    backend_url = os.getenv("BACKEND_URL", "")
    internal_api_key = os.getenv("INTERNAL_API_KEY", "")
    if backend_url:
        env_vars["BACKEND_URL"] = backend_url
    if internal_api_key:
        env_vars["INTERNAL_API_KEY"] = internal_api_key

    subprocess.run(
        [
            "aws", "lambda", "update-function-configuration",
            "--function-name", func_name,
            "--environment", json.dumps({"Variables": env_vars}),
            *_aws_common_args(),
        ],
        check=True,
        capture_output=True,
    )
    print(f"  Environment updated (KMS_KEY_ARN added).")
    print(f"  Deployed {func_name} successfully!")


def main():
    parser = argparse.ArgumentParser(description="Build and deploy Lambda functions")
    parser.add_argument("--build-only", action="store_true", help="Build zips without deploying")
    parser.add_argument("--name", type=str, help="Deploy a specific lambda (e.g. brd-chat)")
    args = parser.parse_args()

    os.makedirs(BUILD_DIR, exist_ok=True)

    targets = {args.name: LAMBDAS[args.name]} if args.name else LAMBDAS

    for name, config in targets.items():
        zip_path = build_lambda(name, config)
        if not zip_path:
            print(f"  SKIPPED {name} (build failed)")
            continue
        if not args.build_only:
            deploy_lambda(name, config, zip_path)

    print(f"\n{'='*60}")
    print("Done!")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
