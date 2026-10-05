# from __future__ import annotations

# import os
# import subprocess
# import sys
# import tempfile
# import threading
# import time
# from pathlib import Path

# from flask import Flask, Response, jsonify, request, send_from_directory

# from local_node import local_db
# from local_node.activation import activate_with_token
# from local_node.camera_config import get_enabled_cameras
# from local_node.auth import auth_bp, set_admin_password
# from local_node.camera_stream_manager import get_camera_stream_manager
# from local_node.config_store import get_branch_id, get_branch_name, get_org_id, get_runtime_identity, is_activated, load_config, read_runtime_status
# from local_node.dashboard_routes import dashboard_bp
# from local_node.live_events import list_events, clear_events
# from local_node.node_service import NodeService
# from local_node.package_import import PackageImportError, parse_embedding_package
# from local_node import perf_stats
# _service = NodeService()
# _camera_manager = get_camera_stream_manager()
# from local_node import recognition_worker


# def _live_event_from_attendance(row: dict) -> dict:
#     check_out_at = row.get("check_out_marked_at")
#     checked_out = bool(check_out_at and row.get("check_out_confirmed"))
#     metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
#     return {
#         "id": row.get("local_event_id"),
#         "type": "attendance",
#         "name": row.get("staff_name") or row.get("person_code") or "Unknown",
#         "staff_id": row.get("person_code") or "",
#         "status": "checked_out" if checked_out else "checked_in",
#         "confidence": float(row.get("confidence") or 0),
#         "message": "Checked out." if checked_out else "Checked in.",
#         "marked_at": check_out_at if checked_out else row.get("marked_at"),
#         "check_out_marked_at": check_out_at,
#         "sync_status": row.get("sync_status") or "pending",
#         "camera_id": row.get("camera_id"),
#         "camera_name": metadata.get("camera_name"),
#         "notes": row.get("notes"),
#     }


# def _web_dist() -> Path:
#     """The legacy local_node_ui build — kept only as a support/diagnostic
#     surface (raw camera list, perf numbers) at /engine. The client never
#     opens this; see _dashboard_dist() for what actually loads at '/'.
#     Activation itself now happens inside the dashboard build's own
#     first-run setup screen (it calls the same /api/activate, /api/status
#     routes below — those are unchanged and unprefixed), so this legacy
#     build no longer needs to be reachable for day-one setup, only for
#     later troubleshooting."""
#     return Path(__file__).resolve().parent / "web" / "dist"


# def _dashboard_dist() -> Path:
#     """The client-dashboard build (Staff Management / Attendance View /
#     Live Monitoring, one nav). This is the default route — see create_app().
#     Built from src/ with VITE_DEPLOYMENT_MODE=local and copied here as
#     dashboard_web/dist by the build pipeline (see build/build.py and
#     build/build_pyinstaller.py's RUNTIME_DATA)."""
#     return Path(__file__).resolve().parent / "dashboard_web" / "dist"


# def _provision_admin_if_needed() -> None:
#     """First-boot only: creates the local admin account from the
#     installer-baked provisioning credential in node_config.json, then
#     clears the plaintext password from disk immediately — it must never
#     persist anywhere except as the admin_auth password_hash.

#     provisioning_admin_email / provisioning_admin_password are written
#     into node_config.json once, by the signed offline config bundle the
#     installer bakes in at build time (installer_packager.py) — not shown
#     here since that file wasn't part of what I reviewed; it needs the
#     matching change to actually populate these two keys per client.
#     """
#     branch_id = get_branch_id(load_config())
#     if not branch_id or local_db.has_any_admin(branch_id):
#         return
#     cfg = load_config()
#     email = str(cfg.get("provisioning_admin_email") or "").strip()
#     password = str(cfg.get("provisioning_admin_password") or "").strip()
#     if not email or not password:
#         return  # nothing to provision from yet (e.g. dev environment) — first login setup UI is the fallback, not built here
#     set_admin_password(branch_id, email, password, full_name=cfg.get("branch_name") or "")
#     from local_node.config_store import save_config
#     save_config({"provisioning_admin_password": ""})


# def _current_branch_id() -> str:
#     """Single source of truth for "which branch is this node's local
#     attendance data scoped to" — every attendance_buffer read/maintenance
#     call below must pass this, or it risks operating across every branch
#     that has ever shared this machine's SQLite file (see local_db.py's
#     _ensure_schema_migrations branch_id docstring for why that used to
#     happen)."""
#     return get_branch_id(load_config()) or "local-branch"


# def _render_perf_page(data: dict) -> str:
#     """Render the perf snapshot as a self-contained HTML page.

#     Deliberately plain: no build step, no bundle, no dependency on the
#     React app. This has to be readable on a node machine that may be
#     misbehaving, which is exactly when the normal UI is least trustworthy.
#     """
#     def esc(value) -> str:
#         return (
#             str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
#         )

#     process = data.get("process") or {}
#     machine_pct = process.get("cpu_percent_of_machine", 0)

#     camera_rows = "".join(
#         "<tr><td>{}</td><td>{}</td><td>{}x{}</td><td>{}</td><td>{}</td><td class='{}'>{}</td></tr>".format(
#             esc(cid), esc(info.get("name")),
#             esc(info.get("width")), esc(info.get("height")),
#             esc(info.get("megapixels")), esc(info.get("source_fps")),
#             "bad" if "software" in str(info.get("hw_acceleration", "")).lower() else "good",
#             esc(info.get("hw_acceleration")),
#         )
#         for cid, info in sorted((data.get("cameras") or {}).items())
#     ) or "<tr><td colspan='6'>No cameras streaming.</td></tr>"

#     stage_rows = "".join(
#         "<tr><td>{}</td><td>{}</td><td class='num'>{}</td><td class='num'>{}</td>"
#         "<td class='num'>{}</td><td class='num'>{}</td><td class='num'>{}</td></tr>".format(
#             esc(row["scope"]), esc(row["stage"]),
#             esc(row["busy_percent_of_one_core"]), esc(row["avg_ms"]),
#             esc(row["max_ms"]), esc(row["per_second"]), esc(row["count"]),
#         )
#         for row in (data.get("stages") or [])
#     ) or "<tr><td colspan='7'>No samples yet.</td></tr>"

#     measurement_rows = "".join(
#         "<tr><td>{}</td><td>{}</td><td class='num'>{}</td><td class='num'>{}</td>"
#         "<td class='num'>{}</td><td class='num'>{}</td></tr>".format(
#             esc(row["scope"]), esc(row["stage"]), esc(row["min"]),
#             esc(row["avg"]), esc(row["max"]), esc(row["count"]),
#         )
#         for row in (data.get("measurements") or [])
#     ) or "<tr><td colspan='6'>No measurements yet.</td></tr>"

#     thread_rows = "".join(
#         "<tr><td>{}</td><td class='num'>{}</td><td class='num'>{}</td></tr>".format(
#             esc(row["thread"]), esc(row["cpu_percent_of_one_core"]), esc(row["cpu_seconds_total"]),
#         )
#         for row in (data.get("threads") or [])
#         if row["cpu_percent_of_one_core"] > 0.05
#     ) or "<tr><td colspan='3'>Per-thread CPU unavailable (non-Windows, or first sample).</td></tr>"

#     return """<!doctype html>
# <html><head><meta charset="utf-8"><title>Node performance</title>
# <meta http-equiv="refresh" content="5">
# <style>
#  body{{font:13px/1.5 Segoe UI,system-ui,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}}
#  h1{{font-size:18px;margin:0 0 4px}} h2{{font-size:14px;margin:28px 0 8px;color:#58a6ff}}
#  p.sub{{color:#8b949e;margin:0 0 20px}}
#  table{{border-collapse:collapse;width:100%;margin-bottom:8px}}
#  th,td{{text-align:left;padding:5px 10px;border-bottom:1px solid #21262d}}
#  th{{color:#8b949e;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}}
#  td.num{{text-align:right;font-variant-numeric:tabular-nums}}
#  .big{{font-size:30px;font-weight:700;color:#e6edf3}}
#  .good{{color:#3fb950}} .bad{{color:#f85149;font-weight:600}}
#  .note{{color:#8b949e;font-size:12px;max-width:70ch;margin:6px 0 0}}
# </style></head><body>
# <h1>Node performance</h1>
# <p class="sub">Window: {window}s &middot; {cpus} logical CPUs &middot; auto-refreshing every 5s</p>

# <div class="big">{machine}% of this machine</div>
# <p class="note">This is what the node process alone consumes. Compare it against Task
# Manager's total: whatever gap remains belongs to other processes and cannot be
# fixed in this codebase.</p>

# <h2>Cameras</h2>
# <table><tr><th>ID</th><th>Name</th><th>Resolution</th><th>MP</th><th>Source FPS</th><th>Hardware decode</th></tr>
# {cameras}</table>
# <p class="note">Every frame a camera sends is decoded, whatever STREAM_FPS_LIMIT is set
# to &mdash; that limit only throttles display and detection, not decode. Resolution x
# source FPS is therefore the decode bill. "software" here means the CPU is doing it.</p>

# <h2>Pipeline stages</h2>
# <table><tr><th>Camera</th><th>Stage</th><th>% of one core</th><th>Avg ms</th><th>Max ms</th><th>Per sec</th><th>Count</th></tr>
# {stages}</table>
# <p class="note">"% of one core" is time spent in that stage over the window. Stages that
# block on I/O accumulate wall time without CPU &mdash; cross-check against the thread
# table below before concluding a stage is expensive.</p>

# <h2>Measurements</h2>
# <table><tr><th>Camera</th><th>Metric</th><th>Min</th><th>Avg</th><th>Max</th><th>Count</th></tr>
# {measurements}</table>
# <p class="note">Raw values, not timings. <b>detector.motion_score</b> is the number
# compared against MOTION_DIFF_THRESHOLD (currently 12.0): min is the noise floor,
# max is the strongest real activity seen. If max never approaches the threshold, the
# motion gate is not firing and every detection pass is coming from the 1.5s idle
# re-check instead. <b>reader.frames_not_consumed</b> is how many frames per
# iteration the camera offered that the reader never asked for: above zero means the
# reader is running behind the stream, and lowering RTSP_BUFFER_FLUSH_GRABS would
# make it decode MORE, not less.</p>

# <h2>Threads (CPU actually charged by the OS)</h2>
# <table><tr><th>Thread</th><th>% of one core</th><th>Total CPU sec</th></tr>
# {threads}</table>
# <p class="note">This is ground truth from the Windows scheduler, not instrumentation.
# camera-reader-* threads high here means decode; camera-detector-* means the face
# model; camera-processor-* means frame copying and JPEG encoding.</p>
# </body></html>""".format(
#         window=data.get("window_seconds"), cpus=data.get("logical_cpus"),
#         machine=machine_pct, cameras=camera_rows, stages=stage_rows,
#         measurements=measurement_rows, threads=thread_rows,
#     )


# def create_app() -> Flask:
#     app = Flask(__name__, static_folder=None)  # disable Flask's implicit static route entirely
#     local_db.init_db()
#     _provision_admin_if_needed()
#     _service.start()

#     # dashboard_bp (staff/attendance/settings/backup) requires login + an
#     # active/grace_period license on every request (see its own
#     # before_request hook). auth_bp (/api/login, /api/session) does not,
#     # by necessity. Neither blueprint touches the routes below —
#     # camera/status/activation/restart/perf keep working even while the
#     # dashboard is logged out or the org is suspended, so recognition and
#     # attendance capture are never interrupted by a billing issue; only
#     # the client's ability to VIEW/manage the dashboard is.
#     app.register_blueprint(auth_bp)
#     app.register_blueprint(dashboard_bp)

#     @app.get("/api/license/status")
#     def api_license_status():
#         from local_node import license_check
#         return jsonify({"success": True, **license_check.current_status()})

#     @app.post("/api/license/activate")
#     def api_license_activate():
#         from local_node import license_check
#         data = request.get_json(silent=True) or {}
#         token = str(
#             data.get("license_key")
#             or data.get("token")
#             or data.get("license_token")
#             or data.get("license")
#             or ""
#         ).strip()
#         try:
#             result = license_check.activate_license(token)
#             return jsonify(result)
#         except Exception as exc:
#             return jsonify({"success": False, "message": str(exc), "error": str(exc)}), 400

#     @app.get("/api/status")
#     def api_status():
#         cfg = get_runtime_identity(load_config())
#         runtime = read_runtime_status()
#         return jsonify({
#             "success": True,
#             "activated": is_activated(),
#             "node_id": cfg.get("node_id"),
#             "org_id": cfg.get("org_id") or cfg.get("organization_id"),
#             "branch_id": cfg.get("branch_id"),
#             "branch_name": cfg.get("branch_name"),
#             "attendance_mode": cfg.get("attendance_mode"),
#             "hostname": cfg.get("hostname"),
#             "runtime": runtime,
#         })

#     @app.post("/api/activate")
#     def api_activate():
#         data = request.get_json(silent=True) or {}
#         try:
#             config = activate_with_token(
#                 api_base_url=str(data.get("api_base_url") or ""),
#                 install_token=str(data.get("install_token") or ""),
#                 node_label=str(data.get("node_label") or "") or None,
#             )
#             return jsonify({"success": True, "config": config})
#         except Exception as exc:
#             return jsonify({"success": False, "message": str(exc)}), 400

#     @app.get("/api/live-events")
#     def api_live_events():
#         branch_id = _current_branch_id()
#         attendance = local_db.recent_attendance(
#             branch_id, 100, include_held=True, current_shift_only=True,
#         )
#         enrolled_count = len(local_db.list_staff(branch_id))
#         visible_ids = {row.get("local_event_id") for row in attendance}
#         events_by_id = {
#             event.get("id"): event
#             for event in list_events(100)
#             if event.get("id") in visible_ids
#         }
#         for row in attendance:
#             event = _live_event_from_attendance(row)
#             previous = events_by_id.get(event["id"])
#             events_by_id[event["id"]] = {**previous, **event} if previous else event
#         events = list(events_by_id.values())
#         events.sort(key=lambda event: str(event.get("marked_at") or ""), reverse=True)
#         return jsonify({
#             "success": True,
#             "events": events[:100],
#             "attendance": attendance,
#             "enrolled_count": enrolled_count,
#         })

#     @app.get("/api/cameras")
#     def api_cameras():
#         if not _camera_manager.list_cameras():
#             cfg = load_config()
#             branch_id = get_branch_id(cfg) or "local-branch"
#             saved = local_db.get_dashboard_config(branch_id) if branch_id else {}
#             configured = saved.get("cameras") if isinstance(saved, dict) else None
#             if isinstance(configured, dict):
#                 configured = configured.get(branch_id) or configured.get("1")
#             if not isinstance(configured, list):
#                 configured = cfg.get("cameras")
#             if isinstance(configured, list):
#                 cameras = get_enabled_cameras({"cameras": configured})
#                 if cameras:
#                     _camera_manager.sync_cameras(branch_id or "local-branch", cameras)
#         return jsonify({"success": True, "cameras": _camera_manager.list_cameras()})

#     @app.get("/api/camera-stream/<camera_id>")
#     def api_camera_stream(camera_id: str):
#         return Response(
#             _camera_manager.mjpeg_frames(camera_id),
#             mimetype="multipart/x-mixed-replace; boundary=frame",
#         )
    
    
#     @app.get("/api/perf")
#     def api_perf():
#         """Where this process's CPU actually goes, split by pipeline stage
#         and by OS thread. Read-only; changes nothing.

#         Pass ?reset=1 to start a fresh measurement window — call it once,
#         wait 30-60 seconds with the system in its normal state, then call
#         again without reset to read that window. Thread percentages are
#         always deltas since the previous call, so the FIRST call after
#         startup reports lifetime averages and every later one reports the
#         interval since the last.
#         """
#         reset = str(request.args.get("reset") or "").strip().lower() in {"1", "true", "yes"}
#         return jsonify({"success": True, "perf": perf_stats.snapshot(reset=reset)})

#     @app.get("/perf")
#     def perf_page():
#         """Plain HTML rendering of /api/perf, so the numbers can be read on
#         the node machine itself without a JSON viewer or devtools. Auto
#         refreshes, which also keeps the thread-CPU deltas flowing."""
#         return Response(_render_perf_page(perf_stats.snapshot()), mimetype="text/html")

#     @app.post("/api/run-cycle")
#     def api_run_cycle():
#         try:
#             return jsonify({"success": True, "status": _service.run_cycle()})
#         except Exception as exc:
#             return jsonify({"success": False, "message": str(exc)}), 400

#     # No cloud attendance sync and no held-for-review workflow anymore —
#     # attendance is check-in/checkout only, resolved atomically at write
#     # time by local_db.record_attendance_local. /api/sync-attendance and
#     # every /api/held-attendance* route (list, mark-late, mark-half-day,
#     # mark-short-leave, mark-overtime, mark-early-left, mark-*-checkin,
#     # sync, delete) were removed along with NodeService's matching
#     # wrapper methods — there is nothing left for an operator to review or
#     # flush; a row is final the moment it's written.

#     @app.post("/api/clear-today-attendance")
#     def api_clear_today_attendance():
#         """Maintenance/testing action: wipes today's attendance_buffer rows
#         (pending and already-synced-to-backup alike), clears the
#         live-events feed, and resets the per-camera dedupe throttle so a
#         cleared person is eligible to be re-detected on the very next
#         frame instead of waiting out DUPLICATE_LOG_SECONDS. This does not
#         undo anything already pushed by the weekly backup — it only resets
#         this node's local view of today."""
#         try:
#             cleared = local_db.clear_today_attendance(_current_branch_id())
#             clear_events()
#             _camera_manager.clear_person_throttles()
#             return jsonify({"success": True, "cleared_count": cleared})
#         except Exception as exc:
#             return jsonify({"success": False, "message": str(exc)}), 400

#     @app.post("/api/import-embeddings")
#     def api_import_embeddings():
#         uploaded = request.files.get("package")
#         if uploaded is None or not uploaded.filename:
#             return jsonify({"success": False, "message": "No package file uploaded."}), 400

#         cfg = get_runtime_identity(load_config())
#         saved_config = local_db.get_dashboard_config(
#             str(cfg.get("branch_id") or "local-branch")
#         )
#         branch_id = str(cfg.get("branch_id") or "local-branch")
#         branch_name = str(
#             cfg.get("branch_name")
#             or saved_config.get("branch_name")
#             or "Main Branch"
#         )

#         with tempfile.TemporaryDirectory() as tmp_dir:
#             tmp_path = Path(tmp_dir) / "package.zip"
#             uploaded.save(tmp_path)
#             try:
#                 package = parse_embedding_package(tmp_path)
#             except PackageImportError as exc:
#                 return jsonify({"success": False, "message": str(exc)}), 400

#             package_branch_label = str(package.get("branch_label") or "").strip()
#             if branch_name and package_branch_label and package_branch_label != branch_name:
#                 return jsonify({
#                     "success": False,
#                     "message": (
#                         f"Package branch label '{package_branch_label}' does not match this node branch '{branch_name}'."
#                     ),
#                 }), 400

#             result = local_db.import_embedding_package(
#                 branch_id=branch_id,
#                 package_id=package["package_id"],
#                 branch_label=package["branch_label"],
#                 generated_at=package["generated_at"],
#                 records=package["records"],
#             )

#             recognition_worker.invalidate_cache() 

#         return jsonify({
#             "success": True,
#             "branch_label": package["branch_label"],
#             "generated_at": package["generated_at"],
#             "source_csv_name": package.get("source_csv_name"),
#             "source_csv_sha256": package.get("source_csv_sha256"),
#             **result,
#         })

#     @app.post("/api/restart")
#     def api_restart():
#         """Hard-restarts the node: launches a replacement instance of this
#         same exe, then kills this one outright with no cleanup — the same
#         effect as ending it from Task Manager and starting it again. The
#         replacement gets --relaunch (retry the single-instance lock) and
#         --no-browser (don't pop a second tab; the frontend polls and
#         reloads the existing one instead)."""
#         def _self_relaunch_command() -> list[str]:
#             # Frozen/Nuitka build: sys.executable IS the compiled exe itself,
#             # invoked directly. Dev environment (`python -m local_node.main`):
#             # sys.executable is the Python interpreter, and re-invoking with
#             # sys.argv[0] (main.py's bare file path) runs it OUTSIDE the
#             # local_node package context — every `from local_node.xxx
#             # import ...` in the codebase then fails at import time, and
#             # because the replacement is launched detached (no console),
#             # that failure is completely silent: it just dies instantly and
#             # nothing comes back. Re-invoking the same way (-m local_node.main)
#             # avoids that entirely.
#             if getattr(sys, "frozen", False) or "__compiled__" in globals():
#                 return [sys.executable]
#             return [sys.executable, "-m", "local_node.main"]

#         def _do_restart() -> None:
#             time.sleep(0.5)  # let this response actually reach the browser first
#             try:
#                 subprocess.Popen(
#                     _self_relaunch_command() + ["--no-browser", "--relaunch"],
#                     # Repo root, not wherever this process's cwd happens to be —
#                     # required for `-m local_node.main` to resolve the package
#                     # regardless of how/where the original process was started.
#                     cwd=str(Path(__file__).resolve().parent.parent),
#                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
#                     close_fds=True,
#                 )
#             finally:
#                 os._exit(1)  # hard kill — matches "End Task", no shutdown hooks run

#         threading.Thread(target=_do_restart, daemon=True).start()
#         return jsonify({"success": True, "message": "Restarting node..."})

#     @app.get("/api/import-history")
#     def api_import_history():
#         return jsonify({"success": True, "history": local_db.import_history(20)})

#     def _serve_spa(dist: Path, build_hint: str):
#         index_file = dist / "index.html"
#         if index_file.exists():
#             return send_from_directory(dist, "index.html")
#         return f"Build not found. Run: {build_hint}", 200

#     def _serve_spa_asset(dist: Path, path: str, build_hint: str):
#         target = dist / path
#         if target.exists() and target.is_file():
#             return send_from_directory(dist, path)
#         return _serve_spa(dist, build_hint)

#     # Default route: the client-dashboard SPA (Staff Management,
#     # Attendance View, Live Monitoring — one nav bar). This is what the
#     # client actually opens; main.py's webbrowser.open() points here.
#     @app.get("/")
#     def index():
#         return _serve_spa(_dashboard_dist(), "cd src && npm run build:local")

#     # Legacy technical UI (raw camera list, activation debug, perf link) —
#     # kept for support/troubleshooting only, not part of the client's nav.
#     @app.get("/engine")
#     def engine_index():
#         return _serve_spa(_web_dist(), "cd local_node/local_node_ui && npm run build")

#     @app.get("/engine/<path:path>")
#     def engine_spa(path: str):
#         return _serve_spa_asset(_web_dist(), path, "cd local_node/local_node_ui && npm run build")

#     # Catch-all SPA fallback for the dashboard build's own client-side
#     # routes (e.g. /staff, /attendance, /live-monitoring) and static
#     # assets (JS/CSS chunks) — MUST be registered last so it never shadows
#     # /api/*, /engine, or /perf above.
#     @app.get("/<path:path>")
#     def spa(path: str):
#         return _serve_spa_asset(_dashboard_dist(), path, "cd src && npm run build:local")

#     return app


from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from local_node import local_db
from local_node.activation import activate_with_token
from local_node.camera_config import get_enabled_cameras
from local_node.auth import auth_bp, set_admin_password
from local_node.camera_stream_manager import get_camera_stream_manager
from local_node.config_store import get_branch_id, get_branch_name, get_org_id, get_runtime_identity, is_activated, load_config, read_runtime_status
from local_node.dashboard_routes import dashboard_bp
from local_node.live_events import list_events, clear_events
from local_node.node_service import NodeService
from local_node.package_import import PackageImportError, parse_embedding_package
from local_node import perf_stats
_service = NodeService()
_camera_manager = get_camera_stream_manager()
from local_node import recognition_worker

def _live_event_from_attendance(row: dict) -> dict:
    check_out_at = row.get("check_out_marked_at")
    checked_out = bool(check_out_at and row.get("check_out_confirmed"))
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "id": row.get("local_event_id"),
        "type": "attendance",
        "name": row.get("staff_name") or row.get("person_code") or "Unknown",
        "staff_id": row.get("person_code") or "",
        "status": "checked_out" if checked_out else "checked_in",
        "confidence": float(row.get("confidence") or 0),
        "message": "Checked out." if checked_out else "Checked in.",
        "marked_at": check_out_at if checked_out else row.get("marked_at"),
        "check_out_marked_at": check_out_at,
        "sync_status": row.get("sync_status") or "pending",
        "camera_id": row.get("camera_id"),
        "camera_name": metadata.get("camera_name"),
        "notes": row.get("notes"),
        # Late check-in signal — see local_db.record_attendance_local's
        # check-in branch. Must be forwarded as-is (not collapsed into
        # `status`, which stays "checked_in" either way) or the Live
        # Attendance Marker page's "late" badge (DetectionCard.tsx's
        # isLateCheckIn) can never fire, since it reads this exact key.
        "check_in_hold_reason": row.get("check_in_hold_reason"),
        # Out-of-window/unconfirmed checkout reason ('early' | 'late' |
        # None) — only meaningful while checked_out is False. Included so
        # a future checkout badge on this page has the same signal
        # _shape_attendance_row already exposes to the Attendance View
        # page as checkOutHoldReason.
        "check_out_hold_reason": None if checked_out else row.get("check_out_hold_reason"),
    }


def _web_dist() -> Path:
    """The legacy local_node_ui build — kept only as a support/diagnostic
    surface (raw camera list, perf numbers) at /engine. The client never
    opens this; see _dashboard_dist() for what actually loads at '/'.
    Activation itself now happens inside the dashboard build's own
    first-run setup screen (it calls the same /api/activate, /api/status
    routes below — those are unchanged and unprefixed), so this legacy
    build no longer needs to be reachable for day-one setup, only for
    later troubleshooting."""
    return Path(__file__).resolve().parent / "web" / "dist"


def _dashboard_dist() -> Path:
    """The client-dashboard build (Staff Management / Attendance View /
    Live Monitoring, one nav). This is the default route — see create_app().
    Built from src/ with VITE_DEPLOYMENT_MODE=local and copied here as
    dashboard_web/dist by the build pipeline (see build/build.py and
    build/build_pyinstaller.py's RUNTIME_DATA)."""
    return Path(__file__).resolve().parent / "dashboard_web" / "dist"


def _provision_admin_if_needed() -> None:
    """First-boot only: creates the local admin account from the
    installer-baked provisioning credential in node_config.json, then
    clears the plaintext password from disk immediately — it must never
    persist anywhere except as the admin_auth password_hash.

    provisioning_admin_email / provisioning_admin_password are written
    into node_config.json once, by the signed offline config bundle the
    installer bakes in at build time (installer_packager.py) — not shown
    here since that file wasn't part of what I reviewed; it needs the
    matching change to actually populate these two keys per client.
    """
    branch_id = get_branch_id(load_config())
    if not branch_id or local_db.has_any_admin(branch_id):
        return
    cfg = load_config()
    email = str(cfg.get("provisioning_admin_email") or "").strip()
    password = str(cfg.get("provisioning_admin_password") or "").strip()
    if not email or not password:
        return  # nothing to provision from yet (e.g. dev environment) — first login setup UI is the fallback, not built here
    set_admin_password(branch_id, email, password, full_name=cfg.get("branch_name") or "")
    from local_node.config_store import save_config
    save_config({"provisioning_admin_password": ""})


def _current_branch_id() -> str:
    """Single source of truth for "which branch is this node's local
    attendance data scoped to" — every attendance_buffer read/maintenance
    call below must pass this, or it risks operating across every branch
    that has ever shared this machine's SQLite file (see local_db.py's
    _ensure_schema_migrations branch_id docstring for why that used to
    happen)."""
    return get_branch_id(load_config()) or "local-branch"


def _render_perf_page(data: dict) -> str:
    """Render the perf snapshot as a self-contained HTML page.

    Deliberately plain: no build step, no bundle, no dependency on the
    React app. This has to be readable on a node machine that may be
    misbehaving, which is exactly when the normal UI is least trustworthy.
    """
    def esc(value) -> str:
        return (
            str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    process = data.get("process") or {}
    machine_pct = process.get("cpu_percent_of_machine", 0)

    camera_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}x{}</td><td>{}</td><td>{}</td><td class='{}'>{}</td></tr>".format(
            esc(cid), esc(info.get("name")),
            esc(info.get("width")), esc(info.get("height")),
            esc(info.get("megapixels")), esc(info.get("source_fps")),
            "bad" if "software" in str(info.get("hw_acceleration", "")).lower() else "good",
            esc(info.get("hw_acceleration")),
        )
        for cid, info in sorted((data.get("cameras") or {}).items())
    ) or "<tr><td colspan='6'>No cameras streaming.</td></tr>"

    stage_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td class='num'>{}</td><td class='num'>{}</td>"
        "<td class='num'>{}</td><td class='num'>{}</td><td class='num'>{}</td></tr>".format(
            esc(row["scope"]), esc(row["stage"]),
            esc(row["busy_percent_of_one_core"]), esc(row["avg_ms"]),
            esc(row["max_ms"]), esc(row["per_second"]), esc(row["count"]),
        )
        for row in (data.get("stages") or [])
    ) or "<tr><td colspan='7'>No samples yet.</td></tr>"

    measurement_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td class='num'>{}</td><td class='num'>{}</td>"
        "<td class='num'>{}</td><td class='num'>{}</td></tr>".format(
            esc(row["scope"]), esc(row["stage"]), esc(row["min"]),
            esc(row["avg"]), esc(row["max"]), esc(row["count"]),
        )
        for row in (data.get("measurements") or [])
    ) or "<tr><td colspan='6'>No measurements yet.</td></tr>"

    thread_rows = "".join(
        "<tr><td>{}</td><td class='num'>{}</td><td class='num'>{}</td></tr>".format(
            esc(row["thread"]), esc(row["cpu_percent_of_one_core"]), esc(row["cpu_seconds_total"]),
        )
        for row in (data.get("threads") or [])
        if row["cpu_percent_of_one_core"] > 0.05
    ) or "<tr><td colspan='3'>Per-thread CPU unavailable (non-Windows, or first sample).</td></tr>"

    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Node performance</title>
<meta http-equiv="refresh" content="5">
<style>
 body{{font:13px/1.5 Segoe UI,system-ui,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:24px}}
 h1{{font-size:18px;margin:0 0 4px}} h2{{font-size:14px;margin:28px 0 8px;color:#58a6ff}}
 p.sub{{color:#8b949e;margin:0 0 20px}}
 table{{border-collapse:collapse;width:100%;margin-bottom:8px}}
 th,td{{text-align:left;padding:5px 10px;border-bottom:1px solid #21262d}}
 th{{color:#8b949e;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}}
 td.num{{text-align:right;font-variant-numeric:tabular-nums}}
 .big{{font-size:30px;font-weight:700;color:#e6edf3}}
 .good{{color:#3fb950}} .bad{{color:#f85149;font-weight:600}}
 .note{{color:#8b949e;font-size:12px;max-width:70ch;margin:6px 0 0}}
</style></head><body>
<h1>Node performance</h1>
<p class="sub">Window: {window}s &middot; {cpus} logical CPUs &middot; auto-refreshing every 5s</p>

<div class="big">{machine}% of this machine</div>
<p class="note">This is what the node process alone consumes. Compare it against Task
Manager's total: whatever gap remains belongs to other processes and cannot be
fixed in this codebase.</p>

<h2>Cameras</h2>
<table><tr><th>ID</th><th>Name</th><th>Resolution</th><th>MP</th><th>Source FPS</th><th>Hardware decode</th></tr>
{cameras}</table>
<p class="note">Every frame a camera sends is decoded, whatever STREAM_FPS_LIMIT is set
to &mdash; that limit only throttles display and detection, not decode. Resolution x
source FPS is therefore the decode bill. "software" here means the CPU is doing it.</p>

<h2>Pipeline stages</h2>
<table><tr><th>Camera</th><th>Stage</th><th>% of one core</th><th>Avg ms</th><th>Max ms</th><th>Per sec</th><th>Count</th></tr>
{stages}</table>
<p class="note">"% of one core" is time spent in that stage over the window. Stages that
block on I/O accumulate wall time without CPU &mdash; cross-check against the thread
table below before concluding a stage is expensive.</p>

<h2>Measurements</h2>
<table><tr><th>Camera</th><th>Metric</th><th>Min</th><th>Avg</th><th>Max</th><th>Count</th></tr>
{measurements}</table>
<p class="note">Raw values, not timings. <b>detector.motion_score</b> is the number
compared against MOTION_DIFF_THRESHOLD (currently 12.0): min is the noise floor,
max is the strongest real activity seen. If max never approaches the threshold, the
motion gate is not firing and every detection pass is coming from the 1.5s idle
re-check instead. <b>reader.frames_not_consumed</b> is how many frames per
iteration the camera offered that the reader never asked for: above zero means the
reader is running behind the stream, and lowering RTSP_BUFFER_FLUSH_GRABS would
make it decode MORE, not less.</p>

<h2>Threads (CPU actually charged by the OS)</h2>
<table><tr><th>Thread</th><th>% of one core</th><th>Total CPU sec</th></tr>
{threads}</table>
<p class="note">This is ground truth from the Windows scheduler, not instrumentation.
camera-reader-* threads high here means decode; camera-detector-* means the face
model; camera-processor-* means frame copying and JPEG encoding.</p>
</body></html>""".format(
        window=data.get("window_seconds"), cpus=data.get("logical_cpus"),
        machine=machine_pct, cameras=camera_rows, stages=stage_rows,
        measurements=measurement_rows, threads=thread_rows,
    )


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)  # disable Flask's implicit static route entirely
    local_db.init_db()
    _provision_admin_if_needed()
    _service.start()

    # dashboard_bp (staff/attendance/settings/backup) requires login + an
    # active/grace_period license on every request (see its own
    # before_request hook). auth_bp (/api/login, /api/session) does not,
    # by necessity. Neither blueprint touches the routes below —
    # camera/status/activation/restart/perf keep working even while the
    # dashboard is logged out or the org is suspended, so recognition and
    # attendance capture are never interrupted by a billing issue; only
    # the client's ability to VIEW/manage the dashboard is.
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    @app.get("/api/license/status")
    def api_license_status():
        from local_node import license_check
        return jsonify({"success": True, **license_check.current_status()})

    @app.post("/api/license/activate")
    def api_license_activate():
        from local_node import license_check
        data = request.get_json(silent=True) or {}
        token = str(
            data.get("license_key")
            or data.get("token")
            or data.get("license_token")
            or data.get("license")
            or ""
        ).strip()
        try:
            result = license_check.activate_license(token)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc), "error": str(exc)}), 400

    @app.get("/api/status")
    def api_status():
        cfg = get_runtime_identity(load_config())
        runtime = read_runtime_status()
        return jsonify({
            "success": True,
            "activated": is_activated(),
            "node_id": cfg.get("node_id"),
            "org_id": cfg.get("org_id") or cfg.get("organization_id"),
            "branch_id": cfg.get("branch_id"),
            "branch_name": cfg.get("branch_name"),
            "attendance_mode": cfg.get("attendance_mode"),
            "hostname": cfg.get("hostname"),
            "runtime": runtime,
        })

    @app.post("/api/activate")
    def api_activate():
        data = request.get_json(silent=True) or {}
        try:
            config = activate_with_token(
                api_base_url=str(data.get("api_base_url") or ""),
                install_token=str(data.get("install_token") or ""),
                node_label=str(data.get("node_label") or "") or None,
            )
            return jsonify({"success": True, "config": config})
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

    @app.get("/api/live-events")
    def api_live_events():
        branch_id = _current_branch_id()
        enrolled_count = len(local_db.list_staff(branch_id))
        # The feed is one card per enrolled person per day (upsert-by-id —
        # see live_events.publish_event), so enrolled_count is the true
        # ceiling of distinct cards that could ever legitimately exist for
        # "today". A flat 100 silently dropped people once a branch grew
        # past that headcount: recent_attendance's current_shift_only path
        # truncates its Python-side filtered list to this same limit, so
        # everyone past the cutoff never even made it into `visible_ids`
        # and their card vanished from the Live Attendance feed entirely.
        # Keep a 100 floor so small branches don't lose the buffer they'd
        # want if e.g. someone checks in/out multiple times in a day.
        feed_limit = max(enrolled_count, 100)
        attendance = local_db.recent_attendance(
            branch_id, feed_limit, include_held=True, current_shift_only=True,
        )
        visible_ids = {row.get("local_event_id") for row in attendance}
        events_by_id = {
            event.get("id"): event
            for event in list_events(feed_limit)
            if event.get("id") in visible_ids
        }
        for row in attendance:
            # A manual/office-correction entry (Add Attendance, or an edit
            # that stamps correction_source='manual') never went through
            # camera detection — record_attendance_manual writes it with
            # source='manual_override', unlike the camera path's default
            # source='camera' (see local_db.py). Surfacing it here would
            # make the Live Staff Monitoring "Detections" feed show a card
            # for something no camera ever saw.
            if row.get("source") == "manual_override":
                continue
            event = _live_event_from_attendance(row)
            previous = events_by_id.get(event["id"])
            events_by_id[event["id"]] = {**previous, **event} if previous else event
        events = list(events_by_id.values())
        events.sort(key=lambda event: str(event.get("marked_at") or ""), reverse=True)
        return jsonify({
            "success": True,
            "events": events[:feed_limit],
            "attendance": attendance,
            "enrolled_count": enrolled_count,
        })

    @app.get("/api/cameras")
    def api_cameras():
        if not _camera_manager.list_cameras():
            cfg = load_config()
            branch_id = get_branch_id(cfg) or "local-branch"
            saved = local_db.get_dashboard_config(branch_id) if branch_id else {}
            configured = saved.get("cameras") if isinstance(saved, dict) else None
            if isinstance(configured, dict):
                configured = configured.get(branch_id) or configured.get("1")
            if not isinstance(configured, list):
                configured = cfg.get("cameras")
            if isinstance(configured, list):
                cameras = get_enabled_cameras({"cameras": configured})
                if cameras:
                    _camera_manager.sync_cameras(branch_id or "local-branch", cameras)
        return jsonify({"success": True, "cameras": _camera_manager.list_cameras()})

    @app.get("/api/camera-stream/<camera_id>")
    def api_camera_stream(camera_id: str):
        return Response(
            _camera_manager.mjpeg_frames(camera_id),
            mimetype="multipart/x-mixed-replace; boundary=frame",
        )
    
    
    @app.get("/api/perf")
    def api_perf():
        """Where this process's CPU actually goes, split by pipeline stage
        and by OS thread. Read-only; changes nothing.

        Pass ?reset=1 to start a fresh measurement window — call it once,
        wait 30-60 seconds with the system in its normal state, then call
        again without reset to read that window. Thread percentages are
        always deltas since the previous call, so the FIRST call after
        startup reports lifetime averages and every later one reports the
        interval since the last.
        """
        reset = str(request.args.get("reset") or "").strip().lower() in {"1", "true", "yes"}
        return jsonify({"success": True, "perf": perf_stats.snapshot(reset=reset)})

    @app.get("/perf")
    def perf_page():
        """Plain HTML rendering of /api/perf, so the numbers can be read on
        the node machine itself without a JSON viewer or devtools. Auto
        refreshes, which also keeps the thread-CPU deltas flowing."""
        return Response(_render_perf_page(perf_stats.snapshot()), mimetype="text/html")

    @app.post("/api/run-cycle")
    def api_run_cycle():
        try:
            return jsonify({"success": True, "status": _service.run_cycle()})
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

    # No cloud attendance sync and no held-for-review workflow anymore —
    # attendance is check-in/checkout only, resolved atomically at write
    # time by local_db.record_attendance_local. /api/sync-attendance and
    # every /api/held-attendance* route (list, mark-late, mark-half-day,
    # mark-short-leave, mark-overtime, mark-early-left, mark-*-checkin,
    # sync, delete) were removed along with NodeService's matching
    # wrapper methods — there is nothing left for an operator to review or
    # flush; a row is final the moment it's written.

    @app.post("/api/clear-today-attendance")
    def api_clear_today_attendance():
        """Maintenance/testing action: wipes today's attendance_buffer rows
        (pending and already-synced-to-backup alike), clears the
        live-events feed, and resets the per-camera dedupe throttle so a
        cleared person is eligible to be re-detected on the very next
        frame instead of waiting out DUPLICATE_LOG_SECONDS. This does not
        undo anything already pushed by the weekly backup — it only resets
        this node's local view of today."""
        try:
            cleared = local_db.clear_today_attendance(_current_branch_id())
            clear_events()
            _camera_manager.clear_person_throttles()
            return jsonify({"success": True, "cleared_count": cleared})
        except Exception as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

    @app.post("/api/import-embeddings")
    def api_import_embeddings():
        uploaded = request.files.get("package")
        if uploaded is None or not uploaded.filename:
            return jsonify({"success": False, "message": "No package file uploaded."}), 400

        cfg = get_runtime_identity(load_config())
        saved_config = local_db.get_dashboard_config(
            str(cfg.get("branch_id") or "local-branch")
        )
        branch_id = str(cfg.get("branch_id") or "local-branch")
        branch_name = str(
            cfg.get("branch_name")
            or saved_config.get("branch_name")
            or "Main Branch"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "package.zip"
            uploaded.save(tmp_path)
            try:
                package = parse_embedding_package(tmp_path)
            except PackageImportError as exc:
                return jsonify({"success": False, "message": str(exc)}), 400

            package_branch_label = str(package.get("branch_label") or "").strip()
            if branch_name and package_branch_label and package_branch_label != branch_name:
                return jsonify({
                    "success": False,
                    "message": (
                        f"Package branch label '{package_branch_label}' does not match this node branch '{branch_name}'."
                    ),
                }), 400

            result = local_db.import_embedding_package(
                branch_id=branch_id,
                package_id=package["package_id"],
                branch_label=package["branch_label"],
                generated_at=package["generated_at"],
                records=package["records"],
            )

            recognition_worker.invalidate_cache() 

        return jsonify({
            "success": True,
            "branch_label": package["branch_label"],
            "generated_at": package["generated_at"],
            "source_csv_name": package.get("source_csv_name"),
            "source_csv_sha256": package.get("source_csv_sha256"),
            **result,
        })

    @app.post("/api/restart")
    def api_restart():
        """Hard-restarts the node: launches a replacement instance of this
        same exe, then kills this one outright with no cleanup — the same
        effect as ending it from Task Manager and starting it again. The
        replacement gets --relaunch (retry the single-instance lock) and
        --no-browser (don't pop a second tab; the frontend polls and
        reloads the existing one instead)."""
        def _self_relaunch_command() -> list[str]:
            # Frozen/Nuitka build: sys.executable IS the compiled exe itself,
            # invoked directly. Dev environment (`python -m local_node.main`):
            # sys.executable is the Python interpreter, and re-invoking with
            # sys.argv[0] (main.py's bare file path) runs it OUTSIDE the
            # local_node package context — every `from local_node.xxx
            # import ...` in the codebase then fails at import time, and
            # because the replacement is launched detached (no console),
            # that failure is completely silent: it just dies instantly and
            # nothing comes back. Re-invoking the same way (-m local_node.main)
            # avoids that entirely.
            if getattr(sys, "frozen", False) or "__compiled__" in globals():
                return [sys.executable]
            return [sys.executable, "-m", "local_node.main"]

        def _do_restart() -> None:
            time.sleep(0.5)  # let this response actually reach the browser first
            try:
                subprocess.Popen(
                    _self_relaunch_command() + ["--no-browser", "--relaunch"],
                    # Repo root, not wherever this process's cwd happens to be —
                    # required for `-m local_node.main` to resolve the package
                    # regardless of how/where the original process was started.
                    cwd=str(Path(__file__).resolve().parent.parent),
                    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
            finally:
                os._exit(1)  # hard kill — matches "End Task", no shutdown hooks run

        threading.Thread(target=_do_restart, daemon=True).start()
        return jsonify({"success": True, "message": "Restarting node..."})

    @app.get("/api/import-history")
    def api_import_history():
        return jsonify({"success": True, "history": local_db.import_history(20)})

    def _serve_spa(dist: Path, build_hint: str):
        index_file = dist / "index.html"
        if index_file.exists():
            return send_from_directory(dist, "index.html")
        return f"Build not found. Run: {build_hint}", 200

    # Paths whose absence must be a hard 404. Anything else is treated as a
    # client-side route and gets the SPA fallback.
    _ASSET_SUFFIXES = frozenset({
        ".js", ".mjs", ".css", ".map", ".json",
        ".svg", ".png", ".ico", ".woff", ".woff2",
    })

    def _serve_spa_asset(dist: Path, path: str, build_hint: str):
        target = dist / path
        if target.is_file():
            return send_from_directory(dist, path)
        # A missing bundle must 404, not fall through to index.html.
        # Answering a <script type="module"> request with HTML at status 200
        # makes the browser refuse to execute it: the page renders as an
        # empty <div id="root"> with no 404, no server error and no log
        # line. That is the single hardest failure in this app to diagnose
        # remotely, and it is what a mis-staged dashboard_web/dist produces.
        if Path(path).suffix.lower() in _ASSET_SUFFIXES:
            abort(404)
        return _serve_spa(dist, build_hint)

    # Default route: the client-dashboard SPA (Staff Management,
    # Attendance View, Live Monitoring — one nav bar). This is what the
    # client actually opens; main.py's webbrowser.open() points here.
    @app.get("/")
    def index():
        return _serve_spa(_dashboard_dist(), "cd src && npm run build:local")

    # Legacy technical UI (raw camera list, activation debug, perf link) —
    # kept for support/troubleshooting only, not part of the client's nav.
    @app.get("/engine")
    def engine_index():
        return _serve_spa(_web_dist(), "cd local_node/local_node_ui && npm run build")

    @app.get("/engine/<path:path>")
    def engine_spa(path: str):
        return _serve_spa_asset(_web_dist(), path, "cd local_node/local_node_ui && npm run build")

    # Catch-all SPA fallback for the dashboard build's own client-side
    # routes (e.g. /staff, /attendance, /live-monitoring) and static
    # assets (JS/CSS chunks) — MUST be registered last so it never shadows
    # /api/*, /engine, or /perf above.
    @app.get("/<path:path>")
    def spa(path: str):
        return _serve_spa_asset(_dashboard_dist(), path, "cd src && npm run build:local")

    return app