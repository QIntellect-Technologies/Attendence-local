"""
local_node/auth.py

Local dashboard login for the single-tenant offline node.

DESIGN: stateless, HMAC-signed tokens — no server-side session table.
This node is single-process/single-machine with no load balancer to
share session state across, so a signed token that verifies itself is
strictly simpler than a sessions table (one less table, one less place
for a stale row to linger) and matches the "no workaround, root-cause"
brief: the ONLY state that needs to persist is the admin credential
itself (admin_auth, in local_db.py) and a per-install signing secret
(node_config.json's local_auth_secret, generated once on first boot).

Password hashing: PBKDF2-HMAC-SHA256, stdlib only (hashlib) — no new
dependency for a single local admin account.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Any, Callable

from flask import Blueprint, g, jsonify, request

from local_node import license_check
from local_node import local_db
from local_node.config_store import get_branch_id, get_org_id, load_config, save_config

auth_bp = Blueprint("auth", __name__, url_prefix="/api")

_PBKDF2_ITERATIONS = 200_000
_TOKEN_TTL_SECONDS = 60 * 60 * 12  # 12h — a workday plus slack; re-login next morning is fine for a single-admin machine


def _get_or_create_signing_secret() -> bytes:
    cfg = load_config()
    secret_hex = str(cfg.get("local_auth_secret") or "").strip()
    if secret_hex:
        return bytes.fromhex(secret_hex)
    secret_hex = secrets.token_hex(32)
    save_config({"local_auth_secret": secret_hex})
    return bytes.fromhex(secret_hex)


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """Returns (password_hash_hex, salt_hex). Pass salt_hex back in on
    verification; never on creation (a fresh salt is generated then)."""
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return digest.hex(), salt.hex()


def set_admin_password(branch_id: str, email: str, password: str, full_name: str = "") -> None:
    password_hash, salt_hex = hash_password(password)
    local_db.upsert_admin(branch_id, email, password_hash, salt_hex, full_name)


def verify_admin_password(branch_id: str, email: str, password: str) -> bool:
    record = local_db.get_admin_by_email(branch_id, email)
    if record is None:
        return False
    candidate_hash, _ = hash_password(password, record["password_salt"])
    # constant-time compare — this is exactly the kind of check timing
    # attacks target, even on a LAN-only endpoint.
    return hmac.compare_digest(candidate_hash, record["password_hash"])


def issue_token(branch_id: str, email: str) -> str:
    secret = _get_or_create_signing_secret()
    payload = {"branch_id": branch_id, "email": email, "iat": int(time.time())}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    signature = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def verify_token(token: str) -> dict[str, Any] | None:
    """Returns the decoded payload if the token is well-formed, correctly
    signed, and not expired; otherwise None. Never raises — every call
    site treats None as "not authenticated"."""
    if not token or "." not in token:
        return None
    payload_b64, _, signature = token.partition(".")
    secret = _get_or_create_signing_secret()
    expected = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode()).decode())
    except Exception:
        return None
    if time.time() - float(payload.get("iat") or 0) > _TOKEN_TTL_SECONDS:
        return None
    return payload


def token_from_request_headers(headers: Any) -> str | None:
    auth_header = headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


def check_login_and_license() -> Any:
    """Returns a Flask (jsonify_body, status_code) tuple to short-circuit
    the request if it should be blocked, or None if it may proceed. Single
    implementation shared by require_login (decorator, for routes
    registered directly on the Flask app) and dashboard_bp's
    before_request hook (for every blueprint route at once) — see each
    call site.

    On success, also stashes the verified token payload on flask.g so
    any route behind either guard can resolve "who am I" via
    get_current_admin() below without re-verifying the HMAC signature a
    second time — single source of truth for both "is this request
    authenticated" and "which admin is this."
    """
    blocked = license_check.blocked_response()
    if blocked is not None:
        return blocked
    token = token_from_request_headers(request.headers)
    payload = verify_token(token) if token else None
    if payload is None:
        return jsonify({"success": False, "message": "Not authenticated."}), 401
    g.local_auth_payload = payload
    return None


def get_current_admin() -> dict[str, Any] | None:
    """Returns the admin_auth row for the token that authenticated the
    CURRENT request, or None.

    Relies entirely on check_login_and_license having already run (via
    dashboard_bp.before_request, or the require_login decorator) and
    populated g.local_auth_payload — never re-verifies the token itself.
    Only call this from a route that sits behind one of those two
    guards; anywhere else, g.local_auth_payload won't exist and this
    always returns None.
    """
    payload = getattr(g, "local_auth_payload", None)
    if not payload:
        return None
    return local_db.get_admin_by_email(
        str(payload.get("branch_id") or ""), str(payload.get("email") or "")
    )


def validate_strong_password(password: str) -> None:
    """Single source of truth for this node's own password-strength
    policy. Mirrors the Client Dashboard's client-side checklist
    (ChangePasswordCard.tsx's PASSWORD_RULES) rule-for-rule so a password
    accepted there is never rejected here (and vice versa) — same
    contract as the cloud's support_db_client_users.validate_strong_password,
    reimplemented independently since this node has no dependency on the
    cloud codebase. Raises ValueError with a user-facing message on the
    first rule that fails.
    """
    pw = str(password or "")
    if len(pw) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not re.search(r"[A-Z]", pw):
        raise ValueError("Password must include at least one uppercase letter")
    if not re.search(r"[a-z]", pw):
        raise ValueError("Password must include at least one lowercase letter")
    if not re.search(r"[0-9]", pw):
        raise ValueError("Password must include at least one number")
    if not re.search(r"[^A-Za-z0-9]", pw):
        raise ValueError("Password must include at least one special character")


def _resolve_org_branch_names(cfg: dict[str, Any], branch_id: str) -> tuple[str, str]:
    """Single source of truth for the org/branch display-name fallback
    chain (node_config.json -> local dashboard_config cache -> hardcoded
    default). Extracted from api_login so build_dashboard_user_payload
    below can produce the exact same names on a post-password-change
    refresh as a fresh login would.
    """
    org_name = str(cfg.get("org_name") or cfg.get("organization_name") or "").strip()
    branch_name = str(cfg.get("branch_name") or cfg.get("branchName") or "").strip()
    if not org_name or org_name == "Local Organization":
        try:
            saved_d = local_db.get_dashboard_config(branch_id)
            saved_org = str(saved_d.get("organization_name") or saved_d.get("org_name") or "").strip()
            if saved_org and saved_org != "Local Organization":
                org_name = saved_org
            if not branch_name or branch_name == "Main Branch":
                saved_b = str(saved_d.get("branch_name") or "").strip()
                if saved_b and saved_b != "Main Branch":
                    branch_name = saved_b
        except Exception:
            pass
    return org_name, branch_name


def build_dashboard_user_payload(admin: dict[str, Any], branch_id: str) -> dict[str, Any]:
    """Single source of truth for the 'user' object shape returned to the
    Client Dashboard SPA. Used by /login and by dashboard_routes.py's
    GET /api/users/<id> (the refreshUser call AuthContext.tsx makes right
    after ChangePasswordCard.tsx's password change) so both responses
    normalise identically on the frontend (normaliseUser) — a
    post-password-change refresh can never drift from what a fresh
    login would have shown.
    """
    cfg = load_config()
    organization_id = get_org_id(cfg) or "local"
    org_name, branch_name = _resolve_org_branch_names(cfg, branch_id)
    org_status = license_check.current_status().get("org_status", "active")
    display_user_name = org_name or admin.get("full_name") or "Admin"

    return {
        "id": admin["id"],
        "email": admin["email"],
        "name": display_user_name,
        "full_name": admin.get("full_name") or display_user_name,
        "role": "admin",
        "organization_id": organization_id,
        "organization_name": org_name or "Local Organization",
        "org_name": org_name or "Local Organization",
        "branch_id": branch_id or "local-branch",
        "branch_name": branch_name or "Main Branch",
        "organization_status": org_status,
        "dashboard_ready": True,
        "requires_onboarding": False,
    }


def require_login(view: Callable) -> Callable:
    """Route decorator for individual routes. dashboard_bp uses a
    before_request hook instead (see dashboard_routes.py) so every route
    on that blueprint is covered without decorating each one — this
    decorator remains for any route registered directly on the app that
    isn't part of that blueprint."""
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        blocked = check_login_and_license()
        if blocked is not None:
            return blocked
        return view(*args, **kwargs)

    return wrapped


# ── Routes ────────────────────────────────────────────────────────────────────
# Deliberately NOT on dashboard_bp: /login must be reachable while logged
# out (obviously), and /session is what the frontend calls on every app
# boot to decide whether to show the login screen or the dashboard —
# both need to run their own, narrower check (license only, no token
# requirement) rather than dashboard_bp's before_request.

@auth_bp.post("/login")
def api_login():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or data.get("username") or "").strip().lower()
    password = str(data.get("password") or "").strip()
    if not email or not password:
        return jsonify({"success": False, "message": "Email and password required"}), 400

    # Same block-before-token-issuance behavior as the cloud's
    # /api/login (app.py's _org_login_blocked_response): an unlicensed org's
    # admin must never receive a working session, even momentarily.
    blocked = license_check.blocked_response()
    if blocked is not None:
        return blocked

    branch_id = get_branch_id(load_config())
    # Local mode has one branch and one admin account. On a fresh node, the
    # first login establishes that account; only the PBKDF2 hash and salt are
    # persisted in SQLite, never the submitted password.
    if not local_db.has_any_admin(branch_id):
        set_admin_password(branch_id, email, password, full_name=email.split("@", 1)[0])

    if not verify_admin_password(branch_id, email, password):
        return jsonify({"success": False, "message": "Invalid credentials"}), 401

    admin = local_db.get_admin_by_email(branch_id, email)
    token = issue_token(branch_id, email)
    user_payload = build_dashboard_user_payload(admin, branch_id)

    # Response shape matches the CLOUD /api/login contract exactly
    # (success/user/token/dashboard_ready/requires_onboarding/
    # organization_id/organization_status/message) so client-dashboard's
    # existing api.ts needs a base-URL change only, not a rewrite.
    return jsonify({
        "success": True,
        "user": user_payload,
        "token": token,
        "dashboard_ready": user_payload["dashboard_ready"],
        "requires_onboarding": user_payload["requires_onboarding"],
        "organization_id": user_payload["organization_id"],
        "organization_name": user_payload["organization_name"],
        "organization_status": user_payload["organization_status"],
        "message": "Login successful.",
    })


@auth_bp.get("/session")
def api_session():
    """Polled once on app boot so the SPA knows whether to render the
    login screen or the dashboard, without needing a full route hit that
    might 401 loudly in the browser console."""
    token = token_from_request_headers(request.headers)
    payload = verify_token(token) if token else None
    cfg = load_config()
    org_name = str(cfg.get("org_name") or cfg.get("organization_name") or "").strip()
    return jsonify({
        "authenticated": payload is not None,
        "organization_status": license_check.current_status().get("org_status", "active"),
        "organization_name": org_name or "Local Organization",
        "org_name": org_name or "Local Organization",
    })