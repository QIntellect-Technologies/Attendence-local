"""Local SQLite store for dashboard staff and shifts.

Rows keep the dashboard-facing fields in normal columns and preserve extra
form fields in JSON so the migration can proceed without losing fields that
are not yet owned by a dedicated local table.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
import bcrypt
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.environ.get("ATTENDANCE_SQLITE_PATH", os.path.join("data", "attendance.db"))
_lock = threading.RLock()
_local = threading.local()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        return conn
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS local_staff (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            branch_id TEXT,
            person_code TEXT,
            name TEXT NOT NULL,
            people_type TEXT NOT NULL DEFAULT 'staff',
            role TEXT NOT NULL DEFAULT 'staff',
            status TEXT NOT NULL DEFAULT 'active',
            is_archived INTEGER NOT NULL DEFAULT 0,
            shift_id_ref TEXT,
            department_id TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_local_staff_org ON local_staff(org_id, branch_id, is_archived);
        CREATE TABLE IF NOT EXISTS local_shifts (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            people_type TEXT NOT NULL DEFAULT 'staff',
            name TEXT NOT NULL,
            check_in_time TEXT NOT NULL,
            grace_minutes INTEGER NOT NULL DEFAULT 0,
            check_out_time TEXT,
            checkout_grace_minutes INTEGER,
            sync_delay_minutes INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_local_shifts_scope ON local_shifts(org_id, branch_id, people_type, is_active);
        """
    )
    conn.commit()
    _local.conn = conn
    return conn


def _merge(row: sqlite3.Row) -> dict[str, Any]:
    data = json.loads(row["payload_json"] or "{}")
    data.update({
        "id": row["id"], "org_id": row["org_id"], "organization_id": row["org_id"],
        "branch_id": row["branch_id"], "people_type": row["people_type"],
        "person_code": row["person_code"], "employee_id": row["person_code"],
        "name": row["name"], "full_name": row["name"], "role": row["role"],
        "status": row["status"], "is_archived": bool(row["is_archived"]),
        "shift_id_ref": row["shift_id_ref"], "department_id": row["department_id"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    })
    return data


def list_staff(org_id: str, branch_id: str | None = None, role: str | None = "staff", archived: bool = False, people_type: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM local_staff WHERE org_id = ? AND is_archived = ?"
    params: list[Any] = [str(org_id), int(archived)]
    if branch_id:
        sql += " AND branch_id = ?"; params.append(str(branch_id))
    if role and role != "all":
        sql += " AND role = ?"; params.append(str(role))
    if people_type:
        sql += " AND people_type = ?"; params.append(str(people_type).lower())
    sql += " ORDER BY name"
    return [_merge(row) for row in _connect().execute(sql, params).fetchall()]


def get_staff(staff_id: str, org_id: str | None = None) -> dict[str, Any] | None:
    sql = "SELECT * FROM local_staff WHERE id = ?"; params: list[Any] = [str(staff_id)]
    if org_id:
        sql += " AND org_id = ?"; params.append(str(org_id))
    row = _connect().execute(sql, params).fetchone()
    return _merge(row) if row else None


def create_staff(org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = _now(); staff_id = str(payload.get("id") or uuid.uuid4())
    name = str(payload.get("name") or payload.get("full_name") or "").strip()
    if not name: raise ValueError("Name is required")
    known = {"id", "org_id", "organization_id", "branch_id", "person_code", "name", "full_name", "people_type", "role", "status", "is_archived", "shift_id_ref", "department_id"}
    extras = {k: v for k, v in payload.items() if k not in known}
    if payload.get("password"):
        extras["password_hash"] = bcrypt.hashpw(
            str(payload["password"]).encode(), bcrypt.gensalt()
        ).decode()
        extras.pop("password", None)
    row = {
        "id": staff_id, "org_id": str(org_id), "branch_id": str(payload.get("branch_id")) if payload.get("branch_id") else None,
        "person_code": payload.get("person_code") or payload.get("employee_id"), "name": name,
        "people_type": str(payload.get("people_type") or "staff").lower(), "role": str(payload.get("role") or "staff"),
        "status": str(payload.get("status") or "active"), "is_archived": 0,
        "shift_id_ref": payload.get("shift_id_ref"), "department_id": payload.get("department_id"),
        "payload_json": json.dumps(extras, default=str),
        "created_at": now, "updated_at": now,
    }
    with _lock:
        conn = _connect()
        conn.execute("INSERT INTO local_staff (id, org_id, branch_id, person_code, name, people_type, role, status, is_archived, shift_id_ref, department_id, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row.values()))
        conn.commit()
    return get_staff(staff_id, org_id) or row


def update_staff(org_id: str, staff_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = get_staff(staff_id, org_id)
    if not current: raise ValueError(f"Staff member {staff_id!r} not found")
    merged = {**current, **payload}
    name = str(merged.get("name") or merged.get("full_name") or current["name"]).strip()
    known = {"id", "org_id", "organization_id", "branch_id", "person_code", "name", "full_name", "people_type", "role", "status", "is_archived", "shift_id_ref", "department_id", "created_at", "updated_at"}
    extras = {k: v for k, v in merged.items() if k not in known}
    with _lock:
        conn = _connect()
        conn.execute("UPDATE local_staff SET branch_id=?, person_code=?, name=?, people_type=?, role=?, status=?, is_archived=?, shift_id_ref=?, department_id=?, payload_json=?, updated_at=? WHERE id=? AND org_id=?", (merged.get("branch_id"), merged.get("person_code") or merged.get("employee_id"), name, str(merged.get("people_type") or "staff").lower(), str(merged.get("role") or "staff"), str(merged.get("status") or "active"), int(bool(merged.get("is_archived", False))), merged.get("shift_id_ref"), merged.get("department_id"), json.dumps(extras, default=str), _now(), str(staff_id), str(org_id)))
        conn.commit()
    return get_staff(staff_id, org_id) or current


def set_staff_archived(staff_id: str, archived: bool, reason: str | None = None) -> dict[str, Any]:
    row = get_staff(staff_id)
    if not row: raise ValueError(f"Staff member {staff_id!r} not found")
    payload = {"termination_reason": reason} if reason else {}
    updated = update_staff(row["org_id"], staff_id, {"is_archived": archived, "status": "archived" if archived else "active", **payload})
    return updated


def delete_staff(staff_id: str) -> dict[str, Any]:
    row = get_staff(staff_id)
    if not row: raise ValueError(f"Staff member {staff_id!r} not found")
    with _lock:
        conn = _connect(); conn.execute("DELETE FROM local_staff WHERE id = ?", (str(staff_id),)); conn.commit()
    return row


def authenticate_staff(email: str, password: str) -> dict[str, Any] | None:
    rows = _connect().execute(
        "SELECT * FROM local_staff WHERE lower(json_extract(payload_json, '$.email')) = lower(?) "
        "AND is_archived = 0 LIMIT 20",
        (str(email).strip(),),
    ).fetchall()
    for row in rows:
        data = _merge(row)
        password_hash = data.get("password_hash")
        if password_hash and bcrypt.checkpw(
            str(password).encode(), str(password_hash).encode()
        ):
            data["dashboard_scope"] = data.get("dashboard_scope") or "branch"
            data["organization_id"] = data["org_id"]
            data["dashboard_ready"] = bool(data.get("org_id") and data.get("branch_id"))
            return data
    return None


def list_shifts(org_id: str, branch_id: str | None = None, people_type: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM local_shifts WHERE org_id = ? AND is_active = 1"; params: list[Any] = [str(org_id)]
    if branch_id: sql += " AND branch_id = ?"; params.append(str(branch_id))
    if people_type: sql += " AND people_type = ?"; params.append(str(people_type).lower())
    sql += " ORDER BY check_in_time, name"
    rows = []
    for row in _connect().execute(sql, params).fetchall():
        data = json.loads(row["payload_json"] or "{}"); data.update(dict(row)); data["is_active"] = bool(row["is_active"]); rows.append(data)
    return rows


def get_shift(org_id: str, shift_id: str) -> dict[str, Any] | None:
    row = _connect().execute("SELECT * FROM local_shifts WHERE id = ? AND org_id = ?", (str(shift_id), str(org_id))).fetchone()
    if not row: return None
    data = json.loads(row["payload_json"] or "{}"); data.update(dict(row)); data["is_active"] = bool(row["is_active"]); return data


def create_shift(org_id: str, branch_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    now = _now(); shift_id = str(payload.get("id") or uuid.uuid4())
    row = {"id": shift_id, "org_id": str(org_id), "branch_id": str(branch_id), "people_type": str(payload.get("people_type") or "staff").lower(), "name": str(payload.get("name") or "").strip(), "check_in_time": str(payload.get("check_in_time") or "09:00"), "grace_minutes": int(payload.get("grace_minutes") or 0), "check_out_time": payload.get("check_out_time"), "checkout_grace_minutes": payload.get("checkout_grace_minutes"), "sync_delay_minutes": int(payload.get("sync_delay_minutes") or 0), "is_active": 1, "payload_json": json.dumps({}, default=str), "created_at": now, "updated_at": now}
    if not row["name"]: raise ValueError("name is required")
    with _lock:
        conn = _connect(); conn.execute("INSERT INTO local_shifts (id, org_id, branch_id, people_type, name, check_in_time, grace_minutes, check_out_time, checkout_grace_minutes, sync_delay_minutes, is_active, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row.values())); conn.commit()
    return get_shift(org_id, shift_id) or row


def update_shift(org_id: str, shift_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    current = get_shift(org_id, shift_id)
    if not current: raise ValueError(f"Shift {shift_id!r} not found")
    merged = {**current, **payload}
    fields = ["name", "check_in_time", "grace_minutes", "check_out_time", "checkout_grace_minutes", "sync_delay_minutes", "is_active"]
    values = [merged.get(field) for field in fields]
    values += [_now(), str(shift_id), str(org_id)]
    with _lock:
        conn = _connect(); conn.execute("UPDATE local_shifts SET name=?, check_in_time=?, grace_minutes=?, check_out_time=?, checkout_grace_minutes=?, sync_delay_minutes=?, is_active=?, updated_at=? WHERE id=? AND org_id=?", values); conn.commit()
    return get_shift(org_id, shift_id) or current


def delete_shift(org_id: str, shift_id: str) -> bool:
    with _lock:
        conn = _connect(); cur = conn.execute("UPDATE local_shifts SET is_active=0, updated_at=? WHERE id=? AND org_id=?", (_now(), str(shift_id), str(org_id))); conn.commit()
    if not cur.rowcount: raise ValueError(f"Shift {shift_id!r} not found")
    return True


def assign_shift(org_id: str, staff_id: str, shift_id: str | None) -> dict[str, Any]:
    if shift_id and not get_shift(org_id, shift_id): raise ValueError(f"shift_id {shift_id!r} not found")
    return update_staff(org_id, staff_id, {"shift_id_ref": shift_id})
