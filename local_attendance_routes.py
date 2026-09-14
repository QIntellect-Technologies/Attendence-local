from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import jwt
from flask import Blueprint, Response, jsonify, request

from local_node import config_store, camera_config, local_db, live_events, recognition_engine
from local_node.camera_stream_manager import get_camera_stream_manager

# require_client_dashboard_auth already exists in app.py — import it from there
from app import require_client_dashboard_auth  # adjust if app.py's decorator lives elsewhere

local_attendance_bp = Blueprint("local_attendance", __name__)

STREAM_TOKEN_SECRET = os.environ.get("LOCAL_STREAM_TOKEN_SECRET")
if not STREAM_TOKEN_SECRET:
    raise RuntimeError("LOCAL_STREAM_TOKEN_SECRET must be set — do not default this in production")
STREAM_TOKEN_TTL_SECONDS = 60


def _branch_id() -> str:
    return config_store.get_branch_id() or "local-branch"


def _branch_name() -> str:
    return config_store.get_branch_name() or "Main Branch"


def _camera_runtime_config() -> dict:
    config = config_store.load_config()
    saved = local_db.get_dashboard_config(_branch_id())
    cameras = saved.get("cameras") if isinstance(saved, dict) else None
    if isinstance(cameras, list):
        return {**config, "cameras": cameras}
    if isinstance(cameras, dict):
        branch_cameras = cameras.get(_branch_id()) or cameras.get("1")
        if isinstance(branch_cameras, list):
            return {**config, "cameras": branch_cameras}
    return config


def local_node_startup_sync() -> None:
    """Call once at process start, and again whenever Settings saves a new
    camera config. Cameras run continuously once enabled — attendance
    capture must not depend on whether the dashboard tab is open."""
    cfg = _camera_runtime_config()
    cameras = camera_config.get_enabled_cameras(cfg)
    get_camera_stream_manager().sync_cameras(_branch_id(), cameras)


@local_attendance_bp.route("/api/cameras", methods=["GET"])
@require_client_dashboard_auth
def local_api_get_cameras():
    cfg = _camera_runtime_config()
    enabled = camera_config.get_enabled_cameras(cfg)
    live_status = {c["id"]: c for c in get_camera_stream_manager().list_cameras()}
    branch_id, branch_name = _branch_id(), _branch_name()

    return jsonify([
        {
            "id": cam["id"],
            "camera_name": cam["camera_name"],
            "location": cam.get("location", ""),
            "branch_id": branch_id,
            "branch_name": branch_name,
            "status": "Online" if live_status.get(cam["id"], {}).get("online") else "Offline",
            "last_seen": live_status.get(cam["id"], {}).get("last_frame_at"),
            "stream_path": f"/stream/{cam['id']}",
        }
        for cam in enabled
    ]), 200


@local_attendance_bp.route("/api/stats", methods=["GET"])
@require_client_dashboard_auth
def local_api_stats():
    branch_id = _branch_id()
    enrolled_count = len(local_db.get_embeddings_grouped_by_person(branch_id))

    # NOTE: UTC calendar day, not branch-local timezone. shift_gate has the
    # real branch-timezone logic (_branch_zone) but it's private — expose a
    # public helper there before this needs to be timezone-exact.
    today = datetime.now(timezone.utc).date().isoformat()
    rows = local_db.recent_attendance(branch_id, limit=1000, include_held=False)
    today_rows = [r for r in rows if str(r.get("marked_at", "")).startswith(today)]
    present_people = {(r.get("people_type"), r.get("person_code")) for r in today_rows}

    return jsonify({
        "enrolled_users": enrolled_count,
        "unique_users_today": len(present_people),
        "total_logs": len(today_rows),
    }), 200


@local_attendance_bp.route("/api/live-detections", methods=["GET"])
@require_client_dashboard_auth
def local_api_live_detections():
    branch_id, branch_name = _branch_id(), _branch_name()
    detections = [
        {
            "name": e["name"],
            "confidence": e["confidence"],
            "timestamp": e["marked_at"],
            "source": "camera",
            "user_id": e.get("staff_id"),
            "department": None,
            "camera_id": e.get("camera_id"),
            "branch_id": branch_id,
            "branch_name": branch_name,
            "face_crop": e.get("snapshot"),
        }
        for e in live_events.list_events(limit=100)
    ]
    return jsonify({"detections": detections}), 200


# ── stream start/stop ────────────────────────────────────────────────────
# Deliberate behavior change from the cloud version: attendance capture
# runs continuously once cameras are enabled (see local_node_startup_sync),
# not only while an admin has this page open — a person walking past a
# door must be marked whether or not anyone is watching the dashboard.
# Start/Stop here only control whether THIS browser tab is drawing tiles;
# they do not start or stop capture/detection server-side.

@local_attendance_bp.route("/api/stream/start", methods=["POST"])
@require_client_dashboard_auth
def local_api_stream_start():
    local_node_startup_sync()  # idempotent — safe even if already running
    cfg = _camera_runtime_config()
    ids = [c["id"] for c in camera_config.get_enabled_cameras(cfg)]
    return jsonify({"success": True, "started": ids}), 200


@local_attendance_bp.route("/api/stream/stop", methods=["POST"])
@require_client_dashboard_auth
def local_api_stream_stop():
    return jsonify({"success": True, "stopped": []}), 200


@local_attendance_bp.route("/api/stream/token", methods=["POST"])
@require_client_dashboard_auth
def local_api_stream_token():
    data = request.get_json(silent=True) or {}
    camera_id = str(data.get("camera_id") or "")
    if not camera_id:
        return jsonify({"error": "camera_id required"}), 400
    payload = {
        "camera_id": camera_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + STREAM_TOKEN_TTL_SECONDS,
    }
    token = jwt.encode(payload, STREAM_TOKEN_SECRET, algorithm="HS256")
    return jsonify({"success": True, "stream_token": token}), 200


@local_attendance_bp.route("/api/stream/<camera_id>", methods=["GET"])
def local_api_stream(camera_id):
    # No @require_client_dashboard_auth — an <img> tag can't send a Bearer
    # header, so the short-lived stream_token IS the auth here.
    token = request.args.get("stream_token", "")
    try:
        payload = jwt.decode(token, STREAM_TOKEN_SECRET, algorithms=["HS256"])
    except Exception:
        return jsonify({"error": "invalid or expired stream token"}), 401
    if payload.get("camera_id") != camera_id:
        return jsonify({"error": "token does not match camera"}), 401

    return Response(
        get_camera_stream_manager().mjpeg_frames(camera_id),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@local_attendance_bp.route("/api/init", methods=["POST"])
@require_client_dashboard_auth
def local_api_init():
    try:
        recognition_engine.warmup()
    except Exception as exc:
        return jsonify({"status": "error", "message": str(exc)}), 500
    return jsonify({"status": "ready"}), 200