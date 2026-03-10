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
BUILD_DIR = os.path.join(REPO_ROOT, "lambda_builds")
PROFILE = "590184044598_PowerUser"
REGION = "us-east-1"
KMS_KEY_ARN = "arn:aws:kms:us-east-1:590184044598:key/mrk-29bf4d8d90604305976882df6c91149e"

# Lambda definitions: name -> {function_name, handler_file, needs_prompts}
LAMBDAS = {
    "brd-chat": {
        "function_name": "sdlc-dev-brd-chat",
        "handler_file": "lambda_brd_chat.py",
        "needs_prompts": False,
    },
    "brd-from-history": {
        "function_name": "sdlc-dev-brd-from-history",
        "handler_file": "lambda_brd_from_history.py",
        "needs_prompts": True,
    },
    "brd-generator": {
        "function_name": "sdlc-dev-brd-generator",
        "handler_file": "lambda_brd_generator.py",
        "needs_prompts": True,
    },
    "requirements-gathering": {
        "function_name": "sdlc-dev-requirements-gathering",
        "handler_file": "lambda_requirements_gathering.py",
        "needs_prompts": True,
    },
}

# Shared local files every lambda needs
SHARED_FILES = [
    "llm_gateway.py",
]

# Shared directories (copied as-is)
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

    # 2. Copy shared files
    for f in SHARED_FILES:
        src = os.path.join(REPO_ROOT, f)
        if os.path.exists(src):
            shutil.copy2(src, pkg_dir)
            print(f"  + {f}")

    # 3. Copy shared directories (services/)
    for dir_name, files in SHARED_DIRS.items():
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

    # 5. Install pip packages (target Linux x86_64 for Lambda runtime)
    print(f"  Installing pip packages: {PIP_PACKAGES} (linux x86_64)")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install", *PIP_PACKAGES,
            "-t", pkg_dir,
            "--quiet", "--upgrade",
            "--platform", "manylinux2014_x86_64",
            "--only-binary=:all:",
            "--python-version", "3.12",
            "--implementation", "cp",
        ],
        check=True,
    )

    # 6. Remove unnecessary files to reduce zip size
    for root, dirs, files in os.walk(pkg_dir):
        # Remove __pycache__, *.dist-info, tests
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "tests", "test")]
        for d in list(dirs):
            if d.endswith(".dist-info") or d.endswith(".egg-info"):
                shutil.rmtree(os.path.join(root, d))
                dirs.remove(d)
        for f in files:
            if f.endswith(".pyc"):
                os.remove(os.path.join(root, f))

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


def deploy_lambda(name: str, config: dict, zip_path: str):
    """Deploy a lambda zip to AWS."""
    func_name = config["function_name"]
    print(f"\nDeploying {name} -> {func_name}...")

    # Update function code
    subprocess.run(
        [
            "aws", "lambda", "update-function-code",
            "--function-name", func_name,
            "--zip-file", f"fileb://{zip_path}",
            "--profile", PROFILE,
            "--region", REGION,
        ],
        check=True,
        capture_output=True,
    )
    print(f"  Code updated.")

    # Wait for update to complete
    print(f"  Waiting for update to complete...")
    subprocess.run(
        [
            "aws", "lambda", "wait", "function-updated-v2",
            "--function-name", func_name,
            "--profile", PROFILE,
            "--region", REGION,
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
            "--profile", PROFILE,
            "--region", REGION,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    env_vars = json.loads(result.stdout) if result.stdout.strip() != "null" else {}
    env_vars["KMS_KEY_ARN"] = KMS_KEY_ARN

    subprocess.run(
        [
            "aws", "lambda", "update-function-configuration",
            "--function-name", func_name,
            "--environment", json.dumps({"Variables": env_vars}),
            "--profile", PROFILE,
            "--region", REGION,
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
