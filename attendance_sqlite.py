"""
attendance_sqlite.py

Local SQLite attendance store for the cloud backend — step one of moving
this system off Supabase. Originally scoped to ONLY camera-marked
attendance (what CameraStreamManager's detector loop in app.py writes on
a face match); now also holds hand-entered rows from the Client
Dashboard's "Add/Edit Attendance" form (create_manual_attendance /
update_attendance_row below), because /api/attendance and
/api/attendance/today (app.py) read exclusively from get_dashboard_logs()
for every UUID org — a manual row written anywhere else (e.g. Supabase's
`attendance` table) is invisible to this page no matter what its
source/capture_channel columns say. Staff directory, embeddings, and
everything else this page doesn't touch stay wherever they already live
(see support_cp_db) — migrating everything at once would make this page
impossible to verify in isolation. The table is still named
`camera_attendance` for now to avoid a churny rename across every query
in this file; `source`/`capture_channel` is what actually distinguishes
a camera row from a manual one, not the table name.

Unlike local_node's local_db.py (one machine, one branch, no org
concept), this file is multi-tenant: every table and every query is
scoped by (organization_id, branch_id), because one cloud backend serves
every org. Treat a missing organization_id filter on any query here as a
bug, the same way support_cp_db.py treats a missing org_id filter as a
cross-tenant leak.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

import cloud_shift_gate

# Overridable via env for deployments where the writable disk isn't ./data
# (e.g. a mounted volume on Railway) — falls back to a local ./data folder
# for dev.
DB_PATH = os.environ.get("ATTENDANCE_SQLITE_PATH", os.path.join("data", "attendance.db"))

_write_lock = threading.Lock()
_local = threading.local()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_utc() -> str:
    # No per-branch timezone/shift-gate concept has been ported yet (that's
    # local_node-only today) — attendance_date is plain UTC calendar date
    # until that's migrated too. Documented here so nobody assumes this
    # matches a branch's local midnight.
    return datetime.now(timezone.utc).date().isoformat()


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _local.conn = conn
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS camera_attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id TEXT NOT NULL,
            branch_id TEXT,
            people_type TEXT,
            person_code TEXT NOT NULL,
            staff_name TEXT,
            confidence REAL,
            camera_id TEXT,
            source TEXT,
            attendance_date TEXT NOT NULL,
            marked_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_camera_attendance_daily
        ON camera_attendance (organization_id, COALESCE(branch_id, ''), person_code, attendance_date)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_camera_attendance_org_date "
        "ON camera_attendance (organization_id, attendance_date)"
    )
    _ensure_gate_columns(conn)
    conn.commit()


def _ensure_gate_columns(conn: sqlite3.Connection) -> None:
    """PRAGMA-guarded ALTER TABLE, same pattern local_node's local_db.py
    uses — CREATE TABLE IF NOT EXISTS above won't retrofit these columns
    onto a camera_attendance table that already existed before the new
    shift gate (record_gated_attendance) shipped.

    check_in_status: 'on_time' | 'late' | 'unscheduled' — set once, on
      the first (and only) check-in write, by cloud_shift_gate.classify_check_in.
      Never revisited afterwards.
    check_out_marked_at / check_out_confidence / check_out_camera_id:
      the checkout leg's own columns, separate from the check-in columns
      above (marked_at/confidence/camera_id) so a person's check-in and
      checkout can be attributed to different cameras/confidences, same
      split local_node's attendance_buffer uses.
    check_out_status: 'checked_out' once check_out_marked_at is set by a
      confirmed (in-grace-window) sighting; NULL until then.
    last_seen_at: updated on EVERY sighting after check-in, including
      ones outside the checkout grace window that do NOT confirm a
      checkout (cloud_shift_gate.classify_check_out_action ==
      'info_only') — this is the "capture the timestamp for information
      but don't mark as checked out" column the new gate's spec calls
      for. Also updated on a confirmed checkout, so it always reflects
      the most recent sighting regardless of leg.
    """
    existing = {row[1] for row in conn.execute("PRAGMA table_info(camera_attendance)").fetchall()}
    migrations = {
        "check_in_status": "ALTER TABLE camera_attendance ADD COLUMN check_in_status TEXT",
        "check_out_marked_at": "ALTER TABLE camera_attendance ADD COLUMN check_out_marked_at TEXT",
        "check_out_confidence": "ALTER TABLE camera_attendance ADD COLUMN check_out_confidence REAL",
        "check_out_camera_id": "ALTER TABLE camera_attendance ADD COLUMN check_out_camera_id TEXT",
        "check_out_status": "ALTER TABLE camera_attendance ADD COLUMN check_out_status TEXT",
        "last_seen_at": "ALTER TABLE camera_attendance ADD COLUMN last_seen_at TEXT",
        # Free-text operator note -- only ever set/read by the manual
        # create/edit path below (save_manual_attendance_record's
        # equivalent). NULL for every camera-written row.
        "notes": "ALTER TABLE camera_attendance ADD COLUMN notes TEXT",
    }
    for column, ddl in migrations.items():
        if column not in existing:
            conn.execute(ddl)


def record_attendance(
    *,
    org_id: str,
    branch_id: str | None,
    person_code: str,
    staff_name: str | None,
    confidence: float,
    source: str,
    camera_id: str,
    people_type: str | None = None,
) -> dict[str, Any]:
    """Marks one person present for today, once. Matches the shape
    support_cp_db.record_cloud_camera_attendance returned (`already_marked`
    key) so app.py's detector loop needs no branching on which store it's
    talking to.

    Dedup is enforced by the DB itself (the unique index above), not a
    SELECT-then-INSERT check — closes the TOCTOU race two near-simultaneous
    detections on the same person (two cameras catching the same doorway
    entrance) would otherwise hit.
    """
    org_id = str(org_id)
    branch_id = str(branch_id) if branch_id else None
    today = _today_utc()
    now = _utc_now_iso()

    with _write_lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO camera_attendance
                    (organization_id, branch_id, people_type, person_code, staff_name,
                     confidence, camera_id, source, attendance_date, marked_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    org_id, branch_id, people_type, str(person_code), staff_name,
                    float(confidence), camera_id, source, today, now, now,
                ),
            )
            conn.commit()
            return {
                "already_marked": False,
                "id": cur.lastrowid,
                "marked_at": now,
                "attendance_date": today,
            }
        except sqlite3.IntegrityError:
            # Already marked today for this org/branch/person — the normal,
            # expected outcome on every repeat sighting after the first.
            return {"already_marked": True, "marked_at": None, "attendance_date": today}


def record_gated_attendance(
    *,
    org_id: str,
    branch_id: str | None,
    staff: dict[str, Any],
    people_type: str | None,
    person_code: str,
    staff_name: str | None,
    confidence: float,
    source: str,
    camera_id: str,
    event_dt_utc: datetime | None = None,
) -> dict[str, Any]:
    """The new no-held-review shift gate, wired into CameraDetector's live
    detection loop (app.py) in place of the plain record_attendance()
    above. See cloud_shift_gate.py's module docstring for the full rules;
    summary:

      - No row yet today -> CHECK-IN. Confirmed immediately as 'on_time'
        or 'late' depending on whether this detection landed at/before
        the check-in grace deadline. Nothing is ever held.
      - Row already has a check-in -> CHECK-OUT candidate. A detection
        inside the checkout grace window confirms/overwrites the
        checkout (last one inside the window wins). A detection outside
        that window only updates last_seen_at — informational, never
        the official checkout, never held for later review either.

    `staff` must carry the shift-linkage fields cloud_shift_gate.
    resolve_window needs: id, shift_id_ref, check_in_grace_override,
    check_out_grace_override, person_code — exactly what
    SUPABASE_EMBEDDING_CACHE's per-person 'staff' entry now carries (see
    app.py's refresh_supabase_embedding_cache).

    Same table as record_attendance() (camera_attendance), same
    (org, branch, person, date) row — the two functions are not meant to
    run against the same person on the same day; a branch/org uses one
    gate or the other, chosen by whichever code path calls in.
    """
    org_id = str(org_id)
    branch_id = str(branch_id) if branch_id else None
    person_code = str(person_code)
    event_dt = event_dt_utc or datetime.now(timezone.utc)
    now = event_dt.isoformat() if event_dt_utc else _utc_now_iso()

    window = cloud_shift_gate.resolve_window(
        org_id=org_id, branch_id=branch_id, staff=staff,
        people_type=people_type or "staff", event_dt_utc=event_dt,
    )
    branch_zone = cloud_shift_gate.get_branch_zone(org_id, branch_id)
    today = event_dt.astimezone(branch_zone).date().isoformat()

    with _write_lock:
        conn = _connect()
        existing = conn.execute(
            "SELECT * FROM camera_attendance WHERE organization_id = ? AND COALESCE(branch_id, '') = ? "
            "AND person_code = ? AND attendance_date = ?",
            (org_id, branch_id or "", person_code, today),
        ).fetchone()

        if existing is None:
            status = cloud_shift_gate.classify_check_in(window, event_dt, branch_zone)
            try:
                cur = conn.execute(
                    """
                    INSERT INTO camera_attendance
                        (organization_id, branch_id, people_type, person_code, staff_name,
                         confidence, camera_id, source, attendance_date, marked_at, created_at,
                         check_in_status, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        org_id, branch_id, people_type, person_code, staff_name,
                        float(confidence), camera_id, source, today, now, now,
                        status, now,
                    ),
                )
                conn.commit()
                return {
                    "already_marked": False, "event_type": "check_in", "status": status,
                    "id": cur.lastrowid, "marked_at": now, "attendance_date": today,
                }
            except sqlite3.IntegrityError:
                # Lost a race with a near-simultaneous detection on another
                # camera for the same person — same dedup guard
                # record_attendance() relies on. Fall through to the
                # checkout branch below against whatever row won.
                existing = conn.execute(
                    "SELECT * FROM camera_attendance WHERE organization_id = ? AND COALESCE(branch_id, '') = ? "
                    "AND person_code = ? AND attendance_date = ?",
                    (org_id, branch_id or "", person_code, today),
                ).fetchone()
                if existing is None:
                    raise

        # Check-in already exists -> this is a checkout candidate.
        action = cloud_shift_gate.classify_check_out_action(window, event_dt, branch_zone)
        if action == "confirm":
            # Only an in-window sighting may create or replace the official
            # checkout. Early detections and branches without a checkout
            # window fall through to the informational path below.
            conn.execute(
                "UPDATE camera_attendance SET check_out_marked_at = ?, check_out_confidence = ?, "
                "check_out_camera_id = ?, check_out_status = 'checked_out', last_seen_at = ? WHERE id = ?",
                (now, float(confidence), camera_id, now, existing["id"]),
            )
            conn.commit()
            return {
                "already_marked": False, "event_type": "check_out", "status": "checked_out",
                "id": existing["id"], "marked_at": now, "attendance_date": today,
            }

        # info_only: outside the checkout grace window on either side.
        # Never overwrites check_out_marked_at — only records that this
        # person was seen, for information, without touching the official
        # checkout leg.
        conn.execute(
            "UPDATE camera_attendance SET last_seen_at = ? WHERE id = ?",
            (now, existing["id"]),
        )
        conn.commit()
        return {
            "already_marked": True, "event_type": "check_out_info_only", "status": "info_only",
            "id": existing["id"], "marked_at": existing["check_out_marked_at"], "attendance_date": today,
        }


def get_today_stats(org_id: str, branch_id: str | None = None, people_type: str | None = None) -> dict[str, Any]:
    """Feeds /api/stats's today_attendance/unique_users_today/recent_entries
    fields for camera-marked attendance — see app.py's get_stats(), which
    merges this in on top of whatever Supabase still supplies for
    total_users/enrolled_users (not migrated yet)."""
    org_id = str(org_id)
    today = _today_utc()
    conn = _connect()

    where = ["organization_id = ?", "attendance_date = ?"]
    params: list[Any] = [org_id, today]
    if branch_id:
        where.append("branch_id = ?")
        params.append(str(branch_id))
    if people_type:
        where.append("people_type = ?")
        params.append(people_type)
    where_sql = " AND ".join(where)

    row = conn.execute(
        f"SELECT COUNT(*) AS cnt, COUNT(DISTINCT person_code) AS uniq, AVG(confidence) AS avg_conf "
        f"FROM camera_attendance WHERE {where_sql}",
        params,
    ).fetchone()

    recent = conn.execute(
        f"SELECT person_code, staff_name, confidence, camera_id, marked_at "
        f"FROM camera_attendance WHERE {where_sql} ORDER BY marked_at DESC LIMIT 10",
        params,
    ).fetchall()

    return {
        "today_count": row["cnt"] or 0,
        "unique_users_today": row["uniq"] or 0,
        "avg_confidence": round(row["avg_conf"], 4) if row["avg_conf"] is not None else 0,
        "recent_entries": [
            {
                "person_code": r["person_code"],
                "staff_name": r["staff_name"],
                "confidence": r["confidence"],
                "camera_id": r["camera_id"],
                "marked_at": r["marked_at"],
            }
            for r in recent
        ],
    }


def list_recent(org_id: str, branch_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    org_id = str(org_id)
    conn = _connect()
    where = ["organization_id = ?"]
    params: list[Any] = [org_id]
    if branch_id:
        where.append("branch_id = ?")
        params.append(str(branch_id))
    where_sql = " AND ".join(where)

    rows = conn.execute(
        f"SELECT * FROM camera_attendance WHERE {where_sql} "
        f"ORDER BY marked_at DESC LIMIT ?",
        params + [int(limit)],
    ).fetchall()
    return [dict(r) for r in rows]


def _row_to_dashboard_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Shape one camera_attendance row into the dict the Client Dashboard's
    attendance table expects. Shared by get_dashboard_logs (list reads) and
    create_manual_attendance/update_attendance_row (so a create/edit
    response matches the shape a subsequent GET would return, no second
    round-trip needed).

    capture_channel is derived from the row's own `source` column, not
    hardcoded -- a hand-entered row always has source='manual' (set
    explicitly by create_manual_attendance, never left to a column
    default), so that's the one signal this table can trust the same way
    the frontend trusts it. Every other row on this table only ever comes
    from the camera detector loop (record_gated_attendance /
    record_attendance), so 'cloud' is accurate for anything that isn't
    'manual' -- there is no local_node/mobile_app row in this table.
    """
    is_manual = row["source"] == "manual"
    return {
        "id": f"sqlite:{row['id']}",
        "user_id": row["person_code"],
        "staff_id": row["person_code"],
        "name": row["staff_name"] or "Unknown",
        "staff_name": row["staff_name"] or "Unknown",
        "people_type": row["people_type"] or "staff",
        "branch_id": row["branch_id"],
        "backend_branch_id": row["branch_id"],
        "timestamp": row["marked_at"],
        "check_in": row["marked_at"],
        "check_out": row["check_out_marked_at"],
        "status": "CHECKED_OUT" if row["check_out_marked_at"] else "CHECKED_IN",
        "check_in_status": row["check_in_status"] or "unscheduled",
        "notes": row["notes"] if "notes" in row.keys() else None,
        "confidence": row["confidence"] or 0,
        "check_out_confidence": row["check_out_confidence"],
        "camera_id": None if is_manual else row["camera_id"],
        "check_out_camera_id": None if is_manual else row["check_out_camera_id"],
        "source": row["source"] or "camera",
        "capture_channel": "manual" if is_manual else "cloud",
        "log_date": row["attendance_date"],
    }


def get_dashboard_logs(
    org_id: str,
    branch_id: str | None = None,
    limit: int = 500,
    people_type: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict[str, Any]]:
    """Return local attendance (camera-marked + hand-entered) in the
    dashboard attendance shape."""
    org_id = str(org_id)
    conn = _connect()
    where = ["organization_id = ?"]
    params: list[Any] = [org_id]
    if branch_id:
        where.append("branch_id = ?")
        params.append(str(branch_id))
    if people_type:
        where.append("people_type = ?")
        params.append(str(people_type))
    if start:
        where.append("attendance_date >= ?")
        params.append(str(start)[:10])
    if end:
        where.append("attendance_date <= ?")
        params.append(str(end)[:10])

    rows = conn.execute(
        "SELECT * FROM camera_attendance WHERE "
        + " AND ".join(where)
        + " ORDER BY marked_at DESC LIMIT ?",
        params + [max(1, min(int(limit or 500), 5000))],
    ).fetchall()

    return [_row_to_dashboard_dict(row) for row in rows]


def get_row(org_id: str, row_id: int) -> dict[str, Any] | None:
    """Fetch one row by its numeric id (the part after 'sqlite:' in the
    dashboard-facing id), scoped to org_id. Returns the raw sqlite3.Row's
    dashboard-shaped dict, or None if it doesn't exist / belongs to
    another org."""
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM camera_attendance WHERE id = ? AND organization_id = ?",
        (int(row_id), str(org_id)),
    ).fetchone()
    return _row_to_dashboard_dict(row) if row else None


def create_manual_attendance(
    *,
    org_id: str,
    branch_id: str | None,
    people_type: str | None,
    person_code: str,
    staff_name: str | None,
    attendance_date: str,
    check_in_iso: str,
    check_out_iso: str | None,
    check_in_status: str | None,
    notes: str | None,
) -> dict[str, Any]:
    """Hand-entered row from the Client Dashboard's 'Add Attendance' form
    -- the manual-create counterpart to record_gated_attendance. Always
    source='manual', confidence=1.0 (a manually-entered record is, by
    definition, fully confident -- same reasoning the old Supabase-backed
    save_manual_attendance_record used).

    One row per (org, branch, person, date), enforced by the same unique
    index record_attendance/record_gated_attendance rely on -- raises
    ValueError on a duplicate rather than silently forking the day into
    two rows, same contract callers already expect from the Supabase
    version of this function.
    """
    org_id = str(org_id)
    branch_id = str(branch_id) if branch_id else None
    person_code = str(person_code)
    now = _utc_now_iso()

    with _write_lock:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO camera_attendance
                    (organization_id, branch_id, people_type, person_code, staff_name,
                     confidence, camera_id, source, attendance_date, marked_at, created_at,
                     check_in_status, check_out_marked_at, check_out_status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    org_id, branch_id, people_type, person_code, staff_name,
                    1.0, None, "manual", attendance_date, check_in_iso, now,
                    check_in_status or "on_time",
                    check_out_iso,
                    "checked_out" if check_out_iso else None,
                    notes,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise ValueError(
                "An attendance record already exists for this employee on this date. "
                "Edit the existing record instead of adding a new one.",
            )
        row = conn.execute(
            "SELECT * FROM camera_attendance WHERE id = ?", (cur.lastrowid,),
        ).fetchone()
        return _row_to_dashboard_dict(row)


def update_attendance_row(org_id: str, row_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    """Partial edit of one row (manual or camera-written) by numeric id --
    the shared backend for both the 'Add/Edit Attendance' modal's edit
    mode and the inline pencil-edit on any row in the table. Never touches
    source/capture_channel -- an operator correcting a camera row's
    checkout time doesn't turn it into a manual row, same principle the
    old Supabase-backed update_client_attendance_record used.

    `updates` keys (all optional, same names the callers already build):
      - timestamp: ISO datetime -> marked_at
      - check_out_timestamp: ISO datetime or None -> check_out_marked_at
        (None clears the checkout and its status together)
      - status: 'on_time' | 'late' | 'early' | 'unscheduled' -> check_in_status
      - notes: string or None
    """
    org_id = str(org_id)
    conn = _connect()
    existing = conn.execute(
        "SELECT * FROM camera_attendance WHERE id = ? AND organization_id = ?",
        (int(row_id), org_id),
    ).fetchone()
    if existing is None:
        raise ValueError("Attendance record not found")

    set_clauses: list[str] = []
    params: list[Any] = []
    if "timestamp" in updates:
        set_clauses.append("marked_at = ?")
        params.append(updates["timestamp"])
    if "check_out_timestamp" in updates:
        check_out_val = updates["check_out_timestamp"]
        set_clauses.append("check_out_marked_at = ?")
        params.append(check_out_val)
        set_clauses.append("check_out_status = ?")
        params.append("checked_out" if check_out_val else None)
    if "status" in updates:
        set_clauses.append("check_in_status = ?")
        params.append(updates["status"])
    if "notes" in updates:
        set_clauses.append("notes = ?")
        params.append(updates["notes"])

    if not set_clauses:
        raise ValueError("No editable fields were provided")

    with _write_lock:
        conn.execute(
            f"UPDATE camera_attendance SET {', '.join(set_clauses)} WHERE id = ? AND organization_id = ?",
            params + [int(row_id), org_id],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM camera_attendance WHERE id = ?", (int(row_id),),
        ).fetchone()
        return _row_to_dashboard_dict(row)


def clear_today_for_person(org_id: str, branch_id: str | None, person_code: str) -> bool:
    """Undo one accidental mark — e.g. an operator clears a false-positive
    match. Only touches today's row for this exact org/branch/person, same
    scoping discipline as every read above."""
    org_id = str(org_id)
    today = _today_utc()
    with _write_lock:
        conn = _connect()
        cur = conn.execute(
            "DELETE FROM camera_attendance WHERE organization_id = ? AND COALESCE(branch_id, '') = ? "
            "AND person_code = ? AND attendance_date = ?",
            (org_id, str(branch_id) if branch_id else "", str(person_code), today),
        )
        conn.commit()
        return cur.rowcount > 0