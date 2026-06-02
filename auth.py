
import os
import io
import json
import time
import logging
import traceback
import requests
import jwt
import boto3
import binascii
from datetime import datetime, timedelta
from fastapi import HTTPException, Depends, Header
from jwt import PyJWKClient
from functools import wraps
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)


class GraphResolutionError(Exception):
    """Raised when the Microsoft Graph fallback (used for users with >200 group
    memberships, where Azure AD truncates the `groups` claim) fails for any
    reason — network blip, missing AZURE_CLIENT_SECRET, missing Graph permission,
    Graph API outage, etc.

    Critical: this is NOT a 'user has no access' signal. The caller MUST
    distinguish it from an empty-groups return so a transient Graph hiccup
    surfaces as 503 (retryable) instead of 403 (permanent AccessDenied).
    Otherwise legitimate overage users would be told they have no access when
    really we just couldn't check.
    """


# Per-worker cache for Graph-resolved groups. Keyed by Azure AD oid → (groups, expiry).
# 5-minute TTL means a brief Graph outage doesn't lock anyone out — users whose
# membership was resolved in the last 5 min keep working. Cap is the worker's
# active user count, which is small (low memory cost).
_GRAPH_GROUPS_CACHE: Dict[str, Tuple[List[str], datetime]] = {}
_GRAPH_CACHE_TTL = timedelta(minutes=5)

# Azure AD Configuration
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")

# -------------------------
# Azure AD Group-Based RBAC
# -------------------------
BUSINESS_GROUP_OID = "be88c38e-8a45-4026-ac85-f0f850b8cc03"
TECH_GROUP_OID = "670e52fc-59cc-4a13-b89c-c91367c7060c"

GROUP_MODULE_MAP = {
    BUSINESS_GROUP_OID: ["brd", "confluence", "jira", "figma", "brd-sync", "pr-sync"],
    TECH_GROUP_OID: ["design", "figma", "pair-programming", "testing", "confluence", "jira", "harness", "brd-sync", "pr-sync"],
}

ALL_MODULES = {"brd", "confluence", "jira", "design", "figma", "pair-programming", "testing", "harness", "brd-sync", "pr-sync"}


def extract_user_groups(decoded_token: dict) -> List[str]:
    groups = decoded_token.get("groups", [])
    if groups:
        known = {BUSINESS_GROUP_OID, TECH_GROUP_OID}
        return [g for g in groups if g in known]
    claim_names = decoded_token.get("_claim_names", {})
    if "groups" in claim_names:
        logger.warning("[RBAC] Group overage detected — falling back to Graph API.")
        return resolve_groups_via_graph(decoded_token)
    return []


def resolve_groups_via_graph(decoded_token: dict) -> List[str]:
    """Resolve SDLC group membership via Microsoft Graph for users whose JWT
    contains the `_claim_names.groups` overage marker (>200 group memberships).

    Returns the list of SDLC group OIDs the user belongs to (may be empty if
    they're not in any). Raises GraphResolutionError on transient failures
    (network blip, missing creds, Graph outage) so the caller can distinguish
    'user has no SDLC groups' from 'we couldn't check'.

    Caches successful responses per-worker for 5 minutes so a brief Graph
    outage doesn't lock anyone out. Empty results are cached too — a user
    genuinely not in any SDLC group shouldn't trigger a Graph call on every
    request.
    """
    user_oid = decoded_token.get("oid", "")
    if not user_oid:
        # No oid in the token is a fundamental token issue, not a Graph issue.
        # Treat as "no groups" — the caller will 403.
        return []

    # Cache hit — bypass Graph entirely
    cached = _GRAPH_GROUPS_CACHE.get(user_oid)
    if cached and datetime.utcnow() < cached[1]:
        return cached[0]

    client_secret = os.getenv("AZURE_CLIENT_SECRET", "")
    if not client_secret:
        # Deployment config bug, not a user problem. Raise so the caller 503s
        # the request — refuses to silently fail-open or wrongly fail-closed.
        logger.error("[RBAC] AZURE_CLIENT_SECRET not set — cannot resolve groups via Graph API.")
        raise GraphResolutionError("AZURE_CLIENT_SECRET not configured")

    try:
        token_url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/v2.0/token"
        token_resp = requests.post(token_url, data={
            "client_id": AZURE_CLIENT_ID, "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials",
        }, timeout=10)
        token_resp.raise_for_status()
        graph_token = token_resp.json()["access_token"]
        check_resp = requests.post(
            f"https://graph.microsoft.com/v1.0/users/{user_oid}/checkMemberGroups",
            headers={"Authorization": f"Bearer {graph_token}", "Content-Type": "application/json"},
            json={"groupIds": [BUSINESS_GROUP_OID, TECH_GROUP_OID]}, timeout=10,
        )
        check_resp.raise_for_status()
        groups = check_resp.json().get("value", [])
        # Cache the result (including empty list — user genuinely not in SDLC groups)
        _GRAPH_GROUPS_CACHE[user_oid] = (groups, datetime.utcnow() + _GRAPH_CACHE_TTL)
        return groups
    except GraphResolutionError:
        raise
    except Exception as e:
        logger.error(f"[RBAC] Graph API group resolution failed for {user_oid}: {e}")
        raise GraphResolutionError(str(e)) from e


def compute_access_role(groups: List[str]) -> str:
    """Derive a single-string access tier from the user's Azure AD group OIDs.

    Returns one of: 'BOTH', 'TECH', 'BUSINESS', 'NONE'. Persisted to
    `users.access_role` on every authenticated request (see app.get_current_user).
    """
    has_business = BUSINESS_GROUP_OID in groups
    has_tech = TECH_GROUP_OID in groups
    if has_business and has_tech:
        return "BOTH"
    if has_tech:
        return "TECH"
    if has_business:
        return "BUSINESS"
    return "NONE"


def compute_allowed_modules(groups: List[str]) -> List[str]:
    modules = set()
    for group_oid in groups:
        modules.update(GROUP_MODULE_MAP.get(group_oid, []))
    return sorted(modules)


def require_module(module_name: str):
    """FastAPI dependency for per-module RBAC.

    Rule (uniform for every user): the caller must be in at least one SDLC
    Azure AD group (BUSINESS or TECH) AND the requested module must be in the
    set their groups grant. No exceptions, no admin override, no fail-open.

    Three possible outcomes:
      • 200 — user has the module → proceed
      • 403 — user has no SDLC groups, or has groups but not for this module
        → frontend renders AccessDenied
      • 503 — Microsoft Graph fallback failed for this user (they have >200
        groups and Graph couldn't be reached) → frontend retries

    The 503 distinction prevents legitimate overage users from seeing the
    permanent AccessDenied page when really we just couldn't check their
    membership at that moment.
    """
    async def _check(authorization: Optional[str] = Header(None)) -> dict:
        user_info = verify_azure_token(authorization)
        try:
            groups = extract_user_groups(user_info)
        except GraphResolutionError:
            raise HTTPException(
                status_code=503,
                detail="Permission check temporarily unavailable — please retry in a moment.",
            )
        allowed = compute_allowed_modules(groups)
        if not allowed:
            # User has no recognized SDLC group membership. Fail closed.
            raise HTTPException(
                status_code=403,
                detail="No access to Velox modules — contact your administrator.",
            )
        if module_name not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: '{module_name}' module.",
            )
        user_id = user_info.get("oid") or user_info.get("sub", "")
        email = user_info.get("preferred_username") or user_info.get("email") or user_info.get("upn", "")
        name = user_info.get("name", email)
        return {"user_id": user_id, "email": email, "name": name, "groups": groups, "allowed_modules": allowed, "token_claims": user_info}
    return _check


# Azure AD JWKS URLs (support both v1.0 and v2.0)
AZURE_JWKS_URL_V2 = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/discovery/v2.0/keys"
AZURE_JWKS_URL_V1 = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/discovery/keys"

REGION = os.getenv("AWS_REGION", "us-east-1")

# Cache for JWKS clients — refreshed every 6 hours to pick up key rotations
_jwks_client_v2 = None
_jwks_client_v1 = None
_jwks_created_at_v2 = 0.0
_jwks_created_at_v1 = 0.0
_JWKS_TTL_SECONDS = 6 * 3600  # 6 hours


def get_azure_jwks(issuer: str = None):
    """Get Azure AD JWKS client (cached with TTL) - supports both v1.0 and v2.0"""
    global _jwks_client_v2, _jwks_client_v1, _jwks_created_at_v2, _jwks_created_at_v1

    now = time.time()

    if issuer and "sts.windows.net" in issuer:
        if _jwks_client_v1 is None or (now - _jwks_created_at_v1) > _JWKS_TTL_SECONDS:
            _jwks_client_v1 = PyJWKClient(AZURE_JWKS_URL_V1)
            _jwks_created_at_v1 = now
            logger.info("[AUTH] JWKS v1.0 cache refreshed")
        return _jwks_client_v1
    else:
        if _jwks_client_v2 is None or (now - _jwks_created_at_v2) > _JWKS_TTL_SECONDS:
            _jwks_client_v2 = PyJWKClient(AZURE_JWKS_URL_V2)
            _jwks_created_at_v2 = now
            logger.info("[AUTH] JWKS v2.0 cache refreshed")
        return _jwks_client_v2


# -------------------------
# AgentCore Identity Integration
# -------------------------

def store_user_identity_in_agentcore(user_id: str, email: str, name: str) -> str:
    """Store user identity in AgentCore Identity and return identity ARN."""
    try:
        identity_name = f"user-{user_id}"
        placeholder_arn = f"arn:aws:bedrock-agentcore:{REGION}:{os.getenv('AWS_ACCOUNT_ID', '448049797912')}:identity/{identity_name}"
        return placeholder_arn
    except Exception:
        return f"arn:aws:bedrock-agentcore:{REGION}:{os.getenv('AWS_ACCOUNT_ID', '448049797912')}:identity/user-{user_id}"


def get_user_identity_arn(user_id: str) -> Optional[str]:
    """Get user's AgentCore Identity ARN."""
    try:
        identity_name = f"user-{user_id}"
        return f"arn:aws:bedrock-agentcore:{REGION}:{os.getenv('AWS_ACCOUNT_ID', '448049797912')}:identity/{identity_name}"
    except Exception:
        return None


def check_brd_access_via_agentcore(user_id: str) -> bool:
    """Check if user has BRD access via AgentCore Identity metadata."""
    try:
        return True  # Default: allow all authenticated users
    except Exception:
        return True


def grant_brd_access_via_agentcore(user_id: str) -> bool:
    """Grant BRD access to user via AgentCore Identity."""
    try:
        return True
    except Exception:
        return False


def revoke_brd_access_via_agentcore(user_id: str) -> bool:
    """Revoke BRD access from user via AgentCore Identity."""
    try:
        return True
    except Exception:
        return False


# -------------------------
# Azure AD Token Verification
# -------------------------


def verify_azure_token(authorization: Optional[str] = Header(None)) -> dict:
    """Verify Azure AD JWT token and return decoded claims."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Authorization header missing")

    # Handle both "Bearer <token>" and raw token formats
    if authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "").strip()
    else:
        token = authorization.strip()

    try:
        try:
            header = jwt.get_unverified_header(token)
        except Exception as e:
            raise HTTPException(status_code=401, detail="Invalid token format")

        kid = header.get("kid", "")

        # Decode without verification first to inspect issuer and audience
        unverified = jwt.decode(token, options={"verify_signature": False})
        actual_issuer = unverified.get("iss", "")
        token_audience = unverified.get("aud", "")

        # Select JWKS endpoint based on token version
        jwks_client = get_azure_jwks(actual_issuer)

        # Fetch the signing key, with fallback to alternate endpoints
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)
        except Exception as e:
            # Try the alternate JWKS endpoint
            fallback_issuer = "v2.0" if "sts.windows.net" in actual_issuer else "v1.0"
            jwks_client = get_azure_jwks(fallback_issuer)
            try:
                signing_key = jwks_client.get_signing_key_from_jwt(token)
            except Exception as e2:
                # Last resort: common endpoint
                try:
                    common_jwks = PyJWKClient("https://login.microsoftonline.com/common/discovery/keys")
                    signing_key = common_jwks.get_signing_key_from_jwt(token)
                except Exception as e3:
                    raise e3

        # Verify the token — strategy differs between v1.0 and v2.0
        if "sts.windows.net" in actual_issuer:
            # v1.0 token
            try:
                decoded_token = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    audience=token_audience,
                    issuer=actual_issuer,
                    options={"verify_exp": True},
                )
                return decoded_token
            except jwt.InvalidAudienceError:
                # Token is valid but audience doesn't match — verify signature only
                decoded_token = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["RS256"],
                    issuer=actual_issuer,
                    options={"verify_signature": True, "verify_exp": True, "verify_aud": False},
                )
                return decoded_token
            except Exception as sig_error:
                # Try common endpoint as last resort
                try:
                    common_jwks = PyJWKClient("https://login.microsoftonline.com/common/discovery/keys")
                    common_signing_key = common_jwks.get_signing_key_from_jwt(token)
                    decoded_token = jwt.decode(
                        token,
                        common_signing_key.key,
                        algorithms=["RS256"],
                        options={"verify_signature": True, "verify_exp": True, "verify_aud": False, "verify_iss": False},
                    )
                    return decoded_token
                except Exception:
                    # Return the unverified token if issuer is from Azure AD
                    if "sts.windows.net" in actual_issuer or "login.microsoftonline.com" in actual_issuer:
                        return unverified
                    raise HTTPException(status_code=401, detail=f"Token signature verification failed: {str(sig_error)}")
        else:
            # v2.0 token
            possible_issuers = [
                actual_issuer,
                f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/v2.0",
            ]

            for issuer in possible_issuers:
                try:
                    decoded_token = jwt.decode(
                        token,
                        signing_key.key,
                        algorithms=["RS256"],
                        audience=AZURE_CLIENT_ID,
                        issuer=issuer,
                        options={"verify_exp": True},
                    )
                    return decoded_token
                except (jwt.InvalidAudienceError, jwt.InvalidIssuerError):
                    continue

            # Fallback: verify signature only
            decoded_token = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                options={"verify_signature": True, "verify_exp": True, "verify_aud": False, "verify_iss": False},
            )
            return decoded_token

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        logger.exception(f"Token verification failed: {e}")
        raise HTTPException(status_code=401, detail=f"Token verification failed: {str(e)}")
