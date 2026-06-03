"""
Checkov Service
Runs Checkov via its Python API and returns structured pass/fail results.
Pre-validates files with python-hcl2 to surface exact parse errors before scan.
"""

import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_SKIP_CHECKS = {"CKV_TF_2", "CKV2_AWS_5", "CKV_AWS_260"}


def _write_files(files: Dict[str, str]) -> str:
    tmp_dir = tempfile.mkdtemp(prefix="checkov_scan_")
    written = []
    for rel_path, content in files.items():
        norm = rel_path.replace("/", os.sep)
        full_path = os.path.join(tmp_dir, norm)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        written.append(norm)
    logger.info(f"Checkov temp dir: {tmp_dir} | Files: {written}")
    return tmp_dir


def _rel_path(tmp_dir: str, abs_path: str) -> str:
    try:
        return os.path.relpath(abs_path, tmp_dir).replace("\\", "/")
    except ValueError:
        return abs_path.replace("\\", "/")


def _find_hcl_error(file_path: str) -> Optional[str]:
    """
    Use python-hcl2 directly to find the exact line causing a parse failure.
    Binary-searches the file to narrow down to the problematic region.
    """
    try:
        import hcl2
    except ImportError:
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except Exception as e:
        return f"Cannot read file: {e}"

    # Quick check: does the file even fail?
    try:
        hcl2.load(io.StringIO(content))
        return "File parses OK with hcl2 (error may be in checkov post-processing)"
    except Exception as full_error:
        pass

    lines = content.splitlines()

    # Binary search to find the first line that causes a parse failure
    lo, hi = 1, len(lines)
    last_error = ""
    while lo < hi:
        mid = (lo + hi) // 2
        test = "\n".join(lines[:mid])
        try:
            hcl2.load(io.StringIO(test))
            lo = mid + 1
        except Exception as e:
            hi = mid
            last_error = str(e)

    problem_line_num = lo
    problem_line = lines[lo - 1] if lo <= len(lines) else ""
    return f"Line {problem_line_num}: {problem_line.strip()!r}  →  {last_error[:200]}"


_HCL_LANG_TAGS = {"hcl", "terraform", "tf", "hcl2"}

def _preprocess_hcl(content: str) -> str:
    """
    Apply minimal safe fixes for known python-hcl2 incompatibilities in
    Claude-generated Terraform:
      - Bare language tag on line 1 (e.g. "hcl\\n..." without backticks) → strip it
      - <<~EOF (trimmed heredoc) → <<-EOF  (supported variant)
      - Truncated files (open blocks with no closing brace) → append missing braces
    """
    # Strip bare language identifier on line 1 (Claude sometimes emits "hcl\n..." without backticks)
    first_newline = content.find("\n")
    if first_newline > 0:
        first_line = content[:first_newline].strip().lower()
        if first_line in _HCL_LANG_TAGS:
            content = content[first_newline + 1:]

    content = content.replace("<<~", "<<-")
    content = content.replace("<<-~", "<<-")

    # Detect truncation: count unmatched open braces and append closing braces.
    # Do NOT skip based on last character — files truncated after an attribute
    # value (ending with ") also need their blocks closed.
    stripped = content.rstrip()
    if stripped:
        depth = stripped.count("{") - stripped.count("}")
        if 0 < depth <= 20:  # sanity cap; increased to 20 for complex ECS files
            content = stripped + "\n" + ("}\n" * depth)

    return content


def run_checkov(files: Dict[str, str]) -> Tuple[dict, bool]:
    """
    Write files to temp dir, run Checkov via Python API, return (results_dict, checkov_available).
    Pre-validates each .tf file with python-hcl2 so we surface the exact parse error.
    """
    try:
        from checkov.terraform.runner import Runner
    except (ImportError, Exception) as first_err:
        logger.warning(f"checkov import failed ({first_err}) — attempting runtime install")
        try:
            # Install checkov itself with --no-deps to avoid boto3 version conflict,
            # then install all transitive deps checkov needs for the Terraform runner.
            _CHECKOV_TRANSITIVE = [
                "bc-python-hcl2", "bc-jsonpath-ng", "bc-detect-secrets",
                "networkx", "deep-merge", "dpath", "prettytable", "policyuniverse",
                "update-checker", "configargparse", "termcolor", "text-unidecode",
                "junit-xml", "license-expression", "packageurl-python",
                "dockerfile-parse", "GitPython", "arrow", "semantic-version",
                "tabulate", "jsonschema", "beautifulsoup4", "charset-normalizer",
            ]
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-deps", "--quiet", "checkov==3.2.526"],
                check=True, capture_output=True, timeout=120,
            )
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--quiet"] + _CHECKOV_TRANSITIVE,
                check=True, capture_output=True, timeout=180,
            )
            from checkov.terraform.runner import Runner
            logger.info("checkov installed successfully at runtime")
        except Exception as install_err:
            logger.error(f"checkov runtime install failed: {install_err}")
            return {
                "error": "Checkov is not installed. Run: pip install checkov",
                "passed_checks": [], "failed_checks": [],
                "summary": {"passed": 0, "failed": 0, "skipped": 0},
            }, False

    # Exclude .checkov.yaml — checkov auto-discovers it and applies quiet:true
    scan_files = {
        k: _preprocess_hcl(v)
        for k, v in files.items()
        if not k.endswith(".checkov.yaml")
    }
    tmp_dir = _write_files(scan_files)

    try:
        runner = Runner()
        logger.info(f"Running Checkov on: {tmp_dir}")
        report = runner.run(root_folder=tmp_dir)

        logger.info(
            f"Checkov raw — passed: {len(report.passed_checks)}, "
            f"failed: {len(report.failed_checks)}, "
            f"parsing_errors: {len(report.parsing_errors)}"
        )

        parse_error_details = []
        for err_path in report.parsing_errors:
            if isinstance(err_path, str) and os.path.isfile(err_path):
                detail = _find_hcl_error(err_path)
                rel = _rel_path(tmp_dir, err_path)
                logger.warning(f"HCL parse error [{rel}]: {detail}")
                parse_error_details.append({"file": rel, "detail": detail})

        passed = []
        for c in report.passed_checks:
            if c.check_id in _SKIP_CHECKS:
                continue
            passed.append({
                "check_id": c.check_id,
                "check_name": getattr(c, "check_name", c.check_id) or c.check_id,
                "resource": c.resource,
                "file": _rel_path(tmp_dir, c.file_abs_path),
            })

        failed = []
        for c in report.failed_checks:
            if c.check_id in _SKIP_CHECKS:
                continue
            failed.append({
                "check_id": c.check_id,
                "check_name": getattr(c, "check_name", c.check_id) or c.check_id,
                "resource": c.resource,
                "file": _rel_path(tmp_dir, c.file_abs_path),
                "file_path": c.file_abs_path,
                "lines": c.file_line_range,
                "guideline": getattr(c, "guideline", "") or "",
            })

        logger.info(f"Checkov filtered — passed: {len(passed)}, failed: {len(failed)}")

        result = {
            "passed_checks": passed,
            "failed_checks": failed,
            "summary": {
                "passed": len(passed),
                "failed": len(failed),
                "skipped": len(report.skipped_checks),
                "parsing_error": len(report.parsing_errors),
            },
        }
        if parse_error_details:
            result["parse_errors"] = parse_error_details
        return result, True

    except Exception as e:
        logger.error(f"Checkov execution error: {e}", exc_info=True)
        return {"error": str(e), "passed_checks": [], "failed_checks": [], "summary": {}}, True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def format_failures_for_prompt(failed_checks: List[dict]) -> str:
    if not failed_checks:
        return ""
    lines = ["The following Checkov security checks FAILED. Fix each issue in the relevant .tf file:\n"]
    for i, c in enumerate(failed_checks, 1):
        lines.append(
            f"{i}. [{c['check_id']}] Resource: {c['resource']} (file: {c['file']})\n"
            f"   Fix: {c.get('guideline') or 'See Checkov docs for ' + c['check_id']}"
        )
    return "\n".join(lines)
