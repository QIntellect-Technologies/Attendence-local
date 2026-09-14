"""
local_node/license_check.py

Access gate for the fully-offline, single-tenant dashboard. Replaces the
old heartbeat/org_status license_gate.py.

Trust model: a signed Ed25519 token (license_token.py, copied verbatim
from the backend) is the sole source of truth for whether this install
may run. Verification is 100% local — baked-in public key + whatever
token this node last received, checked against this machine's own
clock. No network call is ever required to verify a token already on
disk; the network is only ever needed to receive a newer one.

The token is delivered, not entered: activation.py's initial /activate
call and every heartbeat afterward carry the org's current active
license_token (support_db_licenses.get_active_license_token on the
backend), cached via local_db.set_cached_license_token(). Both call
sites only cache on a *successful* round-trip, so a connectivity
failure never touches the cached token — the last valid one keeps
working until its own expires_at.

Fail-open in exactly one case: no token has ever been cached (brand-new
install, or this org has no license issued yet). Every other outcome —
expired, forged signature, wrong org — blocks. Unlike the old
org_status cache, an expired signed token is a hard stop even while
offline; that is the entire reason for this migration.
"""
from __future__ import annotations

import time
from typing import Any

from local_node import local_db
from local_node.config_store import apply_node_config, get_org_id, load_config
from license_token import LicenseTokenError, LicenseTokenExpired, load_public_key, verify_license_token

# check_remote_revocation is the ONE network call this whole gate makes, and
# current_status() runs it on every dashboard request via
# auth.check_login_and_license(). That call now also carries the org/branch
# config refresh (see support_db_licenses.check_license_status on the
# backend) rather than adding a second heartbeat/node_api_key mechanism
# just for config. Throttled here so "on every request" doesn't mean "on
# every request over the network" — offline signature/expiry verification
# below is unaffected and still runs every single call.
_REMOTE_CHECK_MIN_INTERVAL_SECONDS = 120
_last_remote_check_at = 0.0
_last_remote_check_result: dict[str, Any] = {"is_revoked": False}

# Public half of the backend's signing keypair (see backend/license_token.py's
# module docstring for how it's generated). Safe to embed — verification
# never requires the private key.
_LICENSE_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEARf2dAVxbyTVZhQYQpkF8lz33bD/TUEjn9BW3JgUglfE=
-----END PUBLIC KEY-----"""

_MESSAGES = {
    "not_activated": "This install is not activated. Enter a valid license key from QIntellect Support.",
    "expired": "This install's license has expired. Contact QIntellect Support to renew.",
    "revoked": "This install's license has been revoked by Support. Contact Support for a new key.",
    "invalid": "This install's license is invalid. Contact QIntellect Support.",
}

_public_key_cache = None


def _public_key():
    global _public_key_cache
    if _public_key_cache is None:
        _public_key_cache = load_public_key(_LICENSE_PUBLIC_KEY_PEM)
    return _public_key_cache


def check_remote_revocation(jti: str, org_id: str | None = None) -> bool:
    """Check if a license has been explicitly revoked by Support on the backend.
    Returns True if confirmed revoked, False if active or offline/unreachable.

    Throttled to _REMOTE_CHECK_MIN_INTERVAL_SECONDS: called on every
    dashboard request (via current_status), but the network is only
    actually hit at most that often. Between checks, the last known
    result is reused — an already-active install never needs to "wait"
    for a check, it just isn't re-verified on every single click.

    The node's only network address for this is the Support/backend URL
    already in node_config.json (railway_api_base_url/api_base_url) — the
    client machine has no Supabase credentials and never talks to Supabase
    directly, only to the backend's own API.
    """
    global _last_remote_check_at, _last_remote_check_result

    if not jti:
        return False

    now = time.monotonic()
    if now - _last_remote_check_at < _REMOTE_CHECK_MIN_INTERVAL_SECONDS:
        return bool(_last_remote_check_result.get("is_revoked"))

    cfg = load_config()
    base_url = str(cfg.get("railway_api_base_url") or cfg.get("api_base_url") or "").rstrip("/")
    if not base_url:
        return bool(_last_remote_check_result.get("is_revoked"))

    try:
        import requests
        resp = requests.post(
            f"{base_url}/v1/node/license/check",
            json={"jti": jti, "org_id": org_id},
            timeout=4,
        )
        if resp.status_code != 200:
            return bool(_last_remote_check_result.get("is_revoked"))

        data = resp.json()
        is_revoked = data.get("is_revoked") is True or str(data.get("status")).lower() == "revoked"

        # Piggyback config refresh on this same round trip (see backend's
        # check_license_status) — only when the license is confirmed
        # active, and never let a refresh failure affect revocation status.
        if not is_revoked and isinstance(data.get("config"), dict):
            try:
                apply_node_config(data["config"])
            except Exception:
                pass

        _last_remote_check_at = now
        _last_remote_check_result = {"is_revoked": is_revoked}
        return is_revoked
    except Exception:
        # Offline/unreachable: don't advance the throttle timestamp, so the
        # next request tries again immediately rather than waiting out the
        # full interval on what was actually a failed attempt.
        return bool(_last_remote_check_result.get("is_revoked"))



def current_status(check_remote: bool = True) -> dict[str, Any]:
    token = local_db.get_cached_license_token()
    if not token:
        return {
            "org_status": "not_activated",
            "blocked": True,
            "message": _MESSAGES["not_activated"],
            "expires_at": None,
        }

    org_id = get_org_id(load_config())
    try:
        payload = verify_license_token(token, _public_key(), expected_org_id=org_id or None)
    except LicenseTokenExpired:
        return {
            "org_status": "expired",
            "blocked": True,
            "message": _MESSAGES["expired"],
            "org_id": org_id,
            "expires_at": None,
        }
    except LicenseTokenError:
        return {
            "org_status": "invalid",
            "blocked": True,
            "message": _MESSAGES["invalid"],
            "org_id": org_id,
            "expires_at": None,
        }

    # If connected to backend, verify if this specific token (jti) was revoked
    if check_remote and check_remote_revocation(payload.jti, payload.org_id):
        local_db.set_cached_license_token("")
        return {
            "org_status": "revoked",
            "blocked": True,
            "message": _MESSAGES["revoked"],
            "org_id": payload.org_id,
            "expires_at": None,
        }

    return {
        "org_status": "active",
        "blocked": False,
        "message": None,
        "org_id": payload.org_id,
        "expires_at": payload.expires_at,
        "issued_at": payload.issued_at,
    }


def claim_remote_license(jti: str, org_id: str, node_id: str, hostname: str) -> dict[str, Any]:
    """Contact the backend (Support's own API — the only address this node
    knows; it holds no Supabase credentials and never talks to Supabase
    directly) to claim this license token for this machine.

    Raises ValueError if already claimed by another machine, revoked, or
    unreachable — a claim is a one-time, explicit, user-initiated action
    (pasting the license key), so unlike the ongoing revocation check
    there is no "last known good" fallback to reuse here: it must succeed
    against the backend or the person is told clearly why not.
    """
    if not jti or not node_id:
        return {}

    cfg = load_config()
    base_url = str(cfg.get("railway_api_base_url") or cfg.get("api_base_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("This node has no backend URL configured. Contact QIntellect Support.")

    import requests
    try:
        resp = requests.post(
            f"{base_url}/v1/node/license/claim",
            json={"jti": jti, "org_id": org_id, "node_id": node_id, "hostname": hostname},
            timeout=8,
        )
    except requests.exceptions.RequestException as exc:
        raise ValueError(f"Could not reach QIntellect Support to activate this license: {exc}") from exc

    try:
        data = resp.json()
    except ValueError:
        data = {}

    if resp.status_code != 200 or data.get("success") is False:
        err_msg = data.get("error") or data.get("message") or f"Activation failed ({resp.status_code})"
        raise ValueError(err_msg)

    return data


def activate_license(raw_token: str) -> dict[str, Any]:
    """Validate, claim, and store a license key string.

    Supports:
    1. First-ever activation (no org_id yet): trusts the cryptographically
       verified org_id in the signed token and persists it into node_config.json.
    2. Renewal / replacement: verifies that the new token belongs to the SAME
       org_id already bound to this machine.
    3. One-time Machine Claim: binds the license token (jti) to this specific
       node_id online to prevent reusing the same token on other machines.
    4. Revocation check: verifies that the token has not been revoked by Support.
    """
    token = str(raw_token or "").strip()
    if not token:
        raise ValueError("License key is required.")

    import socket
    import uuid
    from local_node.config_store import save_config

    cfg = load_config()
    existing_org_id = get_org_id(cfg)
    hostname = socket.gethostname()

    # Ensure stable node_id
    node_id = str(cfg.get("node_id") or "").strip()
    if not node_id:
        node_id = f"node_{uuid.uuid4().hex[:12]}"
        save_config({"node_id": node_id})

    # Verifies offline with embedded Ed25519 public key.
    # Raises LicenseTokenExpired or LicenseTokenInvalid on any signature/org mismatch.
    payload = verify_license_token(token, _public_key(), expected_org_id=existing_org_id or None)

    # The token carries this org's backend URL — persist it before the
    # claim call below needs it, so the install never has to ship with
    # any backend URL baked in; the license key alone is enough.
    if payload.api_base_url:
        save_config({"railway_api_base_url": payload.api_base_url, "api_base_url": payload.api_base_url})

    # One-time Machine Claim: ensure token hasn't been claimed by another
    # machine. Raises on failure — this is a paste-and-go action, it
    # either succeeds against the backend or the person sees why not.
    claim_result = claim_remote_license(payload.jti, payload.org_id, node_id, hostname) or {}

    # Online check: ensure token has not been revoked on backend
    if check_remote_revocation(payload.jti, payload.org_id):
        raise ValueError("This license token has been revoked by Support and cannot be used.")

    # claim_result is already shaped by the backend's build_node_config_payload
    # (organization_name, contact_email, contact_phone, business_type,
    # enabled_staff_types, branch_id/name/location/max_staff_capacity, ...) —
    # apply_node_config persists that whole shape identically to how
    # install-token activation and the ongoing revocation-check config
    # refresh do, so nothing here hand-picks a subset of fields again.
    save_config({"org_id": payload.org_id, "organization_id": payload.org_id})
    config = apply_node_config(claim_result)
    local_db.set_cached_license_token(token)

    # Sync into SQLite dashboard_config as well — this is the branch-scoped
    # local cache _dashboard_bootstrap() falls back to; keep it consistent
    # with what was just written to node_config.json.
    org_name_val = str(config.get("organization_name") or "").strip()
    branch_name_val = str(config.get("branch_name") or "").strip()
    if org_name_val or branch_name_val:
        try:
            saved = local_db.get_dashboard_config("local-branch")
            local_db.save_dashboard_config("local-branch", {
                **saved,
                "organization_id": payload.org_id,
                **({"organization_name": org_name_val, "org_name": org_name_val} if org_name_val else {}),
                **({"branch_name": branch_name_val, "branchName": branch_name_val} if branch_name_val else {}),
            })
        except Exception:
            pass

    return {
        "success": True,
        "org_status": "active",
        "blocked": False,
        "org_id": payload.org_id,
        "expires_at": payload.expires_at,
        "issued_at": payload.issued_at,
    }



def blocked_response() -> tuple[Any, int] | None:
    from flask import jsonify

    status = current_status()
    if not status["blocked"]:
        return None
    return jsonify({
        "success": False,
        "message": status["message"],
        "code": "ORG_ACCESS_BLOCKED",
        "organization_status": status["org_status"],
        "expires_at": status.get("expires_at"),
    }), 403