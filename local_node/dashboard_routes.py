

# """
# local_node/dashboard_routes.py

# Dashboard-facing API surface for the three purchased modules, mounted into
# the SAME Flask app/process as the existing node engine routes (see
# ui_server.py's create_app()) — one port, one process, matching the
# consolidation plan.

# Route paths intentionally mirror the cloud's existing /api/staff and
# /api/attendance shapes so the client-dashboard frontend's staffApi.ts /
# attendanceApi.ts need only a base-URL change, not a rewrite — same
# contract, different implementation behind it, exactly like swapping
# SupabaseOrgDataStore for a local one.

# Auth: local mode is single-tenant and single-user (the client's own
# machine) — there is no multi-tenant boundary to enforce here, so this
# intentionally does NOT port the cloud's JWT/Bearer middleware. If the
# client wants a login screen anyway (shared machine, multiple staff with
# different access), that's a small addition (one local admin password
# checked against a hash in node_config.json) — flagging as an open
# question rather than guessing at a requirement.
# """

# from __future__ import annotations

# import sqlite3
# import tempfile
# from pathlib import Path

# from flask import Blueprint, Response, jsonify, request

# from local_node import backup_worker
# from local_node import local_db
# from local_node.auth import (
#     build_dashboard_user_payload,
#     check_login_and_license,
#     get_current_admin,
#     issue_token,
#     set_admin_password,
#     validate_strong_password,
# )
# from local_node.config_store import get_branch_id, load_config, save_config
# from local_node.enrollment import EnrollmentError, enroll_from_video
# from local_node import license_check
# from local_node import recognition_worker
# from local_node import live_events
# from local_node.camera_config import get_enabled_cameras
# from local_node.camera_stream_manager import get_camera_stream_manager

# dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api")


# @dashboard_bp.before_request
# def _enforce_login_and_license():
#     """Applies to every route on this blueprint (staff, attendance,
#     settings, backup) — one hook instead of decorating each route.
#     Deliberately scoped to this blueprint only: ui_server.py's own routes
#     (camera status/streaming, activation, restart, perf page) are NOT on
#     dashboard_bp, so recognition and attendance capture keep running even
#     while the dashboard itself is logged out or the org is suspended."""
#     return check_login_and_license()


# def _branch_id() -> str:
#     return get_branch_id(load_config()) or "local-branch"


# def _dashboard_bootstrap() -> dict:
#     """Assemble everything the React dashboard needs to reflect the org's
#     Support-configured setup: name/email/phone, template, staff-type scope,
#     and this node's one branch (name/location/max_staff_capacity).

#     Source of truth precedence: node_config.json (populated by
#     activation.activate_with_token / license_check.activate_license, and
#     kept fresh by license_check's throttled config refresh — see that
#     module) first, falling back to the local SQLite dashboard_config cache
#     only for fields Support has never set (so a brand-new install still
#     renders something sensible before any token is entered).

#     Deliberately does NOT reach out to Supabase directly — this process
#     only ever knows the backend/Support API URL, never Supabase
#     credentials, and branch data has moved off Supabase to the backend's
#     own store (see support_db_branches.py) so a stale direct query here
#     would silently read the wrong source anyway. Any remote refresh
#     happens exclusively through license_check's existing channel.
#     """
#     cfg = load_config()
#     branch_id = _branch_id()
#     saved = local_db.get_dashboard_config(branch_id)

#     def _field(cfg_key: str, saved_key: str | None = None, default: str = "") -> str:
#         value = str(cfg.get(cfg_key) or "").strip()
#         if value:
#             return value
#         value = str(saved.get(saved_key or cfg_key) or "").strip()
#         return value or default

#     organization_id = str(
#         cfg.get("organization_id") or cfg.get("org_id") or saved.get("organization_id") or "local"
#     )
#     organization_name = _field("organization_name", default="Local Organization")
#     branch_name = _field("branch_name", default="Main Branch")
#     contact_email = _field("contact_email")
#     contact_phone = _field("contact_phone")
#     business_type = _field("business_type", default="company")
#     primary_people_type = _field("primary_people_type", default="staff")
#     enabled_staff_types = (
#         cfg.get("enabled_staff_types")
#         if isinstance(cfg.get("enabled_staff_types"), list) and cfg.get("enabled_staff_types")
#         else (saved.get("enabled_staff_types") if isinstance(saved.get("enabled_staff_types"), list) else ["office", "field"])
#     )
#     vertical_config = (
#         cfg.get("vertical_config")
#         if isinstance(cfg.get("vertical_config"), dict) and cfg.get("vertical_config")
#         else (saved.get("vertical_config") if isinstance(saved.get("vertical_config"), dict) else {})
#     )

#     # Org-wide license/headcount capacity (vertical_config.max_users) is a
#     # DIFFERENT number from this branch's own max_staff_capacity below —
#     # they used to be wrongly conflated into one "max_capacity" field.
#     max_capacity = cfg.get("max_capacity") or saved.get("max_capacity") or vertical_config.get("max_users")
#     if max_capacity is not None:
#         try:
#             max_capacity = int(max_capacity)
#         except (TypeError, ValueError):
#             max_capacity = None

#     branch_location = _field("branch_location")
#     branch_max_staff_capacity = cfg.get("branch_max_staff_capacity") or saved.get("branch_max_staff_capacity")
#     if branch_max_staff_capacity is not None:
#         try:
#             branch_max_staff_capacity = int(branch_max_staff_capacity)
#         except (TypeError, ValueError):
#             branch_max_staff_capacity = None

#     local_branch_ui_id = 1
#     branch = {
#         "id": local_branch_ui_id,
#         "backend_branch_id": branch_id or "local-branch",
#         "name": branch_name,
#         "location": branch_location,
#         "max_staff_capacity": branch_max_staff_capacity,
#         "timezone": (saved.get("company_profile") or {}).get("timezone") or (cfg.get("branch") or {}).get("timezone"),
#     }
#     config = dict(saved)
#     config["organization_id"] = organization_id
#     config["organization_name"] = organization_name
#     config["org_name"] = organization_name
#     config["contact_email"] = contact_email
#     config["contact_phone"] = contact_phone
#     config["business_type"] = business_type
#     config["primary_people_type"] = primary_people_type
#     config["enabled_staff_types"] = enabled_staff_types
#     config["vertical_config"] = vertical_config
#     if max_capacity is not None:
#         config["max_capacity"] = max_capacity
#         config["max_users"] = max_capacity
#     config["branch_name"] = branch_name
#     config["branchName"] = branch_name
#     config["branches"] = [branch]
#     config.setdefault("modules", ["employees", "attendance", "liveattendance", "settings"])
#     # The React config normalizer uses numeric UI branch ids, while the local
#     # SQLite tables are scoped by the stable `local-branch` key. Keep the
#     # storage key stable and expose config collections under the UI id.
#     for key in ("departments", "roles", "cameras"):
#         values = config.get(key)
#         if isinstance(values, dict):
#             local_values = values.get(branch_id, values.get(str(local_branch_ui_id)))
#             if local_values is not None:
#                 config[key] = {str(local_branch_ui_id): local_values}
#     return {
#         "success": True,
#         "organization": {
#             "id": organization_id,
#             "name": organization_name,
#             "contact_email": contact_email,
#             "contact_phone": contact_phone,
#             "business_type": business_type,
#             "org_type": business_type,
#             "primary_people_type": primary_people_type,
#             "enabled_staff_types": enabled_staff_types,
#             "vertical_config": vertical_config,
#             "max_capacity": max_capacity,
#             "max_users": max_capacity,
#             "attendance_mode": "local",
#             "status": "active",
#         },
#         "branches": [branch],
#         "modules": [{"module_name": key, "status": "active"} for key in (
#             "employees", "attendance", "liveattendance", "settings"
#         )],
#         "active_modules": ["employees", "attendance", "liveattendance", "settings"],
#         "config": config,
#         "onboarding_config": config,
#     }


# @dashboard_bp.get("/client/bootstrap")
# def api_local_client_bootstrap():
#     return jsonify(_dashboard_bootstrap())


# @dashboard_bp.post("/client/onboarding/complete")
# def api_local_onboarding_complete():
#     payload = request.get_json(silent=True) or {}
#     config = payload.get("config")
#     if not isinstance(config, dict):
#         return jsonify({"success": False, "message": "config must be an object"}), 400
#     branch_id = _branch_id() or "local-branch"
#     normalized_config = dict(config)
#     for key in ("departments", "roles", "cameras"):
#         values = normalized_config.get(key)
#         if isinstance(values, dict) and "1" in values:
#             normalized_config[key] = {_branch_id() or "local-branch": values["1"]}
#     current_node_cfg = load_config()
#     saved_org_name = (
#         normalized_config.get("company_profile", {}).get("name")
#         or payload.get("organization_name")
#         or payload.get("org_name")
#         or current_node_cfg.get("org_name")
#         or current_node_cfg.get("organization_name")
#         or "Local Organization"
#     )
#     saved_branch_name = (
#         normalized_config.get("branch_name")
#         or payload.get("branch_name")
#         or current_node_cfg.get("branch_name")
#         or current_node_cfg.get("branchName")
#         or "Main Branch"
#     )
#     saved = local_db.save_dashboard_config(
#         branch_id,
#         {
#             **normalized_config,
#             "organization_id": payload.get("organization_id") or current_node_cfg.get("org_id") or current_node_cfg.get("organization_id") or "local",
#             "organization_name": saved_org_name,
#             "org_name": saved_org_name,
#             "branch_name": saved_branch_name,
#             "branchName": saved_branch_name,
#         },
#     )
#     configured_timezone = (normalized_config.get("company_profile") or {}).get("timezone")
#     if configured_timezone:
#         current_cfg = load_config()
#         save_config({
#             "branch": {
#                 **(current_cfg.get("branch") or {}),
#                 "timezone": str(configured_timezone).strip(),
#             },
#         })
#     saved_cameras = saved.get("cameras")
#     if isinstance(saved_cameras, dict):
#         saved_cameras = saved_cameras.get(branch_id) or saved_cameras.get("1")
#     if isinstance(saved_cameras, list):
#         get_camera_stream_manager().sync_cameras(
#             branch_id,
#             get_enabled_cameras({"cameras": saved_cameras}),
#         )
#     return jsonify({**_dashboard_bootstrap(), "config": saved, "onboarding_config": saved})


# @dashboard_bp.post("/client/onboarding/departments")
# def api_local_add_department():
#     """Local-node counterpart of the cloud's api_client_add_department
#     (app.py). Additive sibling of onboarding/complete: that route replaces
#     this branch's whole dashboard config with whatever the client currently
#     holds in memory, so posting a partial config from a surface that only
#     knows about departments/roles (the Add/Edit Staff modal) would wipe out
#     camera/network/company-profile config. This does a locked
#     read-modify-write of just the departments collection instead (see
#     local_db.add_department).

#     The frontend sends `branch_id` as the synthetic UI branch id (always
#     `1` locally — see local_branch_ui_id above), but this node is
#     single-branch and stores config under its own real branch id, so the
#     UI's value is accepted but ignored in favor of `_branch_id()`, the same
#     remapping onboarding/complete already does for departments/roles.
#     """
#     data = request.get_json(silent=True) or {}
#     name = data.get("name")
#     branch_id = _branch_id() or "local-branch"
#     try:
#         department, branch_departments = local_db.add_department(branch_id, name)
#         return jsonify({
#             "success": True,
#             "department": department,
#             "departments": branch_departments,
#         }), 200
#     except ValueError as e:
#         return jsonify({"success": False, "message": str(e)}), 400


# @dashboard_bp.post("/client/onboarding/designations")
# def api_local_add_designation():
#     """Local-node counterpart of the cloud's api_client_add_designation
#     (app.py). Same additive read-modify-write rationale as
#     api_local_add_department above — see that docstring."""
#     data = request.get_json(silent=True) or {}
#     department_id = data.get("department_id")
#     name = data.get("name")
#     branch_id = _branch_id() or "local-branch"
#     try:
#         designation, department_designations = local_db.add_designation(
#             branch_id, department_id, name,
#         )
#         return jsonify({
#             "success": True,
#             "designation": designation,
#             "designations": department_designations,
#         }), 200
#     except ValueError as e:
#         return jsonify({"success": False, "message": str(e)}), 400


# # ── Account (self-service) ───────────────────────────────────────────────────
# # Mirrors two cloud routes so client-dashboard's api.ts (changeOwnPassword /
# # AuthContext.tsx's refreshUser) work unmodified against this node:
# #   - PATCH /api/users/<id>/profile  <-> app.py's api_update_user_profile
# #     (legacy/numeric-id accounts) — this node's admin id is always numeric,
# #     so api.ts's isUuidLike() check always routes here, never to
# #     /api/client/account/password.
# #   - GET /api/users/<id>            <-> the session-refresh half of
# #     support_db_client_users.get_client_user_session_by_id — called by
# #     refreshUser() immediately after a successful password change.
# # Both sit on dashboard_bp, so they're already behind
# # check_login_and_license (see before_request above) before they run.

# @dashboard_bp.patch("/users/<int:user_id>/profile")
# def api_update_own_profile(user_id: int):
#     """Self-service 'change my own password' for this node's single admin
#     account. Identity comes from the verified token (get_current_admin),
#     never from the URL — the user_id path segment is only cross-checked
#     against it below, exactly like the cloud's self-only guards, so there
#     is no id-spoofing surface even though this node has just one account.
#     """
#     admin = get_current_admin()
#     if admin is None:
#         return jsonify({
#             "success": False,
#             "message": "Your session could not be identified. Please sign in again.",
#         }), 401

#     if int(admin["id"]) != int(user_id):
#         return jsonify({
#             "success": False,
#             "message": "You can only update your own profile.",
#         }), 403

#     data = request.get_json(silent=True) or {}
#     new_password = str(data.get("new_password") or "").strip()
#     if not new_password:
#         # Name/email/phone editing isn't exposed on this endpoint yet — see
#         # AccountSettings.tsx's docstring ("Add future self-only account
#         # settings here"). ChangePasswordCard.tsx is this route's only
#         # caller today and always sends new_password.
#         return jsonify({"success": False, "message": "New password is required."}), 400

#     try:
#         validate_strong_password(new_password)
#     except ValueError as exc:
#         return jsonify({"success": False, "message": str(exc)}), 400

#     branch_id = _branch_id()
#     set_admin_password(branch_id, admin["email"], new_password, full_name=admin.get("full_name") or "")

#     # Mint a fresh token so THIS request's own caller doesn't need to log
#     # back in on its very next call — same reasoning as the cloud's
#     # api_change_own_dashboard_password. Identity (branch_id + email)
#     # never changes on a password change, only the token's issued-at
#     # time, so re-using it here is correct rather than stale.
#     refreshed_admin = local_db.get_admin_by_email(branch_id, admin["email"])
#     fresh_token = issue_token(branch_id, admin["email"])

#     return jsonify({
#         "success": True,
#         "message": "Password updated successfully.",
#         "user": build_dashboard_user_payload(refreshed_admin, branch_id),
#         "token": fresh_token,
#     }), 200


# @dashboard_bp.get("/users/<int:user_id>")
# def api_get_own_user(user_id: int):
#     """Companion to api_update_own_profile above — the exact URL
#     AuthContext.tsx's refreshUser() calls right after a successful
#     password change for a non-UUID (this node's) id. Returns the same
#     shape /login returns so the frontend's normaliseUser treats a
#     refresh identically to a fresh login."""
#     admin = get_current_admin()
#     if admin is None or int(admin["id"]) != int(user_id):
#         return jsonify({"success": False, "message": "Not authenticated."}), 401

#     return jsonify({
#         "success": True,
#         "user": build_dashboard_user_payload(admin, _branch_id()),
#     }), 200


# # ── Staff (People Management) ────────────────────────────────────────────────

# @dashboard_bp.get("/staff")
# def api_list_staff():
#     include_archived = request.args.get("include_archived") == "1"
#     department = request.args.get("department") or None
#     return jsonify(local_db.list_staff(
#         _branch_id(), include_archived=include_archived, department=department,
#     ))


# @dashboard_bp.get("/staff/archived")
# def api_list_archived_staff():
#     return jsonify(local_db.list_staff(_branch_id(), include_archived=True))


# @dashboard_bp.get("/staff/<staff_id>")
# def api_get_staff(staff_id: str):
#     record = local_db.get_staff(_branch_id(), staff_id)
#     if record is None:
#         return jsonify({"success": False, "message": "Staff record not found"}), 404
#     return jsonify(record)


# @dashboard_bp.get("/staff/<staff_id>/photo")
# def api_get_staff_photo(staff_id: str):
#     with local_db._connect() as conn:
#         row = conn.execute(
#             "SELECT profile_image_data, profile_image_mime "
#             "FROM staff WHERE branch_id = ? AND id = ?",
#             (_branch_id(), staff_id),
#         ).fetchone()

#     if not row or not row[0]:
#         return jsonify({"success": False, "message": "Profile image not found"}), 404

#     return Response(
#         row[0],
#         mimetype=row[1] or "application/octet-stream",
#         headers={"Cache-Control": "no-cache"},
#     )


# @dashboard_bp.post("/staff/<staff_id>/photo")
# def api_upload_staff_photo(staff_id: str):
#     photo = request.files.get("photo")
#     if photo is None:
#         return jsonify({"success": False, "message": "No photo provided"}), 400
#     data = photo.read()
#     if not data:
#         return jsonify({"success": False, "message": "Photo is empty"}), 400
#     with local_db._write_lock:
#         with local_db._connect() as conn:
#             cur = conn.execute(
#                 "UPDATE staff SET profile_image_data = ?, profile_image_mime = ?, updated_at = ? "
#                 "WHERE branch_id = ? AND id = ?",
#                 (data, photo.mimetype or "application/octet-stream", local_db.utc_now(), _branch_id(), staff_id),
#             )
#             conn.commit()
#     if cur.rowcount == 0:
#         return jsonify({"success": False, "message": "Staff record not found"}), 404
#     return jsonify({"success": True, "profile_image_name": photo.filename or "profile-image"})


# @dashboard_bp.post("/staff")
# def api_create_staff():
#     payload = request.get_json(silent=True) or {}
#     branch_id = _branch_id()
#     cfg = load_config()
#     saved = local_db.get_dashboard_config(branch_id)
#     vertical_config = saved.get("vertical_config") or cfg.get("vertical_config") or {}
#     max_cap = (
#         saved.get("max_capacity")
#         or cfg.get("max_capacity")
#         or vertical_config.get("max_users")
#         or vertical_config.get("max_capacity")
#         or saved.get("max_users")
#         or cfg.get("max_users")
#     )
#     if max_cap is not None:
#         try:
#             limit = int(max_cap)
#             if limit > 0:
#                 current_count = local_db.count_active_staff(branch_id)
#                 if current_count >= limit:
#                     return jsonify({
#                         "success": False,
#                         "message": f"Organization staff capacity limit of {limit} reached. Cannot add more people.",
#                     }), 400
#         except (TypeError, ValueError):
#             pass

#     try:
#         record = local_db.create_staff(branch_id, payload)
#     except ValueError as exc:
#         return jsonify({"success": False, "message": str(exc)}), 400
#     return jsonify(record), 201


# @dashboard_bp.put("/staff/<staff_id>")
# def api_update_staff(staff_id: str):
#     payload = request.get_json(silent=True) or {}
#     try:
#         record = local_db.update_staff(_branch_id(), staff_id, payload)
#     except ValueError as exc:
#         return jsonify({"success": False, "message": str(exc)}), 400
#     except Exception as exc:
#         if isinstance(exc, sqlite3.IntegrityError):
#             return jsonify({"success": False, "message": "Staff ID conflicts with existing face enrollment data."}), 400
#         raise
#     if record is None:
#         return jsonify({"success": False, "message": "Staff record not found"}), 404
#     if any(key in payload for key in ("person_code", "personCode", "full_name", "fullName")):
#         recognition_worker.invalidate_cache()
#     return jsonify(record)


# @dashboard_bp.post("/staff/<staff_id>/archive")
# def api_archive_staff(staff_id: str):
#     ok = local_db.archive_staff(_branch_id(), staff_id)
#     if not ok:
#         return jsonify({"success": False, "message": "Staff record not found or already archived"}), 404
#     return jsonify({"success": True})


# @dashboard_bp.post("/staff/<staff_id>/restore")
# def api_restore_staff(staff_id: str):
#     ok = local_db.restore_staff(_branch_id(), staff_id)
#     if not ok:
#         return jsonify({"success": False, "message": "Staff record not found"}), 404
#     return jsonify({"success": True})


# @dashboard_bp.post("/staff/archived/<staff_id>/delete")
# def api_delete_archived_staff(staff_id: str):
#     branch_id = _branch_id()
#     with local_db._write_lock:
#         with local_db._connect() as conn:
#             conn.execute(
#                 "DELETE FROM staff WHERE branch_id = ? AND id = ? AND archived_at IS NOT NULL",
#                 (branch_id, staff_id),
#             )
#             conn.commit()
#     return jsonify({"success": True, "deleted_user_id": staff_id, "deleted_count": 1})


# @dashboard_bp.get("/client/branches/<branch_id>/shifts")
# def api_local_list_shifts(branch_id: str):
#     branch = _branch_id()
#     people_type = request.args.get("people_type")
#     shift_type = str(request.args.get("shift_type") or "main").lower()
#     list_fn = local_db.list_break_shifts if shift_type == "break" else local_db.list_shifts
#     shifts = list_fn(branch, people_type)
#     # No auto-seeding here: this is a plain read. ShiftAllocationTab already
#     # renders an explicit "No shifts exist yet — add one under Shift Timings"
#     # empty state for this exact case, and ShiftTimingsModal owns the one
#     # editable set of starter presets (DEFAULT_SHIFT_PRESETS) that the admin
#     # reviews and explicitly saves. A second, backend-side set of defaults
#     # silently created on GET only fights that flow — different preset
#     # times, created behind the admin's back, and colliding with whatever
#     # the modal tries to save (see ShiftNameConflict in local_db.create_shift).
#     if shift_type in ("main", "break"):
#         shifts = [row for row in shifts if row.get("shift_type", "main") == shift_type]
#     return jsonify({"shifts": shifts})


# @dashboard_bp.post("/client/branches/<branch_id>/shifts")
# def api_local_create_shift(branch_id: str):
#     try:
#         shift = local_db.create_shift(_branch_id(), request.get_json(silent=True) or {})
#     except local_db.ShiftNameConflict as exc:
#         return jsonify({"success": False, "message": str(exc)}), 409
#     return jsonify({"shift": shift}), 201


# @dashboard_bp.patch("/client/branches/<branch_id>/shifts/<shift_id>")
# def api_local_update_shift(branch_id: str, shift_id: str):
#     row = local_db.update_shift(_branch_id(), shift_id, request.get_json(silent=True) or {})
#     if row is None:
#         return jsonify({"success": False, "message": "Shift not found"}), 404
#     return jsonify({"shift": row, "warnings": []})


# @dashboard_bp.delete("/client/branches/<branch_id>/shifts/<shift_id>")
# def api_local_delete_shift(branch_id: str, shift_id: str):
#     row = local_db.update_shift(_branch_id(), shift_id, {"is_active": 0})
#     if row is None:
#         return jsonify({"success": False, "message": "Shift not found"}), 404
#     return jsonify({"deleted": True})


# @dashboard_bp.patch("/client/staff/<staff_id>/shift")
# def api_local_assign_shift(staff_id: str):
#     payload = request.get_json(silent=True) or {}
#     row = local_db.assign_staff_shift(_branch_id(), staff_id, payload.get("shift_id"))
#     if row is None:
#         return jsonify({"success": False, "message": "Staff member or shift not found"}), 404
#     return jsonify({"staff": row})


# @dashboard_bp.get("/client/staff/<staff_id>/break-shifts")
# def api_local_list_staff_break_shifts(staff_id: str):
#     return jsonify({"shifts": local_db.list_staff_break_shifts(_branch_id(), staff_id)})


# @dashboard_bp.put("/client/staff/<staff_id>/break-shifts")
# def api_local_assign_break_shifts(staff_id: str):
#     payload = request.get_json(silent=True) or {}
#     shift_ids = payload.get("shift_ids")
#     if not isinstance(shift_ids, list):
#         return jsonify({"success": False, "message": "shift_ids must be an array"}), 400
#     try:
#         row = local_db.assign_staff_break_shifts(_branch_id(), staff_id, shift_ids)
#     except ValueError as exc:
#         return jsonify({"success": False, "message": str(exc)}), 400
#     if row is None:
#         return jsonify({"success": False, "message": "Staff member not found"}), 404
#     return jsonify({
#         "success": True,
#         "staff": row,
#         "break_shifts": local_db.list_staff_break_shifts(_branch_id(), staff_id),
#     })


# # ── Enrollment ("face-training jobs") ────────────────────────────────────────

# @dashboard_bp.post("/staff/<staff_id>/training-video")
# def api_upload_training_video(staff_id: str):
#     branch_id = _branch_id()
#     record = local_db.get_staff(branch_id, staff_id)
#     if record is None:
#         return jsonify({"success": False, "message": "Staff record not found"}), 404

#     video_file = request.files.get("video")
#     if video_file is None:
#         return jsonify({"success": False, "message": "No video file provided"}), 400

#     # Written to a temp file rather than held in memory — enrollment clips
#     # run 10-60MB and OpenCV's VideoCapture needs a real file path, not a
#     # stream, on the codecs this ships with.
#     with tempfile.TemporaryDirectory() as tmp_dir:
#         video_path = Path(tmp_dir) / "enrollment.mp4"
#         video_file.save(video_path)
#         try:
#             result = enroll_from_video(video_path)
#         except EnrollmentError as exc:
#             return jsonify({"success": False, "message": str(exc)}), 400

#     local_db.upsert_person_embeddings(
#         branch_id=branch_id,
#         people_type=record["people_type"],
#         person_code=record["person_code"],
#         full_name=record["full_name"],
#         embeddings=result["embeddings"],
#         model_version=result["model_version"],
#         source_package_id="local-enrollment",
#     )
#     local_db.recognition_worker.invalidate_cache()  # new embeddings must be picked up immediately

#     return jsonify({
#         "success": True,
#         "frame_count": result["frame_count"],
#         "usable_frame_count": result["usable_frame_count"],
#     })


# # ── Attendance View ───────────────────────────────────────────────────────────

# @dashboard_bp.get("/attendance/view")
# def api_attendance_view():
#     """Shapes attendance_buffer rows into what AttendanceView.tsx expects.

#     This client has no payroll module — check-in/checkout is the entire
#     scope, so checkInStatus/checkOutStatus are trivial reads of columns
#     record_attendance_local already computed at write time (on_time/late
#     for check-in; on_time/None for checkout — a checkout is never "late"
#     here, since anything outside its window never confirms at all, see
#     local_db.record_attendance_local). No shift re-resolution needed here:
#     the shift gate already made this decision once, atomically, when the
#     detection was recorded.
#     """
#     limit = int(request.args.get("limit") or 100)
#     rows = local_db.recent_attendance(_branch_id(), limit=limit)
#     return jsonify([_shape_attendance_row(r) for r in rows])


# @dashboard_bp.get("/attendance/today")
# def api_attendance_today():
#     requested_date = request.args.get("date")
#     start_date = request.args.get("start")
#     end_date = request.args.get("end")
#     rows = local_db.recent_attendance(_branch_id(), limit=5000)
#     if requested_date:
#         rows = [
#             row
#             for row in rows
#             if str(row.get("attendance_date")) == requested_date
#         ]
#     elif start_date or end_date:
#         rows = [
#             row
#             for row in rows
#             if (not start_date or str(row.get("attendance_date")) >= start_date)
#             and (not end_date or str(row.get("attendance_date")) <= end_date)
#         ]
#     return jsonify([_shape_attendance_row(row) for row in rows])


# @dashboard_bp.get("/attendance/breaks/today")
# def api_break_attendance_today():
#     """Return today's Namaz/break attendance rows for review."""
#     today = local_db._today()
#     local_db.finalize_expired_break_attendance(_branch_id(), today)
#     return jsonify(local_db.list_break_attendance(_branch_id(), attendance_date=today))


# @dashboard_bp.get("/attendance/breaks")
# def api_break_attendance():
#     requested_date = request.args.get("date")
#     start_date = request.args.get("start")
#     end_date = request.args.get("end")
#     if requested_date:
#         local_db.finalize_expired_break_attendance(_branch_id(), requested_date)
#     else:
#         local_db.finalize_expired_break_attendance(_branch_id(), end_date)
#     return jsonify(local_db.list_break_attendance(
#         _branch_id(),
#         attendance_date=requested_date,
#         start_date=start_date,
#         end_date=end_date,
#         status=request.args.get("status") or None,
#         limit=int(request.args.get("limit") or 500),
#     ))


# @dashboard_bp.patch("/attendance/breaks/<record_id>")
# def api_update_break_attendance(record_id: str):
#     payload = request.get_json(silent=True) or {}
#     outside_at = payload.get("outside_at", payload.get("outsideAt"))
#     returned_at = payload.get("returned_at", payload.get("returnedAt"))
#     try:
#         row = local_db.update_break_attendance(
#             _branch_id(),
#             str(record_id),
#             outside_at=str(outside_at) if outside_at else None,
#             returned_at=str(returned_at) if returned_at else None,
#             correction_reason=str(
#                 payload.get("correction_reason")
#                 or payload.get("correctionReason")
#                 or ""
#             ).strip() or None,
#         )
#     except ValueError as exc:
#         return jsonify({"success": False, "message": str(exc)}), 400
#     if row is None:
#         return jsonify({"success": False, "message": "Break attendance record not found."}), 404
#     return jsonify({"success": True, "record": row})


# @dashboard_bp.post("/attendance/breaks/finalize")
# def api_finalize_break_attendance():
#     payload = request.get_json(silent=True) or {}
#     attendance_date = str(
#         payload.get("date") or request.args.get("date") or ""
#     ).strip() or None
#     finalized = local_db.finalize_expired_break_attendance(_branch_id(), attendance_date)
#     return jsonify({
#         "success": True,
#         "finalized_count": finalized,
#         "records": local_db.list_break_attendance(
#             _branch_id(), attendance_date=attendance_date, limit=500,
#         ),
#     })


# @dashboard_bp.post("/attendance/mark-absent")
# def api_mark_absent():
#     payload = request.get_json(silent=True) or {}
#     staff_id = str(
#         payload.get("user_id")
#         or payload.get("staff_id")
#         or payload.get("staffId")
#         or ""
#     ).strip()
#     if not staff_id:
#         return jsonify({"success": False, "message": "user_id is required"}), 400

#     branch_id = _branch_id()
#     staff = local_db.get_staff(branch_id, staff_id)
#     if staff is None:
#         staff = next(
#             (
#                 item
#                 for item in local_db.list_staff(branch_id, include_archived=True)
#                 if (
#                     str(item.get("person_code") or "") == staff_id
#                     or (
#                         staff_id.isdigit()
#                         and str(item.get("person_code") or "").isdigit()
#                         and int(item["person_code"]) == int(staff_id)
#                     )
#                 )
#             ),
#             None,
#         )
#     if staff is None:
#         staff = next(
#             (
#                 item
#                 for item in local_db.recent_attendance(branch_id, limit=5000)
#                 if (
#                     str(item.get("person_code") or "") == staff_id
#                     or (
#                         staff_id.isdigit()
#                         and str(item.get("person_code") or "").isdigit()
#                         and int(item["person_code"]) == int(staff_id)
#                     )
#                 )
#             ),
#             None,
#         )
#     if staff is None:
#         return jsonify({"success": False, "message": "Employee not found."}), 404

#     attendance_date = str(payload.get("date") or request.args.get("date") or "").strip()
#     if not attendance_date:
#         attendance_date = local_db._today()
#     people_type = str(staff.get("people_type") or "staff")
#     person_code = str(staff.get("person_code") or staff_id)

#     deleted = local_db.delete_attendance_for_staff(
#         branch_id,
#         person_code,
#         attendance_date,
#         people_type=people_type,
#         staff_name=str(staff.get("full_name") or staff.get("staff_name") or ""),
#     )
#     removed_live_events = live_events.suppress_person_for_today(
#         person_code,
#         str(staff.get("full_name") or staff.get("staff_name") or ""),
#     )

#     return jsonify({
#         "success": True,
#         "record": None,
#         "status": "absent",
#         "deleted_rows": deleted,
#         "removed_live_events": removed_live_events,
#     })


# @dashboard_bp.post("/attendance/manual")
# def api_manual_attendance():
#     payload = request.get_json(silent=True) or {}
#     staff_id = str(payload.get("staff_id") or payload.get("staffId") or "").strip()
#     if not staff_id:
#         return jsonify({"success": False, "message": "Employee is required."}), 400

#     staff = local_db.get_staff(_branch_id(), staff_id)
#     if staff is None:
#         return jsonify({"success": False, "message": "Employee not found."}), 404

#     check_in = payload.get("check_in") or payload.get("checkIn")
#     check_out = payload.get("check_out") or payload.get("checkOut")
#     attendance_date = str(payload.get("attendance_date") or "")
#     if not attendance_date:
#         source_time = check_in or check_out
#         attendance_date = str(source_time or local_db.utc_now())[:10]

#     local_db.record_attendance_manual(
#         _branch_id(),
#         str(staff.get("people_type") or "staff"),
#         str(staff.get("person_code") or staff.get("id") or staff_id),
#         str(staff.get("full_name") or staff.get("name") or ""),
#         1.0,
#         attendance_date,
#         check_in_marked_at=check_in,
#         check_out_marked_at=check_out,
#         metadata={
#             "arrival_status": payload.get("arrival_status") or payload.get("arrivalStatus"),
#             "manual": True,
#         },
#     )

#     rows = local_db.recent_attendance(_branch_id(), limit=5000)
#     row = next(
#         (
#             item
#             for item in rows
#             if str(item.get("person_code")) == str(staff.get("person_code"))
#             and str(item.get("attendance_date")) == attendance_date
#         ),
#         None,
#     )
#     return jsonify({"success": True, "record": _shape_attendance_row(row or {})})


# def _patch_local_attendance(record_id: str):
#     payload = request.get_json(silent=True) or {}
#     raw_check_in = payload.get("check_in", payload.get("checkIn"))
#     raw_check_out = payload.get("check_out", payload.get("checkOut"))
#     arrival_status = str(
#         payload.get("arrival_status")
#         or payload.get("arrivalStatus")
#         or ""
#     ).strip().lower() or None
#     raw_notes = payload.get("notes")
#     notes = str(raw_notes).strip() if raw_notes else None

#     row = local_db.update_attendance_record(
#         _branch_id(),
#         str(record_id),
#         check_in=str(raw_check_in) if raw_check_in else None,
#         check_out=str(raw_check_out) if raw_check_out else None,
#         arrival_status=arrival_status,
#         notes=notes,
#         check_in_provided="check_in" in payload or "checkIn" in payload,
#         check_out_provided="check_out" in payload or "checkOut" in payload,
#         notes_provided="notes" in payload,
#     )
#     if row is None:
#         return jsonify({"success": False, "message": "Attendance record not found or unchanged."}), 404
#     return jsonify({"success": True, "record": _shape_attendance_row(row)})


# @dashboard_bp.patch("/attendance/<record_id>")
# def api_update_attendance(record_id: str):
#     return _patch_local_attendance(record_id)


# @dashboard_bp.patch("/attendance/manual/<record_id>")
# def api_update_manual_attendance(record_id: str):
#     return _patch_local_attendance(record_id)


# def _shape_attendance_row(row: dict) -> dict:
#     check_out_confirmed = bool(row.get("check_out_confirmed"))
#     is_absent = str(row.get("status") or "").lower() == "absent"
#     check_in_time = None if is_absent else row.get("marked_at")
#     check_out_time = row.get("check_out_marked_at") if check_out_confirmed and not is_absent else None
#     check_out_attempted_at = (
#         row.get("check_out_marked_at")
#         if not is_absent and not check_out_confirmed
#         else None
#     )
#     person_code = row.get("person_code")
#     # A hand-entered row is always written with source='manual_override'
#     # (local_db.record_attendance_manual's default, and
#     # manual_instructions_worker's explicit value); every camera-detected
#     # row goes through record_attendance_local, whose source defaults to
#     # 'camera'. That column is therefore the one reliable signal for the
#     # dashboard's Channel column — captureChannel must NOT be hardcoded to
#     # "local_node" for every row, or a manually-added record renders with
#     # the same "Camera" badge as a real camera detection.
#     is_manual_row = str(row.get("source") or "").lower() == "manual_override"
#     return {
#         "id": row.get("id") or row.get("local_event_id"),
#         "user_id": person_code,
#         "staff_id": person_code,
#         "personCode": row["person_code"],
#         "peopleType": row["people_type"],
#         "staffName": row.get("staff_name"),
#         "user_name": row.get("staff_name"),
#         "date": row["attendance_date"],
#         "attendance_date": row["attendance_date"],
#         "timestamp": check_in_time,
#         "check_in": check_in_time,
#         "checkInTime": check_in_time,
#         # Only a CONFIRMED checkout counts as an attendance checkout — an
#         # out-of-window sighting is informational only (see
#         # checkOutHoldReason) and must never read as a real checkout here.
#         "check_out": check_out_time,
#         "checkOutTime": check_out_time,
#         # Keep an out-of-window sighting separate from a confirmed checkout.
#         # The attendance table can show when it happened without treating it
#         # as a payable/work-duration checkout.
#         "checkOutAttemptedAt": check_out_attempted_at,
#         "check_out_attempted_at": check_out_attempted_at,
#         "checkInConfidence": None if is_absent else row.get("confidence"),
#         "checkOutConfidence": row.get("check_out_confidence") if check_out_confirmed and not is_absent else None,
#         "checkOutCameraId": row.get("check_out_camera_id") if check_out_confirmed and not is_absent else None,
#         # Check-in-side camera (as opposed to checkOutCameraId above) — added
#         # so the dashboard's Channel column can show which camera actually
#         # captured this row instead of the generic "Local Node" label for
#         # every row regardless of source. Null for a manually-marked row
#         # (record_attendance_local's camera_id is only ever set by the
#         # camera/recognition pipeline, never by the manual-mark path — see
#         # local_db.py's record_attendance_local vs. its manual counterpart).
#         "cameraId": None if (is_absent or is_manual_row) else row.get("camera_id"),
#         "captureChannel": None if is_absent else ("manual" if is_manual_row else "local_node"),
#         "notes": None if is_absent else row.get("notes"),
#         # Informational only — set when a checkout sighting fell outside its
#         # window and was therefore never confirmed (see checkOutTime above).
#         "checkOutHoldReason": None if is_absent or check_out_confirmed else row.get("check_out_hold_reason"),
#         "checkInStatus": None if is_absent else ("late" if row.get("check_in_hold_reason") == "late" else "on_time"),
#         "checkOutStatus": None if is_absent else ("on_time" if check_out_confirmed else None),
#         "dayStatus": row.get("status") or "present",
#         "workDuration": None,
#     }


# # ── Shift settings (also on the Staff Management page) ──────────────────────
# # Written straight into node_config.json — the exact structure
# # shift_gate.py already reads (shift_windows, shift_mode_enabled). Per-
# # staff personal overrides are edited via the staff endpoints above and
# # materialized into staff_shift_windows by
# # local_db._rebuild_staff_shift_windows; this endpoint only owns the
# # branch-level default per people_type.

# @dashboard_bp.get("/settings/shifts")
# def api_get_shift_settings():
#     cfg = load_config()
#     return jsonify({
#         "shift_mode_enabled": bool(cfg.get("shift_mode_enabled", False)),
#         "shift_windows": cfg.get("shift_windows") or {},
#     })


# @dashboard_bp.put("/settings/shifts")
# def api_update_shift_settings():
#     payload = request.get_json(silent=True) or {}
#     shift_windows = payload.get("shift_windows")
#     if not isinstance(shift_windows, dict):
#         return jsonify({"success": False, "message": "shift_windows must be an object keyed by people_type"}), 400
#     save_config({
#         "shift_mode_enabled": bool(payload.get("shift_mode_enabled", False)),
#         "shift_windows": shift_windows,
#     })
#     return jsonify({"success": True})


# # ── Weekly backup (morning popup) ────────────────────────────────────────────

# @dashboard_bp.get("/backup/status")
# def api_backup_status():
#     return jsonify(backup_worker.status())


# @dashboard_bp.post("/backup/consent")
# def api_backup_consent():
#     payload = request.get_json(silent=True) or {}
#     allow = bool(payload.get("allow"))
#     return jsonify(backup_worker.run_backup_if_consented(allow))


"""
local_node/dashboard_routes.py

Dashboard-facing API surface for the three purchased modules, mounted into
the SAME Flask app/process as the existing node engine routes (see
ui_server.py's create_app()) — one port, one process, matching the
consolidation plan.

Route paths intentionally mirror the cloud's existing /api/staff and
/api/attendance shapes so the client-dashboard frontend's staffApi.ts /
attendanceApi.ts need only a base-URL change, not a rewrite — same
contract, different implementation behind it, exactly like swapping
SupabaseOrgDataStore for a local one.

Auth: local mode is single-tenant and single-user (the client's own
machine) — there is no multi-tenant boundary to enforce here, so this
intentionally does NOT port the cloud's JWT/Bearer middleware. If the
client wants a login screen anyway (shared machine, multiple staff with
different access), that's a small addition (one local admin password
checked against a hash in node_config.json) — flagging as an open
question rather than guessing at a requirement.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

from local_node import backup_worker
from local_node import local_db
from local_node.auth import (
    build_dashboard_user_payload,
    check_login_and_license,
    get_current_admin,
    issue_token,
    set_admin_password,
    validate_strong_password,
)
from local_node.config_store import get_branch_id, load_config, save_config
from local_node.enrollment import EnrollmentError, enroll_from_video
from local_node import license_check
from local_node import recognition_worker
from local_node import live_events
from local_node.camera_config import get_enabled_cameras
from local_node.camera_stream_manager import get_camera_stream_manager

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api")


@dashboard_bp.before_request
def _enforce_login_and_license():
    """Applies to every route on this blueprint (staff, attendance,
    settings, backup) — one hook instead of decorating each route.
    Deliberately scoped to this blueprint only: ui_server.py's own routes
    (camera status/streaming, activation, restart, perf page) are NOT on
    dashboard_bp, so recognition and attendance capture keep running even
    while the dashboard itself is logged out or the org is suspended."""
    return check_login_and_license()


def _branch_id() -> str:
    return get_branch_id(load_config()) or "local-branch"


def _dashboard_bootstrap() -> dict:
    """Assemble everything the React dashboard needs to reflect the org's
    Support-configured setup: name/email/phone, template, staff-type scope,
    and this node's one branch (name/location/max_staff_capacity).

    Source of truth precedence: node_config.json (populated by
    activation.activate_with_token / license_check.activate_license, and
    kept fresh by license_check's throttled config refresh — see that
    module) first, falling back to the local SQLite dashboard_config cache
    only for fields Support has never set (so a brand-new install still
    renders something sensible before any token is entered).

    Deliberately does NOT reach out to Supabase directly — this process
    only ever knows the backend/Support API URL, never Supabase
    credentials, and branch data has moved off Supabase to the backend's
    own store (see support_db_branches.py) so a stale direct query here
    would silently read the wrong source anyway. Any remote refresh
    happens exclusively through license_check's existing channel.
    """
    cfg = load_config()
    branch_id = _branch_id()
    saved = local_db.get_dashboard_config(branch_id)

    def _field(cfg_key: str, saved_key: str | None = None, default: str = "") -> str:
        value = str(cfg.get(cfg_key) or "").strip()
        if value:
            return value
        value = str(saved.get(saved_key or cfg_key) or "").strip()
        return value or default

    organization_id = str(
        cfg.get("organization_id") or cfg.get("org_id") or saved.get("organization_id") or "local"
    )
    organization_name = _field("organization_name", default="Local Organization")
    branch_name = _field("branch_name", default="Main Branch")
    contact_email = _field("contact_email")
    contact_phone = _field("contact_phone")
    business_type = _field("business_type", default="company")
    primary_people_type = _field("primary_people_type", default="staff")
    enabled_staff_types = (
        cfg.get("enabled_staff_types")
        if isinstance(cfg.get("enabled_staff_types"), list) and cfg.get("enabled_staff_types")
        else (saved.get("enabled_staff_types") if isinstance(saved.get("enabled_staff_types"), list) else ["office", "field"])
    )
    vertical_config = (
        cfg.get("vertical_config")
        if isinstance(cfg.get("vertical_config"), dict) and cfg.get("vertical_config")
        else (saved.get("vertical_config") if isinstance(saved.get("vertical_config"), dict) else {})
    )

    # Org-wide license/headcount capacity (vertical_config.max_users) is a
    # DIFFERENT number from this branch's own max_staff_capacity below —
    # they used to be wrongly conflated into one "max_capacity" field.
    max_capacity = cfg.get("max_capacity") or saved.get("max_capacity") or vertical_config.get("max_users")
    if max_capacity is not None:
        try:
            max_capacity = int(max_capacity)
        except (TypeError, ValueError):
            max_capacity = None

    branch_location = _field("branch_location")
    branch_max_staff_capacity = cfg.get("branch_max_staff_capacity") or saved.get("branch_max_staff_capacity")
    if branch_max_staff_capacity is not None:
        try:
            branch_max_staff_capacity = int(branch_max_staff_capacity)
        except (TypeError, ValueError):
            branch_max_staff_capacity = None

    local_branch_ui_id = 1
    branch = {
        "id": local_branch_ui_id,
        "backend_branch_id": branch_id or "local-branch",
        "name": branch_name,
        "location": branch_location,
        "max_staff_capacity": branch_max_staff_capacity,
        "timezone": (saved.get("company_profile") or {}).get("timezone") or (cfg.get("branch") or {}).get("timezone"),
    }
    config = dict(saved)
    config["organization_id"] = organization_id
    config["organization_name"] = organization_name
    config["org_name"] = organization_name
    config["contact_email"] = contact_email
    config["contact_phone"] = contact_phone
    config["business_type"] = business_type
    config["primary_people_type"] = primary_people_type
    config["enabled_staff_types"] = enabled_staff_types
    config["vertical_config"] = vertical_config
    if max_capacity is not None:
        config["max_capacity"] = max_capacity
        config["max_users"] = max_capacity
    config["branch_name"] = branch_name
    config["branchName"] = branch_name
    config["branches"] = [branch]
    config.setdefault("modules", ["employees", "attendance", "liveattendance", "settings"])
    # The React config normalizer uses numeric UI branch ids, while the local
    # SQLite tables are scoped by the stable `local-branch` key. Keep the
    # storage key stable and expose config collections under the UI id.
    for key in ("departments", "roles", "cameras", "archived_staff_retention_days"):
        values = config.get(key)
        if isinstance(values, dict):
            local_values = values.get(branch_id, values.get(str(local_branch_ui_id)))
            if local_values is not None:
                config[key] = {str(local_branch_ui_id): local_values}
        return {
        "success": True,
        "organization": {
            "id": organization_id,
            "name": organization_name,
            "contact_email": contact_email,
            "contact_phone": contact_phone,
            "business_type": business_type,
            "org_type": business_type,
            "primary_people_type": primary_people_type,
            "enabled_staff_types": enabled_staff_types,
            "vertical_config": vertical_config,
            "max_capacity": max_capacity,
            "max_users": max_capacity,
            "attendance_mode": "local",
            "status": "active",
            "shiftEnabledPeopleTypes": config.get("shiftEnabledPeopleTypes") or [],
            "shift_enabled_people_types": config.get("shiftEnabledPeopleTypes") or [],
        },
        "branches": [branch],
        "modules": [{"module_name": key, "status": "active"} for key in (
            "employees", "attendance", "liveattendance", "settings"
        )],
        "active_modules": ["employees", "attendance", "liveattendance", "settings"],
        "config": config,
        "onboarding_config": config,
    }


@dashboard_bp.get("/client/bootstrap")
def api_local_client_bootstrap():
    return jsonify(_dashboard_bootstrap())


@dashboard_bp.post("/client/onboarding/complete")
def api_local_onboarding_complete():
    payload = request.get_json(silent=True) or {}
    config = payload.get("config")
    if not isinstance(config, dict):
        return jsonify({"success": False, "message": "config must be an object"}), 400
    branch_id = _branch_id() or "local-branch"
    normalized_config = dict(config)
    for key in ("departments", "roles", "cameras", "archived_staff_retention_days"):
        values = normalized_config.get(key)
        if isinstance(values, dict) and "1" in values:
            normalized_config[key] = {_branch_id() or "local-branch": values["1"]}
    current_node_cfg = load_config()
    saved_org_name = (
        normalized_config.get("company_profile", {}).get("name")
        or payload.get("organization_name")
        or payload.get("org_name")
        or current_node_cfg.get("org_name")
        or current_node_cfg.get("organization_name")
        or "Local Organization"
    )
    saved_branch_name = (
        normalized_config.get("branch_name")
        or payload.get("branch_name")
        or current_node_cfg.get("branch_name")
        or current_node_cfg.get("branchName")
        or "Main Branch"
    )
    saved = local_db.save_dashboard_config(
        branch_id,
        {
            **normalized_config,
            "organization_id": payload.get("organization_id") or current_node_cfg.get("org_id") or current_node_cfg.get("organization_id") or "local",
            "organization_name": saved_org_name,
            "org_name": saved_org_name,
            "branch_name": saved_branch_name,
            "branchName": saved_branch_name,
        },
    )
    configured_timezone = (normalized_config.get("company_profile") or {}).get("timezone")
    if configured_timezone:
        current_cfg = load_config()
        save_config({
            "branch": {
                **(current_cfg.get("branch") or {}),
                "timezone": str(configured_timezone).strip(),
            },
        })
    saved_cameras = saved.get("cameras")
    if isinstance(saved_cameras, dict):
        saved_cameras = saved_cameras.get(branch_id) or saved_cameras.get("1")
    if isinstance(saved_cameras, list):
        get_camera_stream_manager().sync_cameras(
            branch_id,
            get_enabled_cameras({"cameras": saved_cameras}),
        )
    return jsonify({**_dashboard_bootstrap(), "config": saved, "onboarding_config": saved})


@dashboard_bp.post("/client/onboarding/departments")
def api_local_add_department():
    """Local-node counterpart of the cloud's api_client_add_department
    (app.py). Additive sibling of onboarding/complete: that route replaces
    this branch's whole dashboard config with whatever the client currently
    holds in memory, so posting a partial config from a surface that only
    knows about departments/roles (the Add/Edit Staff modal) would wipe out
    camera/network/company-profile config. This does a locked
    read-modify-write of just the departments collection instead (see
    local_db.add_department).

    The frontend sends `branch_id` as the synthetic UI branch id (always
    `1` locally — see local_branch_ui_id above), but this node is
    single-branch and stores config under its own real branch id, so the
    UI's value is accepted but ignored in favor of `_branch_id()`, the same
    remapping onboarding/complete already does for departments/roles.
    """
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    branch_id = _branch_id() or "local-branch"
    try:
        department, branch_departments = local_db.add_department(branch_id, name)
        return jsonify({
            "success": True,
            "department": department,
            "departments": branch_departments,
        }), 200
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400


@dashboard_bp.post("/client/onboarding/designations")
def api_local_add_designation():
    """Local-node counterpart of the cloud's api_client_add_designation
    (app.py). Same additive read-modify-write rationale as
    api_local_add_department above — see that docstring."""
    data = request.get_json(silent=True) or {}
    department_id = data.get("department_id")
    name = data.get("name")
    branch_id = _branch_id() or "local-branch"
    try:
        designation, department_designations = local_db.add_designation(
            branch_id, department_id, name,
        )
        return jsonify({
            "success": True,
            "designation": designation,
            "designations": department_designations,
        }), 200
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400


# ── Account (self-service) ───────────────────────────────────────────────────
# Mirrors two cloud routes so client-dashboard's api.ts (changeOwnPassword /
# AuthContext.tsx's refreshUser) work unmodified against this node:
#   - PATCH /api/users/<id>/profile  <-> app.py's api_update_user_profile
#     (legacy/numeric-id accounts) — this node's admin id is always numeric,
#     so api.ts's isUuidLike() check always routes here, never to
#     /api/client/account/password.
#   - GET /api/users/<id>            <-> the session-refresh half of
#     support_db_client_users.get_client_user_session_by_id — called by
#     refreshUser() immediately after a successful password change.
# Both sit on dashboard_bp, so they're already behind
# check_login_and_license (see before_request above) before they run.

@dashboard_bp.patch("/users/<int:user_id>/profile")
def api_update_own_profile(user_id: int):
    """Self-service 'change my own password' for this node's single admin
    account. Identity comes from the verified token (get_current_admin),
    never from the URL — the user_id path segment is only cross-checked
    against it below, exactly like the cloud's self-only guards, so there
    is no id-spoofing surface even though this node has just one account.
    """
    admin = get_current_admin()
    if admin is None:
        return jsonify({
            "success": False,
            "message": "Your session could not be identified. Please sign in again.",
        }), 401

    if int(admin["id"]) != int(user_id):
        return jsonify({
            "success": False,
            "message": "You can only update your own profile.",
        }), 403

    data = request.get_json(silent=True) or {}
    new_password = str(data.get("new_password") or "").strip()
    if not new_password:
        # Name/email/phone editing isn't exposed on this endpoint yet — see
        # AccountSettings.tsx's docstring ("Add future self-only account
        # settings here"). ChangePasswordCard.tsx is this route's only
        # caller today and always sends new_password.
        return jsonify({"success": False, "message": "New password is required."}), 400

    try:
        validate_strong_password(new_password)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    branch_id = _branch_id()
    set_admin_password(branch_id, admin["email"], new_password, full_name=admin.get("full_name") or "")

    # Mint a fresh token so THIS request's own caller doesn't need to log
    # back in on its very next call — same reasoning as the cloud's
    # api_change_own_dashboard_password. Identity (branch_id + email)
    # never changes on a password change, only the token's issued-at
    # time, so re-using it here is correct rather than stale.
    refreshed_admin = local_db.get_admin_by_email(branch_id, admin["email"])
    fresh_token = issue_token(branch_id, admin["email"])

    return jsonify({
        "success": True,
        "message": "Password updated successfully.",
        "user": build_dashboard_user_payload(refreshed_admin, branch_id),
        "token": fresh_token,
    }), 200


@dashboard_bp.get("/users/<int:user_id>")
def api_get_own_user(user_id: int):
    """Companion to api_update_own_profile above — the exact URL
    AuthContext.tsx's refreshUser() calls right after a successful
    password change for a non-UUID (this node's) id. Returns the same
    shape /login returns so the frontend's normaliseUser treats a
    refresh identically to a fresh login."""
    admin = get_current_admin()
    if admin is None or int(admin["id"]) != int(user_id):
        return jsonify({"success": False, "message": "Not authenticated."}), 401

    return jsonify({
        "success": True,
        "user": build_dashboard_user_payload(admin, _branch_id()),
    }), 200




# ── Staff (People Management) ────────────────────────────────────────────────

@dashboard_bp.get("/staff")
def api_list_staff():
    include_archived = request.args.get("include_archived") == "1"
    department = request.args.get("department") or None
    return jsonify(local_db.list_staff(
        _branch_id(), include_archived=include_archived, department=department,
    ))


@dashboard_bp.get("/staff/archived")
def api_list_archived_staff():
    return jsonify(local_db.list_staff(_branch_id(), include_archived=True))


@dashboard_bp.get("/staff/<staff_id>")
def api_get_staff(staff_id: str):
    record = local_db.get_staff(_branch_id(), staff_id)
    if record is None:
        return jsonify({"success": False, "message": "Staff record not found"}), 404
    return jsonify(record)


@dashboard_bp.get("/staff/<staff_id>/photo")
def api_get_staff_photo(staff_id: str):
    # A live detection card only ever knows the person's person_code
    # (e.g. "STF-0002") — see camera_stream_manager's live_event["staff_id"]
    # — not this table's internal `id`, so resolve either before querying
    # the image columns. Without this, every detection-card photo request
    # 404s (even with a photo on file) and the UI silently falls back to
    # the live face-crop snapshot instead.
    resolved_id = local_db.resolve_staff_pk(_branch_id(), staff_id)
    if resolved_id is None:
        return jsonify({"success": False, "message": "Staff record not found"}), 404

    with local_db._connect() as conn:
        row = conn.execute(
            "SELECT profile_image_data, profile_image_mime "
            "FROM staff WHERE branch_id = ? AND id = ?",
            (_branch_id(), resolved_id),
        ).fetchone()

    if not row or not row[0]:
        return jsonify({"success": False, "message": "Profile image not found"}), 404

    return Response(
        row[0],
        mimetype=row[1] or "application/octet-stream",
        headers={"Cache-Control": "no-cache"},
    )


@dashboard_bp.post("/staff/<staff_id>/photo")
def api_upload_staff_photo(staff_id: str):
    photo = request.files.get("photo")
    if photo is None:
        return jsonify({"success": False, "message": "No photo provided"}), 400
    data = photo.read()
    if not data:
        return jsonify({"success": False, "message": "Photo is empty"}), 400
    resolved_id = local_db.resolve_staff_pk(_branch_id(), staff_id) or staff_id
    with local_db._write_lock:
        with local_db._connect() as conn:
            cur = conn.execute(
                "UPDATE staff SET profile_image_data = ?, profile_image_mime = ?, updated_at = ? "
                "WHERE branch_id = ? AND id = ?",
                (data, photo.mimetype or "application/octet-stream", local_db.utc_now(), _branch_id(), resolved_id),
            )
            conn.commit()
    if cur.rowcount == 0:
        return jsonify({"success": False, "message": "Staff record not found"}), 404
    return jsonify({"success": True, "profile_image_name": photo.filename or "profile-image"})


@dashboard_bp.post("/staff")
def api_create_staff():
    payload = request.get_json(silent=True) or {}
    branch_id = _branch_id()
    cfg = load_config()
    saved = local_db.get_dashboard_config(branch_id)
    vertical_config = saved.get("vertical_config") or cfg.get("vertical_config") or {}
    max_cap = (
        saved.get("max_capacity")
        or cfg.get("max_capacity")
        or vertical_config.get("max_users")
        or vertical_config.get("max_capacity")
        or saved.get("max_users")
        or cfg.get("max_users")
    )
    if max_cap is not None:
        try:
            limit = int(max_cap)
            if limit > 0:
                current_count = local_db.count_active_staff(branch_id)
                if current_count >= limit:
                    return jsonify({
                        "success": False,
                        "message": f"Organization staff capacity limit of {limit} reached. Cannot add more people.",
                    }), 400
        except (TypeError, ValueError):
            pass

    try:
        record = local_db.create_staff(branch_id, payload)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify(record), 201


@dashboard_bp.put("/staff/<staff_id>")
def api_update_staff(staff_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        record = local_db.update_staff(_branch_id(), staff_id, payload)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        if isinstance(exc, sqlite3.IntegrityError):
            return jsonify({"success": False, "message": "Staff ID conflicts with existing face enrollment data."}), 400
        raise
    if record is None:
        return jsonify({"success": False, "message": "Staff record not found"}), 404
    if any(key in payload for key in ("person_code", "personCode", "full_name", "fullName")):
        recognition_worker.invalidate_cache()
    return jsonify(record)


@dashboard_bp.post("/staff/<staff_id>/archive")
def api_archive_staff(staff_id: str):
    result = local_db.archive_staff(_branch_id(), staff_id)
    if result is None:
        return jsonify({"success": False, "message": "Staff record not found or already archived"}), 404
    # Embeddings were just deleted for this person — the recognition
    # engine's in-memory candidate cache must drop them immediately, or
    # the archived person keeps getting matched by cameras until the
    # cache's own natural refresh (see api_upload_training_video's
    # identical call after enrollment, a few routes up).
    recognition_worker.invalidate_cache()
    return jsonify({
        "success": True,
        "deleted_embeddings": result["deleted_embeddings"],
        "retention_days": result["retention_days"],
        "purge_after": result["purge_after"],
    })


@dashboard_bp.post("/staff/<staff_id>/restore")
def api_restore_staff(staff_id: str):
    ok = local_db.restore_staff(_branch_id(), staff_id)
    if not ok:
        return jsonify({"success": False, "message": "Staff record not found"}), 404
    return jsonify({"success": True})


@dashboard_bp.post("/staff/archived/<staff_id>/delete")
def api_delete_archived_staff(staff_id: str):
    branch_id = _branch_id()
    ok = local_db.delete_staff_permanently(branch_id, staff_id)
    if not ok:
        return jsonify({"success": False, "message": "Archived staff record not found"}), 404
    recognition_worker.invalidate_cache()
    return jsonify({"success": True, "deleted_user_id": staff_id, "deleted_count": 1})


@dashboard_bp.get("/client/branches/<branch_id>/shifts")
def api_local_list_shifts(branch_id: str):
    branch = _branch_id()
    people_type = request.args.get("people_type")
    shift_type = str(request.args.get("shift_type") or "main").lower()
    list_fn = local_db.list_break_shifts if shift_type == "break" else local_db.list_shifts
    shifts = list_fn(branch, people_type)
    # No auto-seeding here: this is a plain read. ShiftAllocationTab already
    # renders an explicit "No shifts exist yet — add one under Shift Timings"
    # empty state for this exact case, and ShiftTimingsModal owns the one
    # editable set of starter presets (DEFAULT_SHIFT_PRESETS) that the admin
    # reviews and explicitly saves. A second, backend-side set of defaults
    # silently created on GET only fights that flow — different preset
    # times, created behind the admin's back, and colliding with whatever
    # the modal tries to save (see ShiftNameConflict in local_db.create_shift).
    if shift_type in ("main", "break"):
        shifts = [row for row in shifts if row.get("shift_type", "main") == shift_type]
    return jsonify({"shifts": shifts})


@dashboard_bp.post("/client/branches/<branch_id>/shifts")
def api_local_create_shift(branch_id: str):
    try:
        shift = local_db.create_shift(_branch_id(), request.get_json(silent=True) or {})
    except local_db.ShiftNameConflict as exc:
        return jsonify({"success": False, "message": str(exc)}), 409
    return jsonify({"shift": shift}), 201


@dashboard_bp.patch("/client/branches/<branch_id>/shifts/<shift_id>")
def api_local_update_shift(branch_id: str, shift_id: str):
    row = local_db.update_shift(_branch_id(), shift_id, request.get_json(silent=True) or {})
    if row is None:
        return jsonify({"success": False, "message": "Shift not found"}), 404
    return jsonify({"shift": row, "warnings": []})


@dashboard_bp.delete("/client/branches/<branch_id>/shifts/<shift_id>")
def api_local_delete_shift(branch_id: str, shift_id: str):
    row = local_db.update_shift(_branch_id(), shift_id, {"is_active": 0})
    if row is None:
        return jsonify({"success": False, "message": "Shift not found"}), 404
    return jsonify({"deleted": True})


@dashboard_bp.patch("/client/staff/<staff_id>/shift")
def api_local_assign_shift(staff_id: str):
    payload = request.get_json(silent=True) or {}
    row = local_db.assign_staff_shift(_branch_id(), staff_id, payload.get("shift_id"))
    if row is None:
        return jsonify({"success": False, "message": "Staff member or shift not found"}), 404
    return jsonify({"staff": row})


@dashboard_bp.get("/client/staff/<staff_id>/break-shifts")
def api_local_list_staff_break_shifts(staff_id: str):
    return jsonify({"shifts": local_db.list_staff_break_shifts(_branch_id(), staff_id)})


@dashboard_bp.put("/client/staff/<staff_id>/break-shifts")
def api_local_assign_break_shifts(staff_id: str):
    payload = request.get_json(silent=True) or {}
    shift_ids = payload.get("shift_ids")
    if not isinstance(shift_ids, list):
        return jsonify({"success": False, "message": "shift_ids must be an array"}), 400
    try:
        row = local_db.assign_staff_break_shifts(_branch_id(), staff_id, shift_ids)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    if row is None:
        return jsonify({"success": False, "message": "Staff member not found"}), 404
    return jsonify({
        "success": True,
        "staff": row,
        "break_shifts": local_db.list_staff_break_shifts(_branch_id(), staff_id),
    })


# ── Enrollment ("face-training jobs") ────────────────────────────────────────

@dashboard_bp.post("/staff/<staff_id>/training-video")
def api_upload_training_video(staff_id: str):
    branch_id = _branch_id()
    record = local_db.get_staff(branch_id, staff_id)
    if record is None:
        return jsonify({"success": False, "message": "Staff record not found"}), 404

    video_file = request.files.get("video")
    if video_file is None:
        return jsonify({"success": False, "message": "No video file provided"}), 400

    # Written to a temp file rather than held in memory — enrollment clips
    # run 10-60MB and OpenCV's VideoCapture needs a real file path, not a
    # stream, on the codecs this ships with.
    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = Path(tmp_dir) / "enrollment.mp4"
        video_file.save(video_path)
        try:
            result = enroll_from_video(video_path)
        except EnrollmentError as exc:
            return jsonify({"success": False, "message": str(exc)}), 400

    local_db.upsert_person_embeddings(
        branch_id=branch_id,
        people_type=record["people_type"],
        person_code=record["person_code"],
        full_name=record["full_name"],
        embeddings=result["embeddings"],
        model_version=result["model_version"],
        source_package_id="local-enrollment",
    )
    # Pre-existing bug fixed here: this used to read
    # `local_db.recognition_worker.invalidate_cache()`, but local_db.py
    # never imports recognition_worker — that raised AttributeError on
    # every successful enrollment upload. recognition_worker is imported
    # directly at the top of this file (see line ~38); use that binding,
    # same as api_archive_staff/api_delete_archived_staff above.
    recognition_worker.invalidate_cache()  # new embeddings must be picked up immediately

    return jsonify({
        "success": True,
        "frame_count": result["frame_count"],
        "usable_frame_count": result["usable_frame_count"],
    })


# ── Attendance View ───────────────────────────────────────────────────────────

@dashboard_bp.get("/attendance/view")
def api_attendance_view():
    """Shapes attendance_buffer rows into what AttendanceView.tsx expects.

    This client has no payroll module — check-in/checkout is the entire
    scope, so checkInStatus/checkOutStatus are trivial reads of columns
    record_attendance_local already computed at write time (on_time/late
    for check-in; on_time/None for checkout — a checkout is never "late"
    here, since anything outside its window never confirms at all, see
    local_db.record_attendance_local). No shift re-resolution needed here:
    the shift gate already made this decision once, atomically, when the
    detection was recorded.
    """
    limit = int(request.args.get("limit") or 100)
    rows = local_db.recent_attendance(_branch_id(), limit=limit)
    return jsonify([_shape_attendance_row(r) for r in rows])


@dashboard_bp.get("/attendance/today")
def api_attendance_today():
    requested_date = request.args.get("date")
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    rows = local_db.recent_attendance(_branch_id(), limit=5000)
    if requested_date:
        rows = [
            row
            for row in rows
            if str(row.get("attendance_date")) == requested_date
        ]
    elif start_date or end_date:
        rows = [
            row
            for row in rows
            if (not start_date or str(row.get("attendance_date")) >= start_date)
            and (not end_date or str(row.get("attendance_date")) <= end_date)
        ]
    return jsonify([_shape_attendance_row(row) for row in rows])


@dashboard_bp.get("/attendance/breaks/today")
def api_break_attendance_today():
    """Return today's Namaz/break attendance rows for review."""
    today = local_db._today()
    local_db.finalize_expired_break_attendance(_branch_id(), today)
    return jsonify(local_db.list_break_attendance(_branch_id(), attendance_date=today))


@dashboard_bp.get("/attendance/breaks")
def api_break_attendance():
    requested_date = request.args.get("date")
    start_date = request.args.get("start")
    end_date = request.args.get("end")
    if requested_date:
        local_db.finalize_expired_break_attendance(_branch_id(), requested_date)
    else:
        local_db.finalize_expired_break_attendance(_branch_id(), end_date)
    return jsonify(local_db.list_break_attendance(
        _branch_id(),
        attendance_date=requested_date,
        start_date=start_date,
        end_date=end_date,
        status=request.args.get("status") or None,
        limit=int(request.args.get("limit") or 500),
    ))


@dashboard_bp.patch("/attendance/breaks/<record_id>")
def api_update_break_attendance(record_id: str):
    payload = request.get_json(silent=True) or {}
    outside_at = payload.get("outside_at", payload.get("outsideAt"))
    returned_at = payload.get("returned_at", payload.get("returnedAt"))
    try:
        row = local_db.update_break_attendance(
            _branch_id(),
            str(record_id),
            outside_at=str(outside_at) if outside_at else None,
            returned_at=str(returned_at) if returned_at else None,
            correction_reason=str(
                payload.get("correction_reason")
                or payload.get("correctionReason")
                or ""
            ).strip() or None,
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    if row is None:
        return jsonify({"success": False, "message": "Break attendance record not found."}), 404
    return jsonify({"success": True, "record": row})


@dashboard_bp.post("/attendance/breaks/finalize")
def api_finalize_break_attendance():
    payload = request.get_json(silent=True) or {}
    attendance_date = str(
        payload.get("date") or request.args.get("date") or ""
    ).strip() or None
    finalized = local_db.finalize_expired_break_attendance(_branch_id(), attendance_date)
    return jsonify({
        "success": True,
        "finalized_count": finalized,
        "records": local_db.list_break_attendance(
            _branch_id(), attendance_date=attendance_date, limit=500,
        ),
    })


@dashboard_bp.post("/attendance/mark-absent")
def api_mark_absent():
    payload = request.get_json(silent=True) or {}
    staff_id = str(
        payload.get("user_id")
        or payload.get("staff_id")
        or payload.get("staffId")
        or ""
    ).strip()
    if not staff_id:
        return jsonify({"success": False, "message": "user_id is required"}), 400

    branch_id = _branch_id()
    staff = local_db.get_staff(branch_id, staff_id)
    if staff is None:
        staff = next(
            (
                item
                for item in local_db.list_staff(branch_id, include_archived=True)
                if (
                    str(item.get("person_code") or "") == staff_id
                    or (
                        staff_id.isdigit()
                        and str(item.get("person_code") or "").isdigit()
                        and int(item["person_code"]) == int(staff_id)
                    )
                )
            ),
            None,
        )
    if staff is None:
        staff = next(
            (
                item
                for item in local_db.recent_attendance(branch_id, limit=5000)
                if (
                    str(item.get("person_code") or "") == staff_id
                    or (
                        staff_id.isdigit()
                        and str(item.get("person_code") or "").isdigit()
                        and int(item["person_code"]) == int(staff_id)
                    )
                )
            ),
            None,
        )
    if staff is None:
        return jsonify({"success": False, "message": "Employee not found."}), 404

    attendance_date = str(payload.get("date") or request.args.get("date") or "").strip()
    if not attendance_date:
        attendance_date = local_db._today()
    people_type = str(staff.get("people_type") or "staff")
    person_code = str(staff.get("person_code") or staff_id)

    deleted = local_db.delete_attendance_for_staff(
        branch_id,
        person_code,
        attendance_date,
        people_type=people_type,
        staff_name=str(staff.get("full_name") or staff.get("staff_name") or ""),
    )
    removed_live_events = live_events.suppress_person_for_today(
        person_code,
        str(staff.get("full_name") or staff.get("staff_name") or ""),
    )

    return jsonify({
        "success": True,
        "record": None,
        "status": "absent",
        "deleted_rows": deleted,
        "removed_live_events": removed_live_events,
    })


@dashboard_bp.post("/attendance/manual")
def api_manual_attendance():
    payload = request.get_json(silent=True) or {}
    staff_id = str(payload.get("staff_id") or payload.get("staffId") or "").strip()
    if not staff_id:
        return jsonify({"success": False, "message": "Employee is required."}), 400

    staff = local_db.get_staff(_branch_id(), staff_id)
    if staff is None:
        return jsonify({"success": False, "message": "Employee not found."}), 404

    check_in = payload.get("check_in") or payload.get("checkIn")
    check_out = payload.get("check_out") or payload.get("checkOut")
    attendance_date = str(payload.get("attendance_date") or "")
    if not attendance_date:
        source_time = check_in or check_out
        attendance_date = str(source_time or local_db.utc_now())[:10]

    local_db.record_attendance_manual(
        _branch_id(),
        str(staff.get("people_type") or "staff"),
        str(staff.get("person_code") or staff.get("id") or staff_id),
        str(staff.get("full_name") or staff.get("name") or ""),
        1.0,
        attendance_date,
        check_in_marked_at=check_in,
        check_out_marked_at=check_out,
        metadata={
            "arrival_status": payload.get("arrival_status") or payload.get("arrivalStatus"),
            "manual": True,
        },
    )

    rows = local_db.recent_attendance(_branch_id(), limit=5000)
    row = next(
        (
            item
            for item in rows
            if str(item.get("person_code")) == str(staff.get("person_code"))
            and str(item.get("attendance_date")) == attendance_date
        ),
        None,
    )
    return jsonify({"success": True, "record": _shape_attendance_row(row or {})})


def _patch_local_attendance(record_id: str):
    payload = request.get_json(silent=True) or {}
    raw_check_in = payload.get("check_in", payload.get("checkIn"))
    raw_check_out = payload.get("check_out", payload.get("checkOut"))
    arrival_status = str(
        payload.get("arrival_status")
        or payload.get("arrivalStatus")
        or ""
    ).strip().lower() or None
    raw_notes = payload.get("notes")
    notes = str(raw_notes).strip() if raw_notes else None

    row = local_db.update_attendance_record(
        _branch_id(),
        str(record_id),
        check_in=str(raw_check_in) if raw_check_in else None,
        check_out=str(raw_check_out) if raw_check_out else None,
        arrival_status=arrival_status,
        notes=notes,
        check_in_provided="check_in" in payload or "checkIn" in payload,
        check_out_provided="check_out" in payload or "checkOut" in payload,
        notes_provided="notes" in payload,
    )
    if row is None:
        return jsonify({"success": False, "message": "Attendance record not found or unchanged."}), 404
    return jsonify({"success": True, "record": _shape_attendance_row(row)})


@dashboard_bp.patch("/attendance/<record_id>")
def api_update_attendance(record_id: str):
    return _patch_local_attendance(record_id)


@dashboard_bp.patch("/attendance/manual/<record_id>")
def api_update_manual_attendance(record_id: str):
    return _patch_local_attendance(record_id)


def _shape_attendance_row(row: dict) -> dict:
    check_out_confirmed = bool(row.get("check_out_confirmed"))
    is_absent = str(row.get("status") or "").lower() == "absent"
    check_in_time = None if is_absent else row.get("marked_at")
    check_out_time = row.get("check_out_marked_at") if check_out_confirmed and not is_absent else None
    check_out_attempted_at = (
        row.get("check_out_marked_at")
        if not is_absent and not check_out_confirmed
        else None
    )
    person_code = row.get("person_code")

    #     # A hand-entered row is always written with source='manual_override'
#     # (local_db.record_attendance_manual's default, and
#     # manual_instructions_worker's explicit value); every camera-detected
#     # row goes through record_attendance_local, whose source defaults to
#     # 'camera'. That column is therefore the one reliable signal for the
#     # dashboard's Channel column — captureChannel must NOT be hardcoded to
#     # "local_node" for every row, or a manually-added record renders with
#     # the same "Camera" badge as a real camera detection.
    is_manual_row = str(row.get("source") or "").lower() == "manual_override"
    return {
        "id": row.get("id") or row.get("local_event_id"),
        "user_id": person_code,
        "staff_id": person_code,
        "personCode": row["person_code"],
        "peopleType": row["people_type"],
        "staffName": row.get("staff_name"),
        "user_name": row.get("staff_name"),
        "date": row["attendance_date"],
        "attendance_date": row["attendance_date"],
        "timestamp": check_in_time,
        "check_in": check_in_time,
        "checkInTime": check_in_time,
        # Only a CONFIRMED checkout counts as an attendance checkout — an
        # out-of-window sighting is informational only (see
        # checkOutHoldReason) and must never read as a real checkout here.
        "check_out": check_out_time,
        "checkOutTime": check_out_time,
        # Keep an out-of-window sighting separate from a confirmed checkout.
        # The attendance table can show when it happened without treating it
        # as a payable/work-duration checkout.
        "checkOutAttemptedAt": check_out_attempted_at,
        "check_out_attempted_at": check_out_attempted_at,
        "checkInConfidence": None if is_absent else row.get("confidence"),
        "checkOutConfidence": row.get("check_out_confidence") if check_out_confirmed and not is_absent else None,
        "checkOutCameraId": row.get("check_out_camera_id") if check_out_confirmed and not is_absent else None,
        # Check-in-side camera (as opposed to checkOutCameraId above) — added
        # so the dashboard's Channel column can show which camera actually
        # captured this row instead of the generic "Local Node" label for
        # every row regardless of source. Null for a manually-marked row
        # (record_attendance_local's camera_id is only ever set by the
        # camera/recognition pipeline, never by the manual-mark path — see
        # local_db.py's record_attendance_local vs. its manual counterpart).
        "cameraId": None if (is_absent or is_manual_row) else row.get("camera_id"),
        "captureChannel": None if is_absent else ("manual" if is_manual_row else "local_node"),
        "notes": None if is_absent else row.get("notes"),
        # Informational only — set when a checkout sighting fell outside its
        # window and was therefore never confirmed (see checkOutTime above).
        "checkOutHoldReason": None if is_absent or check_out_confirmed else row.get("check_out_hold_reason"),
        "checkInStatus": None if is_absent else ("late" if row.get("check_in_hold_reason") == "late" else "on_time"),
        "checkOutStatus": None if is_absent else ("on_time" if check_out_confirmed else None),
        "dayStatus": row.get("status") or "present",
        "workDuration": None,
    }


# ── Shift settings (also on the Staff Management page) ──────────────────────
# Written straight into node_config.json — the exact structure
# shift_gate.py already reads (shift_windows, shift_mode_enabled). Per-
# staff personal overrides are edited via the staff endpoints above and
# materialized into staff_shift_windows by
# local_db._rebuild_staff_shift_windows; this endpoint only owns the
# branch-level default per people_type.

@dashboard_bp.get("/settings/shifts")
def api_get_shift_settings():
    cfg = load_config()
    return jsonify({
        "shift_mode_enabled": bool(cfg.get("shift_mode_enabled", False)),
        "shift_windows": cfg.get("shift_windows") or {},
    })


@dashboard_bp.put("/settings/shifts")
def api_update_shift_settings():
    payload = request.get_json(silent=True) or {}
    shift_windows = payload.get("shift_windows")
    if not isinstance(shift_windows, dict):
        return jsonify({"success": False, "message": "shift_windows must be an object keyed by people_type"}), 400
    save_config({
        "shift_mode_enabled": bool(payload.get("shift_mode_enabled", False)),
        "shift_windows": shift_windows,
    })
    return jsonify({"success": True})


# ── Weekly backup (morning popup) ────────────────────────────────────────────

@dashboard_bp.get("/backup/status")
def api_backup_status():
    return jsonify(backup_worker.status())


@dashboard_bp.post("/backup/consent")
def api_backup_consent():
    payload = request.get_json(silent=True) or {}
    allow = bool(payload.get("allow"))
    return jsonify(backup_worker.run_backup_if_consented(allow))