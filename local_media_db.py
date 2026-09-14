"""Local SQLite cameras, staff photos, and face embeddings."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

DB_PATH = os.environ.get("ATTENDANCE_SQLITE_PATH", os.path.join("data", "attendance.db"))
_lock = threading.RLock()
_local = threading.local()


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None: return conn
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS local_cameras (
            id TEXT PRIMARY KEY, org_id TEXT NOT NULL, branch_id TEXT,
            camera_name TEXT NOT NULL, camera_type TEXT DEFAULT 'nvr',
            rtsp_url TEXT, location TEXT, channel INTEGER, is_active INTEGER DEFAULT 1,
            payload_json TEXT DEFAULT '{}', updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_local_cameras_scope ON local_cameras(org_id, branch_id, is_active);
        CREATE TABLE IF NOT EXISTS local_face_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL,
            staff_id TEXT NOT NULL, embedding_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_local_embeddings_staff ON local_face_embeddings(org_id, staff_id);
    """)
    conn.commit(); _local.conn = conn; return conn


def list_cameras(org_id: str, branch_id: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM local_cameras WHERE org_id = ? AND is_active = 1"; params: list[Any] = [str(org_id)]
    if branch_id: sql += " AND branch_id = ?"; params.append(str(branch_id))
    rows = []
    for row in _connect().execute(sql, params).fetchall():
        data = json.loads(row["payload_json"] or "{}"); data.update(dict(row));
        data.update({"id": row["id"], "camera_id": row["id"], "name": row["camera_name"], "camera_name": row["camera_name"], "organization_id": row["org_id"], "branch_id": row["branch_id"], "rtsp_url": row["rtsp_url"], "rtspUrl": row["rtsp_url"]})
        rows.append(data)
    return rows


def get_camera(org_id: str, camera_id: str) -> dict[str, Any] | None:
    rows = list_cameras(org_id)
    return next((row for row in rows if str(row.get("id")) == str(camera_id)), None)


def save_camera(org_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    camera_id = str(payload.get("id") or payload.get("camera_id") or uuid.uuid4())
    row = (camera_id, str(org_id), str(payload.get("branch_id")) if payload.get("branch_id") else None, str(payload.get("camera_name") or payload.get("name") or "Camera"), str(payload.get("camera_type") or payload.get("type") or "nvr"), payload.get("rtsp_url") or payload.get("rtspUrl"), payload.get("location"), payload.get("channel"), 1, json.dumps(payload, default=str), datetime.now(timezone.utc).isoformat())
    with _lock:
        conn = _connect(); conn.execute("INSERT OR REPLACE INTO local_cameras (id, org_id, branch_id, camera_name, camera_type, rtsp_url, location, channel, is_active, payload_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row); conn.commit()
    return get_camera(org_id, camera_id) or {}


def save_embedding(org_id: str, staff_id: str, embedding: list[float]) -> None:
    with _lock:
        conn = _connect(); conn.execute("INSERT INTO local_face_embeddings (org_id, staff_id, embedding_json, created_at) VALUES (?, ?, ?, ?)", (str(org_id), str(staff_id), json.dumps(embedding), datetime.now(timezone.utc).isoformat())); conn.commit()


def replace_embeddings(org_id: str, staff_id: str, embeddings: list[list[float]]) -> int:
    with _lock:
        conn = _connect()
        conn.execute("DELETE FROM local_face_embeddings WHERE org_id = ? AND staff_id = ?", (str(org_id), str(staff_id)))
        now = datetime.now(timezone.utc).isoformat()
        conn.executemany("INSERT INTO local_face_embeddings (org_id, staff_id, embedding_json, created_at) VALUES (?, ?, ?, ?)", [(str(org_id), str(staff_id), json.dumps(vector), now) for vector in embeddings])
        conn.commit()
    return len(embeddings)


def get_staff_embeddings(org_id: str, staff_id: str) -> list[list[float]]:
    return [json.loads(row["embedding_json"]) for row in _connect().execute("SELECT embedding_json FROM local_face_embeddings WHERE org_id = ? AND staff_id = ?", (str(org_id), str(staff_id))).fetchall()]


def get_recognition_people(org_id: str, branch_id: str | None = None) -> list[dict[str, Any]]:
    import local_people_db
    staff = local_people_db.list_staff(org_id, branch_id, role="staff", archived=False)
    result = []
    for person in staff:
        vectors = get_staff_embeddings(org_id, person["id"])
        if vectors:
            result.append({"staff_id": person["id"], "name": person.get("name") or "Unknown", "people_type": person.get("people_type") or "staff", "branch_id": person.get("branch_id"), "person_code": person.get("person_code"), "embeddings": vectors, "staff": person})
    return result
