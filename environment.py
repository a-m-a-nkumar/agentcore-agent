"""
ENVIRONMENT SWITCH
==================
Edit ONLY this file to switch between LOCAL and VDI environments.

Instructions
------------
1. For LOCAL development  →  uncomment the "LOCAL" line, comment out "VDI"
2. For VDI / client       →  uncomment the "VDI" line,   comment out "LOCAL"

What this controls:
  - S3 uploads   : plain boto3 (local) vs SSE-KMS via s3_service (VDI)
  - Database     : direct password (local) vs AWS Secrets Manager (VDI)
  - LLM calls    : direct AWS Bedrock (local) vs Deluxe API Gateway (VDI)
  - Agent ARNs   : account 448049797912 (local) vs 590184044598 (VDI)
"""

# ============================================================
# Uncomment ONE of the two lines below:
# ============================================================

# from env_local import *    # LOCAL  — uncomment for local development      # noqa: F401, F403
from env_vdi import *    # VDI    — uncomment for VDI / client deployment  # noqa: F401, F403
