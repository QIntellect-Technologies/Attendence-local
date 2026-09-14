from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

APP_NAME = "qintellect_attendance_node"

# Load local-node environment overrides from local_node/.env when running
# the node from source. This is useful for development setups that rely on
# .env values instead of setting them globally in the shell.
_dotenv_path = Path(__file__).resolve().parent / ".env"
if _dotenv_path.exists():
    load_dotenv(_dotenv_path)
DEFAULT_API_BASE_URL = os.getenv("QINTELLECT_API_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


def app_data_dir() -> Path:
    configured = os.getenv("QINTELLECT_NODE_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    if os.name == "nt":
        base = os.getenv("PROGRAMDATA") or str(Path.home())
        return Path(base) / "QIntellect" / "AttendanceNode"

    return Path.home() / f".{APP_NAME}"


APP_DIR = app_data_dir()
CONFIG_PATH = APP_DIR / "node_config.json"
STATUS_PATH = APP_DIR / "node_runtime_status.json"
LOG_DIR = APP_DIR / "logs"
DB_PATH = APP_DIR / "local_node.db"
MODELS_DIR = APP_DIR / "models"


def ensure_app_dirs() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)


def _restrict_file(path: Path) -> None:
    if os.name != "nt" and path.exists():
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_api_base(config: dict[str, Any]) -> str:
    return _clean_text(config.get("railway_api_base_url") or config.get("api_base_url") or DEFAULT_API_BASE_URL).rstrip("/")


def _normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(config or {})
    api_base = _normalize_api_base(normalized)
    org_id = _clean_text(
        normalized.get("organization_id")
        or normalized.get("org_id")
        or normalized.get("organizationId")
    )
    branch_id = _clean_text(normalized.get("branch_id") or normalized.get("branchId"))
    branch_name = _clean_text(
        normalized.get("branch_name")
        or normalized.get("branchName")
        or normalized.get("node_branch_name")
    )
    attendance_mode = _clean_text(normalized.get("attendance_mode") or "local").lower()

    env_match_threshold = os.getenv("MATCH_THRESHOLD")
    if env_match_threshold is not None and str(env_match_threshold).strip():
        match_threshold_raw = env_match_threshold
    else:
        match_threshold_raw = normalized.get("match_threshold")
        if match_threshold_raw is None:
            match_threshold_raw = 0.45
    try:
        match_threshold = float(match_threshold_raw)
    except Exception:
        match_threshold = 0.45
    match_threshold = max(0.0, min(1.0, match_threshold))

    normalized["railway_api_base_url"] = api_base
    normalized["api_base_url"] = api_base
    normalized["match_threshold"] = match_threshold
    if org_id:
        normalized["organization_id"] = org_id
        normalized["org_id"] = org_id
    if branch_id:
        normalized["branch_id"] = branch_id
    if branch_name:
        normalized["branch_name"] = branch_name
        normalized["branchName"] = branch_name
    if attendance_mode not in {"cloud", "local"}:
        attendance_mode = "local"
    normalized["attendance_mode"] = attendance_mode

    if "sync_delay_minutes" in normalized:
        try:
            normalized["sync_delay_minutes"] = max(0, int(normalized.get("sync_delay_minutes") or 0))
        except Exception:
            normalized["sync_delay_minutes"] = 0

    if "node_label" in normalized:
        normalized["node_label"] = _clean_text(normalized.get("node_label")) or None
    if "node_id" in normalized:
        normalized["node_id"] = _clean_text(normalized.get("node_id")) or None

    return normalized


def load_config() -> dict[str, Any]:
    ensure_app_dirs()
    if not CONFIG_PATH.exists():
        return _normalize_runtime_config({
            "railway_api_base_url": DEFAULT_API_BASE_URL,
            "api_base_url": DEFAULT_API_BASE_URL,
            "ui_port": int(os.getenv("QINTELLECT_NODE_UI_PORT", "8765")),
            "poll_interval_seconds": int(os.getenv("QINTELLECT_NODE_POLL_SECONDS", "30")),
        })

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
    except Exception:
        return {}

    data = _normalize_runtime_config(data)
    data["ui_port"] = int(data.get("ui_port") or os.getenv("QINTELLECT_NODE_UI_PORT", "8765"))
    data["poll_interval_seconds"] = int(data.get("poll_interval_seconds") or 30)
    data["sync_delay_minutes"] = int(data.get("sync_delay_minutes") or 0)
    if "match_threshold" not in data or data["match_threshold"] is None:
        data["match_threshold"] = float(os.getenv("MATCH_THRESHOLD", "0.45"))
    return data


def save_config(config: dict[str, Any]) -> None:
    ensure_app_dirs()
    existing = load_config()
    merged = _normalize_runtime_config({**existing, **dict(config or {})})
    CONFIG_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _restrict_file(CONFIG_PATH)


def get_runtime_identity(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _normalize_runtime_config(config or load_config())
    return {
        "api_base_url": cfg.get("api_base_url"),
        "railway_api_base_url": cfg.get("railway_api_base_url"),
        "organization_id": cfg.get("organization_id") or cfg.get("org_id"),
        "org_id": cfg.get("org_id") or cfg.get("organization_id"),
        "branch_id": cfg.get("branch_id"),
        "branch_name": cfg.get("branch_name") or cfg.get("branchName"),
        "attendance_mode": cfg.get("attendance_mode") or "local",
        "node_id": cfg.get("node_id"),
        "node_label": cfg.get("node_label"),
        "hostname": cfg.get("hostname"),
    }


# Fields the backend's build_node_config_payload() (support_db_organizations.py)
# is the single source of truth for. Every place that receives a response
# from the backend (initial activation via install_token OR license key,
# and the periodic license-status config refresh) must persist exactly
# this set the same way — previously activation.py and license_check.py
# each hand-rolled their own subset and drifted (contact_phone and
# enabled_staff_types were simply never saved by either).
_NODE_CONFIG_FIELDS = (
    "organization_name", "org_name", "contact_email", "contact_phone",
    "business_type", "primary_people_type", "enabled_staff_types",
    "vertical_config", "max_capacity", "max_users",
    "branch_id", "branch_name", "branch_location", "branch_max_staff_capacity",
)


def apply_node_config(response: dict[str, Any]) -> dict[str, Any]:
    """Extract the org/branch config fields from a backend response
    (activation or license-status refresh) and persist them into
    node_config.json. Returns the merged config that was saved.

    Fields absent from `response` are left untouched (a partial refresh —
    e.g. one that failed to look up the branch — must never null out
    previously-known good values).
    """
    updates = {
        field: response[field]
        for field in _NODE_CONFIG_FIELDS
        if response.get(field) is not None
    }
    if "branch_name" in updates:
        updates["branchName"] = updates["branch_name"]
    save_config(updates)
    return load_config()


def get_branch_name(config: dict[str, Any] | None = None) -> str:
    return _clean_text(get_runtime_identity(config).get("branch_name"))


def get_branch_id(config: dict[str, Any] | None = None) -> str:
    return _clean_text(get_runtime_identity(config).get("branch_id"))


def get_org_id(config: dict[str, Any] | None = None) -> str:
    identity = get_runtime_identity(config)
    return _clean_text(identity.get("organization_id") or identity.get("org_id"))


def get_attendance_mode(config: dict[str, Any] | None = None) -> str:
    return _clean_text(get_runtime_identity(config).get("attendance_mode") or "local").lower()


def get_api_base_url(config: dict[str, Any] | None = None) -> str:
    return _clean_text(get_runtime_identity(config).get("api_base_url") or DEFAULT_API_BASE_URL).rstrip("/")


def is_activated() -> bool:
    """Whether this install has completed activation — via EITHER path
    (install_token -> activation.activate_with_token, or a pasted license
    key -> license_check.activate_license) — and is ready to run the
    recognition/attendance pipeline.

    Deliberately does NOT check node_api_key: that field is install_token-
    only (see activation.py). claim_org_license() never mints one for the
    license-key path (the on-prem default — see support_db_licenses.py),
    so requiring it here would leave every license-key-activated install
    permanently unactivated. node_id + branch_id are the two fields both
    activation paths guarantee are set on success; see
    has_node_api_key() for the (separate, install_token-only) check that
    gates the legacy heartbeat/cloud-sync worker.
    """
    cfg = load_config()
    return bool(_clean_text(cfg.get("node_id")) and _clean_text(cfg.get("branch_id")))


def has_node_api_key(config: dict[str, Any] | None = None) -> bool:
    """True only for installs activated via the legacy install_token flow
    (activation.py), which is the sole source of node_api_key. Used to
    gate the heartbeat worker's cloud-sync calls — every one of which
    requires this key (see api_client.py's X-Node-Api-Key header) —
    without conflating "has a cloud-sync credential" with "is activated"
    (see is_activated())."""
    cfg = config if config is not None else load_config()
    return bool(_clean_text(cfg.get("node_api_key")))


def write_runtime_status(status: dict[str, Any]) -> None:
    ensure_app_dirs()
    STATUS_PATH.write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_runtime_status() -> dict[str, Any]:
    ensure_app_dirs()
    if not STATUS_PATH.exists():
        return {}
    try:
        value = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}