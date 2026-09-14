"""SQLite persistence for dashboard attendance settings.

This is the first settings migration slice. The public API mirrors the
settings rows currently returned by the Supabase-backed settings module, so
routes and frontend clients keep their existing contracts.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import uuid
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
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS local_departments (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            branch_id TEXT,
            name TEXT NOT NULL,
            code TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            default_shift_id TEXT,
            default_check_in_grace_override INTEGER,
            default_check_out_grace_override INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS ux_local_departments_scope
            ON local_departments(org_id, COALESCE(branch_id, ''), name);

        CREATE TABLE IF NOT EXISTS local_capture_settings (
            org_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            people_type TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'shift',
            capture_check_out INTEGER NOT NULL DEFAULT 0,
            check_in_time TEXT,
            check_in_grace_minutes INTEGER,
            check_out_time TEXT,
            check_out_grace_minutes INTEGER,
            sync_delay_minutes INTEGER NOT NULL DEFAULT 0,
            default_shift_id TEXT,
            default_check_in_grace_override INTEGER,
            default_check_out_grace_override INTEGER,
            visit_evidence_mode TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(org_id, branch_id, people_type)
        );

        CREATE TABLE IF NOT EXISTS local_manual_instructions (
            id TEXT PRIMARY KEY,
            org_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            staff_id TEXT,
            people_type TEXT NOT NULL,
            attendance_date TEXT NOT NULL,
            check_in_time TEXT,
            check_out_time TEXT,
            check_in_grace_minutes INTEGER,
            check_out_grace_minutes INTEGER,
            capture_check_out INTEGER NOT NULL DEFAULT 1,
            reason TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT,
            created_by TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_local_manual_scope
            ON local_manual_instructions(org_id, branch_id, attendance_date);
        """
    )
    conn.commit()
    _local.conn = conn
    return conn


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def list_departments(org_id: str, branch_id: str | None = None, include_inactive: bool = False) -> list[dict[str, Any]]:
    conn = _connect()
    sql = "SELECT * FROM local_departments WHERE org_id = ?"
    params: list[Any] = [str(org_id)]
    if branch_id and branch_id != "all":
        sql += " AND (branch_id = ? OR branch_id IS NULL)"
        params.append(str(branch_id))
    if not include_inactive:
        sql += " AND status = 'active'"
    sql += " ORDER BY name"
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def create_department(org_id: str, branch_id: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    code = str(payload.get("code") or "").strip() or None
    now = _now()
    department = {
        "id": str(uuid.uuid4()),
        "org_id": str(org_id),
        "branch_id": str(branch_id) if branch_id else None,
        "name": name,
        "code": code,
        "status": "active",
        "default_shift_id": None,
        "default_check_in_grace_override": None,
        "default_check_out_grace_override": None,
        "created_at": now,
        "updated_at": now,
    }
    with _lock:
        try:
            _connect().execute(
                "INSERT INTO local_departments (id, org_id, branch_id, name, code, status, "
                "default_shift_id, default_check_in_grace_override, default_check_out_grace_override, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(department.values()),
            )
            _connect().commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Department {name!r} already exists for this branch") from exc
    return department


def update_department(org_id: str, department_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name cannot be empty")
        updates["name"] = name
    if "code" in payload:
        updates["code"] = str(payload.get("code") or "").strip() or None
    if "status" in payload:
        if payload["status"] not in ("active", "inactive"):
            raise ValueError("status must be 'active' or 'inactive'")
        updates["status"] = payload["status"]
    if not updates:
        raise ValueError("No updatable fields provided")
    updates["updated_at"] = _now()
    with _lock:
        conn = _connect()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        cur = conn.execute(
            f"UPDATE local_departments SET {assignments} WHERE id = ? AND org_id = ?",
            list(updates.values()) + [str(department_id), str(org_id)],
        )
        conn.commit()
        if not cur.rowcount:
            raise ValueError(f"Department {department_id!r} not found for this org")
        return dict(conn.execute("SELECT * FROM local_departments WHERE id = ?", (str(department_id),)).fetchone())


def delete_department(org_id: str, department_id: str) -> bool:
    return bool(update_department(org_id, department_id, {"status": "inactive"}))


def set_department_default_shift(
    org_id: str,
    department_id: str,
    shift_id: str | None,
    check_in_grace_override: int | None = None,
    check_out_grace_override: int | None = None,
) -> dict[str, Any]:
    updates = {
        "default_shift_id": str(shift_id) if shift_id else None,
        "default_check_in_grace_override": check_in_grace_override if shift_id else None,
        "default_check_out_grace_override": check_out_grace_override if shift_id else None,
        "updated_at": _now(),
    }
    with _lock:
        conn = _connect()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        cur = conn.execute(
            f"UPDATE local_departments SET {assignments} WHERE id = ? AND org_id = ?",
            list(updates.values()) + [str(department_id), str(org_id)],
        )
        conn.commit()
        if not cur.rowcount:
            raise ValueError(f"department_id {department_id!r} not found for this org")
        return dict(conn.execute("SELECT * FROM local_departments WHERE id = ?", (str(department_id),)).fetchone())


def set_branch_default_shift(
    org_id: str,
    branch_id: str,
    people_type: str,
    shift_id: str | None,
    check_in_grace_override: int | None = None,
    check_out_grace_override: int | None = None,
) -> dict[str, Any]:
    existing = get_capture_settings(org_id, branch_id, people_type) or {
        "mode": "shift", "capture_check_out": False, "sync_delay_minutes": 0,
    }
    if existing.get("mode") == "simple":
        raise ValueError("This branch+people_type is in 'simple' capture mode — switch it to 'shift' mode before assigning a default shift")
    payload = {
        **existing,
        "mode": "shift",
        "default_shift_id": shift_id,
        "check_in_grace_minutes": check_in_grace_override,
        "check_out_grace_minutes": check_out_grace_override,
    }
    return upsert_capture_settings(org_id, branch_id, people_type, payload)


def get_capture_settings(org_id: str, branch_id: str, people_type: str) -> dict[str, Any] | None:
    row = _row(_connect().execute(
        "SELECT * FROM local_capture_settings WHERE org_id = ? AND branch_id = ? AND people_type = ?",
        (str(org_id), str(branch_id), str(people_type).lower()),
    ).fetchone())
    if row:
        row["capture_check_out"] = bool(row["capture_check_out"])
    return row


def list_capture_settings(org_id: str, branch_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM local_capture_settings WHERE org_id = ?"
    params: list[Any] = [str(org_id)]
    if branch_id and branch_id != "all":
        sql += " AND branch_id = ?"
        params.append(str(branch_id))
    sql += " ORDER BY people_type"
    rows = [dict(row) for row in _connect().execute(sql, params).fetchall()]
    for row in rows:
        row["capture_check_out"] = bool(row["capture_check_out"])
    return rows


def upsert_capture_settings(org_id: str, branch_id: str, people_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "shift")
    if mode not in ("shift", "simple"):
        raise ValueError("mode must be 'shift' or 'simple'")
    normalized = str(people_type).strip().lower()
    values = {
        "org_id": str(org_id), "branch_id": str(branch_id), "people_type": normalized,
        "mode": mode, "capture_check_out": int(bool(payload.get("capture_check_out", False))),
        "check_in_time": payload.get("check_in_time") if mode == "simple" else None,
        "check_in_grace_minutes": payload.get("check_in_grace_minutes") if mode == "simple" else None,
        "check_out_time": payload.get("check_out_time") if mode == "simple" else None,
        "check_out_grace_minutes": payload.get("check_out_grace_minutes") if mode == "simple" else None,
        "sync_delay_minutes": int(payload.get("sync_delay_minutes") or 0),
        "default_shift_id": payload.get("default_shift_id") if mode == "shift" else None,
        "default_check_in_grace_override": payload.get("check_in_grace_minutes") if mode == "shift" else None,
        "default_check_out_grace_override": payload.get("check_out_grace_minutes") if mode == "shift" else None,
        "visit_evidence_mode": payload.get("visit_evidence_mode"), "updated_at": _now(),
    }
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column not in {"org_id", "branch_id", "people_type"})
    with _lock:
        conn = _connect()
        conn.execute(
            f"INSERT INTO local_capture_settings ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(org_id, branch_id, people_type) DO UPDATE SET {updates}",
            [values[column] for column in columns],
        )
        conn.commit()
    return get_capture_settings(org_id, branch_id, normalized) or values


def list_manual_instructions(org_id: str, branch_id: str | None = None, people_type: str | None = None, staff_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM local_manual_instructions WHERE org_id = ?"
    params: list[Any] = [str(org_id)]
    if branch_id and branch_id != "all":
        sql += " AND branch_id = ?"
        params.append(str(branch_id))
    if people_type:
        sql += " AND people_type = ?"
        params.append(str(people_type).lower())
    if staff_id:
        sql += " AND staff_id = ?"
        params.append(str(staff_id))
    sql += " ORDER BY attendance_date DESC, created_at DESC"
    rows = [dict(row) for row in _connect().execute(sql, params).fetchall()]
    for row in rows:
        row["capture_check_out"] = bool(row["capture_check_out"])
    return rows


def create_manual_instruction(org_id: str, branch_id: str, payload: dict[str, Any], created_by: str | None = None) -> dict[str, Any]:
    now = _now()
    row = {
        "id": str(uuid.uuid4()), "org_id": str(org_id), "branch_id": str(branch_id),
        "staff_id": str(payload.get("staff_id")) if payload.get("staff_id") else None,
        "people_type": str(payload.get("people_type") or "staff").lower(),
        "attendance_date": str(payload.get("attendance_date") or payload.get("date") or "")[:10],
        "check_in_time": payload.get("check_in_time"), "check_out_time": payload.get("check_out_time"),
        "check_in_grace_minutes": payload.get("check_in_grace_minutes"),
        "check_out_grace_minutes": payload.get("check_out_grace_minutes"),
        "capture_check_out": int(bool(payload.get("capture_check_out", True))),
        "reason": payload.get("reason"), "status": "pending", "notes": payload.get("note") or payload.get("notes"),
        "created_by": str(created_by) if created_by else None, "created_at": now, "updated_at": now,
    }
    if not row["attendance_date"]:
        raise ValueError("attendance_date is required")
    columns = list(row)
    with _lock:
        conn = _connect()
        conn.execute(
            f"INSERT INTO local_manual_instructions ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [row[column] for column in columns],
        )
        conn.commit()
    return row


def delete_manual_instruction(org_id: str, instruction_id: str) -> bool:
    with _lock:
        conn = _connect()
        cur = conn.execute("DELETE FROM local_manual_instructions WHERE id = ? AND org_id = ?", (str(instruction_id), str(org_id)))
        conn.commit()
    if not cur.rowcount:
        raise ValueError(f"Manual instruction {instruction_id!r} not found for this org")
    return True


def update_manual_instruction_status(org_id: str, instruction_id: str, status: str, note: str | None = None) -> dict[str, Any]:
    updates: dict[str, Any] = {"status": str(status), "updated_at": _now()}
    if note is not None:
        updates["notes"] = str(note)
    with _lock:
        conn = _connect()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        cur = conn.execute(
            f"UPDATE local_manual_instructions SET {assignments} WHERE id = ? AND org_id = ?",
            list(updates.values()) + [str(instruction_id), str(org_id)],
        )
        conn.commit()
        if not cur.rowcount:
            raise ValueError(f"Instruction {instruction_id!r} not found for this org")
        return dict(conn.execute("SELECT * FROM local_manual_instructions WHERE id = ?", (str(instruction_id),)).fetchone())
