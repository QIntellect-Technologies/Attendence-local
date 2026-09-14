from __future__ import annotations

import json
import uuid
import re
import sqlite3
from datetime import datetime, timezone, date, timedelta
from typing import Any, Iterable
import threading

from local_node.config_store import DB_PATH as LOCAL_DB_PATH
from local_node import config_store
from local_node import shift_gate

# Process-global lock to serialize local DB write paths and avoid
# TOCTOU races between threads in this process.
_write_lock = threading.Lock()


class ShiftNameConflict(ValueError):
    """Raised by create_shift when an *active* shift already uses that name
    for the branch. Soft-deleted (is_active = 0) shifts with the same name
    are revived instead of raising this."""


def _connect() -> sqlite3.Connection:
    """Single connection factory for every call site in this module.
    WAL mode replaces the default rollback-journal's two blocking
    fsync()s per commit (write journal -> fsync -> write db -> fsync ->
    delete journal) with an append-only log — this is what turns an
    ordinary attendance write into a multi-second stall under real-time
    antivirus scanning on Windows. journal_mode=WAL persists in the db
    file itself once set, but synchronous is per-connection and must be
    re-applied every time, which is why this can't just be a one-time
    PRAGMA in init_db()."""
    conn = sqlite3.connect(LOCAL_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today(cfg: dict[str, Any] | None = None) -> str:
    """Branch-local calendar date — the same timezone the shift gate and
    the cloud's day-window bucketing use (see shift_gate._branch_zone,
    support_db_attendance_gate._get_branch_timezone), not the node
    machine's own system clock. Falls back to shift_gate's own UTC
    default if no branch timezone has synced yet (fresh activation,
    before the first /v1/node/config poll).

    Accepts an already-loaded config so callers that also need
    shift_gate lookups in the same pass (record_attendance_local) don't
    each re-read+re-parse node_config.json from disk for one detection.
    cfg=None preserves the old standalone behavior for every other
    caller (e.g. clear_today_attendance)."""
    zone = shift_gate._branch_zone(cfg or shift_gate.load_config())
    return datetime.now(zone).date().isoformat()

def _ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    """attendance_buffer may already exist on branch machines from earlier
    deployments without these columns. CREATE TABLE IF NOT EXISTS won't
    retrofit those — this PRAGMA-guarded ALTER TABLE lets every already-
    installed local node pick up new columns on next start, with zero
    manual migration step at any client site.

    check_in_confirmed / check_out_confirmed: per-leg "was the value
    currently stored in marked_at / check_out_marked_at actually captured
    inside its shift window" flags — see record_attendance_local. DEFAULT 1
    so pre-existing rows (written before this migration) are treated as
    already finalized and never silently reopened for reconfirmation;
    every new row/update this function's caller writes sets the real value
    explicitly.

    notes: operator-facing free text set by record_attendance_local for
    the "detected early, never seen again inside the shift window, only
    confirmed once it closed" case — see _format_early_before_shift_note.
    Also used for the checkout-leg early/late held-review case — see
    _format_checkout_hold_note. NULL for every other row; no default
    needed.

    check_out_hold_reason: 'early' | 'late' | NULL. Set only while a
    checkout sighting sits in held_for_review (check_out_confirmed=0,
    check_out_marked_at holding the informative-but-unconfirmed sighting
    time) — see record_attendance_local's checkout branch. Cleared back to
    NULL by whichever operator resolution action (mark_held_checkouts_late /
    mark_held_checkouts_overtime / mark_held_checkouts_half_day /
    mark_held_checkouts_short_leave) resolves the row, so its presence
    alone tells the review screen "this row still needs a checkout
    decision" without a separate flag.

    branch_id: SECURITY-RELEVANT retrofit. attendance_buffer previously had
    NO branch scoping at all — every query matched purely on
    (people_type, person_code, attendance_date), and local_event_id was
    just f"{people_type}:{person_code}:{today}". Since this SQLite file
    lives at one fixed path per machine (config_store.DB_PATH) regardless
    of which branch is currently activated, reactivating a machine for a
    DIFFERENT branch made every read/write here silently operate across
    branches — a stale row from a previous branch with the same
    people_type+person_code (very plausible with small numeric codes like
    "0001") would be read, shown in held-review, and even overwritten by
    the new branch's detections. DEFAULT '' means every row written before
    this migration is retroactively "no branch" — which never matches a
    real (non-empty Supabase UUID) branch_id in any query below, so it's
    automatically and permanently excluded from every branch-scoped query
    without a destructive DELETE. It is NOT backfilled to the current
    branch_id, since a mixed-branch machine could have rows from more than
    one branch and there's no way to tell them apart after the fact —
    quarantining beats guessing. These orphaned '' rows are otherwise
    inert and can be purged manually if disk space matters."""
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(attendance_buffer)").fetchall()}
    additions = {
        "check_out_marked_at": "TEXT",
        "check_out_confidence": "REAL",
        "check_out_camera_id": "TEXT",
        "check_out_metadata": "TEXT NOT NULL DEFAULT '{}'",
        "check_in_confirmed": "INTEGER NOT NULL DEFAULT 1",
        "check_out_confirmed": "INTEGER NOT NULL DEFAULT 1",
        "notes": "TEXT",
        "check_out_hold_reason": "TEXT",
        # 'late' | NULL. Set only while a check-in sighting sits in
        # held_for_review because it arrived after the check-in window
        # closed — mirrors check_out_hold_reason. There's only ever one
        # value ('late'): unlike checkout, an early check-in stray isn't a
        # "hold reason" needing a decision — it's just "still waiting for
        # the window", which check_in_hold_reason=NULL already represents.
        # Cleared by mark_held_check_ins_short_leave / mark_held_check_ins_half_day.
        "check_in_hold_reason": "TEXT",
        "branch_id": "TEXT NOT NULL DEFAULT ''",
        "shift_id_ref": "TEXT",
    }
    for column, ddl_type in additions.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE attendance_buffer ADD COLUMN {column} {ddl_type}")


def reset_local_data() -> None:
    """Wipe every locally-cached table (attendance_buffer, staff_embeddings,
    imported_packages) and recreate them fresh via init_db(). Called from
    activation.activate_with_token() whenever a machine activates for a
    branch different from whatever it was previously configured for (or is
    activating for the very first time) — see that function's comment for
    the exact "reset vs. preserve" decision.

    Why this exists on top of branch_id scoping (see
    _ensure_schema_migrations' branch_id docstring): since this machine's
    SQLite file lives at one fixed path regardless of which branch is
    currently activated (config_store.DB_PATH), branch_id scoping stops a
    reactivated machine's OLD data from ever being READ or WRITTEN
    across branches — but on its own it just quarantines that old data
    forever rather than removing it. For a machine being redeployed to a
    genuinely different client/branch (returned hardware, support
    reimaging a device, a client's exe reused on new premises), the
    correct behavior isn't "keep it quarantined indefinitely", it's "this
    machine has no business holding that data at all anymore" —
    activation is the one moment that's unambiguously true, so that's
    where the actual wipe belongs. Branch scoping remains as defense in
    depth for any path that reaches attendance_buffer without going
    through activation (there isn't one today, but a future refactor
    could introduce one without realizing this guarantee depended on it).

    Uses DROP + recreate (via init_db()) rather than DELETE FROM, so the
    recreated schema is always exactly current — no lingering columns from
    whatever schema-migration state the previous branch's install had
    reached. Does NOT touch config_store's own JSON config file; that's a
    separate concern already fully overwritten by save_config() in the
    same activation flow.
    """
    with _connect() as conn:
        conn.execute("DROP TABLE IF EXISTS attendance_buffer")
        conn.execute("DROP TABLE IF EXISTS staff_embeddings")
        conn.execute("DROP TABLE IF EXISTS imported_packages")
        conn.commit()
    init_db()


def init_db() -> None:
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT NOT NULL,
                people_type TEXT NOT NULL,
                person_code TEXT NOT NULL,
                full_name TEXT NOT NULL DEFAULT '',
                staff_id TEXT,
                embedding_index INTEGER NOT NULL DEFAULT 0,
                embedding TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL DEFAULT 0,
                model_version TEXT,
                source_package_id TEXT,
                imported_at TEXT NOT NULL,
                UNIQUE (branch_id, people_type, person_code, embedding_index)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_embeddings_person "
            "ON staff_embeddings(branch_id, people_type, person_code)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS imported_packages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                package_id TEXT NOT NULL,
                branch_label TEXT,
                generated_at TEXT,
                record_count INTEGER NOT NULL DEFAULT 0,
                imported_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                imported_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance_buffer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_event_id TEXT NOT NULL UNIQUE,
                branch_id TEXT NOT NULL DEFAULT '',
                people_type TEXT NOT NULL,
                person_code TEXT NOT NULL,
                staff_name TEXT NOT NULL DEFAULT '',
                attendance_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'present',
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                camera_id TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                marked_at TEXT NOT NULL,
                sync_status TEXT NOT NULL DEFAULT 'pending',
                sync_error TEXT,
                synced_at TEXT
            )
            """
        )
        _ensure_schema_migrations(conn)

        # Created after the migration above (not right after CREATE TABLE)
        # because branch_id only exists unconditionally once
        # _ensure_schema_migrations has run — on a pre-existing install,
        # CREATE INDEX referencing branch_id here would fail if it ran
        # before the ALTER TABLE that adds the column.
        #
        # MUST be UNIQUE: record_attendance_local's upserts (see the two
        # ON CONFLICT(branch_id, people_type, person_code, attendance_date)
        # sites below) require the conflict target to be backed by a UNIQUE
        # index or PRIMARY KEY — SQLite raises "ON CONFLICT clause does not
        # match any PRIMARY KEY or UNIQUE constraint" against a plain index,
        # which is exactly what every attendance mark hit while this index
        # was non-unique. CREATE UNIQUE INDEX IF NOT EXISTS alone is not
        # enough to repair an install that already has the old plain index:
        # IF NOT EXISTS sees a same-named index and silently no-ops, so the
        # crash would persist on every existing deployment while looking
        # fixed on a fresh one. Detect that case explicitly, dedupe the rows
        # the old non-unique index allowed to collide (keeping the highest
        # id — local_event_id/id is monotonically increasing on insert, so
        # MAX(id) is the newest row per tuple), drop the stale index, then
        # recreate it unique. Guarded on index_is_unique so this dedupe scan
        # runs once per install, not on every process start.
        index_is_unique = any(
            row[1] == "idx_attendance_branch_person_date" and row[2]
            for row in cur.execute("PRAGMA index_list(attendance_buffer)").fetchall()
        )
        if not index_is_unique:
            cur.execute(
                """
                DELETE FROM attendance_buffer
                WHERE id NOT IN (
                    SELECT MAX(id) FROM attendance_buffer
                    GROUP BY branch_id, people_type, person_code, attendance_date
                )
                """
            )
            cur.execute("DROP INDEX IF EXISTS idx_attendance_branch_person_date")
            cur.execute(
                "CREATE UNIQUE INDEX idx_attendance_branch_person_date "
                "ON attendance_buffer(branch_id, people_type, person_code, attendance_date)"
            )

        # ── People Management (Staff Management page) ───────────────────────
        # Does NOT duplicate embedding data — "face trained?" is a computed
        # join onto staff_embeddings (see _row_to_staff_dict). branch_id is
        # kept for the same reactivation-safety reason as every other table
        # here (see _ensure_schema_migrations' branch_id docstring).
        #
        # shift_check_in_time / shift_check_out_time / *_grace_minutes are
        # this person's tier-2 personal shift override (shift_gate.py's
        # existing precedence order). NULL means "no personal override —
        # use this branch's default shift_windows[people_type]", which is
        # authored via PUT /api/settings/shifts straight into node_config.json
        # (see dashboard_routes.py) rather than a second local table, since
        # node_config.json is already exactly what shift_gate.py reads.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS staff (
                id TEXT PRIMARY KEY,
                branch_id TEXT NOT NULL,
                people_type TEXT NOT NULL DEFAULT 'staff',
                person_code TEXT NOT NULL,
                full_name TEXT NOT NULL,
                cnic TEXT,
                email TEXT,
                phone TEXT,
                salary REAL NOT NULL DEFAULT 0.0,
                department TEXT,
                designation TEXT,
                role TEXT NOT NULL DEFAULT 'staff',
                status TEXT NOT NULL DEFAULT 'active',
                capacity_flag TEXT,
                shift_check_in_time TEXT,
                shift_check_in_grace_minutes INTEGER NOT NULL DEFAULT 15,
                shift_check_out_time TEXT,
                shift_check_out_grace_minutes INTEGER NOT NULL DEFAULT 15,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                archived_at TEXT,
                UNIQUE (branch_id, person_code)
            )
            """
        )
        staff_columns = {
            row[1] for row in cur.execute("PRAGMA table_info(staff)").fetchall()
        }
        if "shift_id_ref" not in staff_columns:
            cur.execute("ALTER TABLE staff ADD COLUMN shift_id_ref TEXT")
        if "profile_image_data" not in staff_columns:
            cur.execute("ALTER TABLE staff ADD COLUMN profile_image_data BLOB")
        if "profile_image_mime" not in staff_columns:
            cur.execute("ALTER TABLE staff ADD COLUMN profile_image_mime TEXT")
        if "cnic" not in staff_columns:
            cur.execute("ALTER TABLE staff ADD COLUMN cnic TEXT")
        if "salary" not in staff_columns:
            cur.execute("ALTER TABLE staff ADD COLUMN salary REAL NOT NULL DEFAULT 0.0")
        if "purge_after" not in staff_columns:
            # Stamped by archive_staff() = archived_at + the branch's
            # archived_staff_retention_days (see get_archived_staff_retention_days).
            # retention_worker.py permanently deletes the record once this
            # passes — see purge_expired_archived_staff.
            cur.execute("ALTER TABLE staff ADD COLUMN purge_after TEXT")
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_branch_status "
            "ON staff(branch_id, status, archived_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_person_code "
            "ON staff(branch_id, person_code)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_config (
                branch_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shifts (
                id TEXT PRIMARY KEY,
                branch_id TEXT NOT NULL,
                name TEXT NOT NULL,
                people_type TEXT NOT NULL DEFAULT 'staff',
                check_in_time TEXT NOT NULL,
                grace_minutes INTEGER NOT NULL DEFAULT 15,
                check_out_time TEXT,
                checkout_grace_minutes INTEGER,
                sync_delay_minutes INTEGER NOT NULL DEFAULT 0,
                shift_type TEXT NOT NULL DEFAULT 'main',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # One-time rebuild for DB files created before this fix: the original
        # schema had a table-level UNIQUE(branch_id, name), which blocks
        # re-creating a shift with the same name as a soft-deleted one
        # (delete_shift only ever sets is_active = 0, it never removes the
        # row). CREATE TABLE IF NOT EXISTS above is a no-op on those files,
        # so the stale constraint has to be dropped by rebuilding the table
        # — SQLite has no ALTER TABLE ... DROP CONSTRAINT.
        shifts_ddl_row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'shifts'"
        ).fetchone()
        if shifts_ddl_row and re.search(
            r"UNIQUE\s*\(\s*branch_id\s*,\s*name\s*\)", shifts_ddl_row[0], re.IGNORECASE
        ):
            cur.execute("ALTER TABLE shifts RENAME TO shifts_pre_unique_fix")
            cur.execute(
                """
                CREATE TABLE shifts (
                    id TEXT PRIMARY KEY,
                    branch_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    people_type TEXT NOT NULL DEFAULT 'staff',
                    check_in_time TEXT NOT NULL,
                    grace_minutes INTEGER NOT NULL DEFAULT 15,
                    check_out_time TEXT,
                    checkout_grace_minutes INTEGER,
                    sync_delay_minutes INTEGER NOT NULL DEFAULT 0,
                    shift_type TEXT NOT NULL DEFAULT 'main',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                INSERT INTO shifts (
                    id, branch_id, name, people_type, check_in_time, grace_minutes,
                    check_out_time, checkout_grace_minutes, sync_delay_minutes,
                    shift_type, is_active, created_at, updated_at
                )
                SELECT
                    id, branch_id, name, people_type, check_in_time, grace_minutes,
                    check_out_time, checkout_grace_minutes, sync_delay_minutes,
                    shift_type, is_active, created_at, updated_at
                FROM shifts_pre_unique_fix
                """
            )
            cur.execute("DROP TABLE shifts_pre_unique_fix")
        shift_columns = {
            row[1] for row in cur.execute("PRAGMA table_info(shifts)").fetchall()
        }
        if "shift_type" not in shift_columns:
            cur.execute(
                "ALTER TABLE shifts ADD COLUMN shift_type TEXT NOT NULL DEFAULT 'main'"
            )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_shifts_branch ON shifts(branch_id, is_active)"
        )
        # Replaces the old table-level UNIQUE(branch_id, name): scoping the
        # uniqueness to active rows lets a shift name be reused once the
        # prior shift with that name has been (soft-)deleted.
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_shifts_branch_name_active "
            "ON shifts(branch_id, name) WHERE is_active = 1"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_shifts_branch_type "
            "ON shifts(branch_id, shift_type, is_active)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS staff_shift_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT NOT NULL,
                staff_id TEXT NOT NULL,
                shift_id TEXT NOT NULL,
                assigned_at TEXT NOT NULL,
                UNIQUE(branch_id, staff_id, shift_id),
                FOREIGN KEY(staff_id) REFERENCES staff(id),
                FOREIGN KEY(shift_id) REFERENCES shifts(id)
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_staff_shift_assignments_staff "
            "ON staff_shift_assignments(branch_id, staff_id)"
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS break_attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT NOT NULL,
                staff_id TEXT NOT NULL,
                people_type TEXT NOT NULL,
                person_code TEXT NOT NULL,
                staff_name TEXT NOT NULL DEFAULT '',
                shift_id TEXT NOT NULL,
                shift_name TEXT NOT NULL DEFAULT '',
                attendance_date TEXT NOT NULL,
                period_start_at TEXT NOT NULL,
                period_end_at TEXT NOT NULL,
                outside_at TEXT NOT NULL,
                returned_at TEXT,
                duration_seconds INTEGER,
                duration_minutes INTEGER,
                status TEXT NOT NULL DEFAULT 'outside',
                outside_confidence REAL,
                return_confidence REAL,
                outside_camera_id TEXT,
                return_camera_id TEXT,
                outside_metadata TEXT NOT NULL DEFAULT '{}',
                return_metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(branch_id, staff_id, shift_id, attendance_date)
            )
            """
        )
        break_columns = {
            row[1] for row in cur.execute("PRAGMA table_info(break_attendance)").fetchall()
        }
        if "duration_minutes" not in break_columns:
            cur.execute(
                "ALTER TABLE break_attendance ADD COLUMN duration_minutes INTEGER"
            )
        for column, definition in (
            ("correction_source", "TEXT NOT NULL DEFAULT 'automatic'"),
            ("corrected_by", "TEXT"),
            ("corrected_at", "TEXT"),
            ("correction_reason", "TEXT"),
        ):
            if column not in break_columns:
                cur.execute(
                    f"ALTER TABLE break_attendance ADD COLUMN {column} {definition}"
                )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_break_attendance_day "
            "ON break_attendance(branch_id, attendance_date, status)"
        )

        # ── Local dashboard login ────────────────────────────────────────────
        # One admin account per branch today (single-tenant, single-user
        # machine) — modeled as branch-scoped + UNIQUE(branch_id, email)
        # rather than a hardcoded singleton row so a future multi-account
        # requirement (e.g. a second manager login) is a UI change, not a
        # schema migration.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_auth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                branch_id TEXT NOT NULL,
                email TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                full_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (branch_id, email)
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS license_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                token TEXT,
                cached_at TEXT
            )
            """
        )

        # ── Weekly consented backup ───────────────────────────────────────────
        # cursors is a JSON object of {table_name: last_backed_up_timestamp},
        # one entry per table this worker backs up — see backup_worker.py.
        # last_prompted_at drives the weekly popup cadence; last_backup_at is
        # only set on an actual successful push (allow=True).
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS backup_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_backup_at TEXT,
                last_prompted_at TEXT,
                last_consent TEXT,
                cursors TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        conn.commit()


def get_dashboard_config(branch_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM dashboard_config WHERE branch_id = ?",
            (branch_id,),
        ).fetchone()
    if not row:
        return {}
    try:
        value = json.loads(row[0] or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _persist_dashboard_config(branch_id: str, merged: dict[str, Any]) -> None:
    """Write-only. Caller must already hold _write_lock."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO dashboard_config(branch_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(branch_id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (branch_id, json.dumps(merged, separators=(",", ":")), utc_now()),
        )
        conn.commit()


def save_dashboard_config(branch_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with _write_lock:
        merged = {**get_dashboard_config(branch_id), **(payload or {})}
        _persist_dashboard_config(branch_id, merged)
    return merged


def add_department(branch_id: str, name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Append one workforce department to this branch's dashboard config."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Department name is required")
    with _write_lock:
        config = get_dashboard_config(branch_id)
        departments = config.setdefault("departments", {})
        branch_departments = departments.setdefault(branch_id, [])
        if any(
            (item.get("name") or "").strip().lower() == clean_name.lower()
            for item in branch_departments
        ):
            raise ValueError(f'"{clean_name}" is already configured for this branch.')
        new_department = {
            "id": f"department_{uuid.uuid4().hex[:12]}",
            "name": clean_name,
            "itemKind": "group",
            "personFamily": "workforce",
        }
        branch_departments.append(new_department)
        _persist_dashboard_config(branch_id, config)
    return new_department, branch_departments


def add_designation(
    branch_id: str, department_id: str, name: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Append one workforce designation under an existing department."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("Designation name is required")
    if not department_id:
        raise ValueError("department_id is required")
    with _write_lock:
        config = get_dashboard_config(branch_id)
        branch_departments = (config.get("departments") or {}).get(branch_id, [])
        if not any(item.get("id") == department_id for item in branch_departments):
            raise ValueError("Department not found for this branch.")
        roles = config.setdefault("roles", {})
        branch_roles = roles.setdefault(branch_id, [])
        new_designation = {
            "id": f"designation_{uuid.uuid4().hex[:12]}",
            "name": clean_name,
            "departmentId": department_id,
            "level": "custom",
            "personFamily": "workforce",
        }
        branch_roles.append(new_designation)
        _persist_dashboard_config(branch_id, config)
    return new_designation, [
        item for item in branch_roles if item.get("departmentId") == department_id
    ]

# ── Embedding import (per-person upsert) ────────────────────────────────────

def upsert_person_embeddings(
    branch_id: str,
    people_type: str,
    person_code: str,
    full_name: str,
    embeddings: list[list[float]],
    model_version: str,
    source_package_id: str,
) -> int:
    """Replace embeddings for exactly one person, leaving every other person
    in this branch untouched. This is the core primitive for incremental zip
    imports — never delete-then-insert at the branch level for this flow."""
    now = utc_now()
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM staff_embeddings WHERE branch_id = ? AND people_type = ? AND person_code = ?",
            (branch_id, people_type, person_code),
        )
        for index, embedding in enumerate(embeddings):
            cur.execute(
                """
                INSERT INTO staff_embeddings (
                    branch_id, people_type, person_code, full_name, embedding_index,
                    embedding, embedding_dim, model_version, source_package_id, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    branch_id, people_type, person_code, full_name, index,
                    json.dumps(embedding, separators=(",", ":")), len(embedding),
                    model_version, source_package_id, now,
                ),
            )
        conn.commit()
    return len(embeddings)


def import_embedding_package(
    branch_id: str,
    package_id: str,
    branch_label: str,
    generated_at: str,
    records: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Apply every person record from a parsed zip package. Idempotent —
    re-importing the same package produces the same end state."""
    imported, skipped, errors = 0, 0, []
    for record in records:
        try:
            count = upsert_person_embeddings(
                branch_id=branch_id,
                people_type=str(record["people_type"]),
                person_code=str(record["person_code"]),
                full_name=str(record.get("full_name") or ""),
                embeddings=record["embeddings"],
                model_version=str(record.get("model_version") or ""),
                source_package_id=package_id,
            )
            if count > 0:
                imported += 1
            else:
                skipped += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"{record.get('people_type')}:{record.get('person_code')}: {exc}")

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO imported_packages
                (package_id, branch_label, generated_at, record_count, imported_count, skipped_count, imported_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (package_id, branch_label, generated_at, imported + skipped, imported, skipped, utc_now()),
        )
        conn.commit()

    return {"imported": imported, "skipped": skipped, "errors": errors}


def get_all_embeddings(branch_id: str) -> list[dict[str, Any]]:
    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            # Reduce contention and allow concurrent readers; wait briefly
            # if the DB is busy rather than erroring immediately.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            cur = conn.cursor()
        cur.execute(
            "SELECT * FROM staff_embeddings WHERE branch_id = ? ORDER BY people_type, person_code, embedding_index",
            (branch_id,),
        )
        return [dict(row) | {"embedding": json.loads(row["embedding"])} for row in cur.fetchall()]


def import_history(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM imported_packages ORDER BY id DESC LIMIT ?", (int(limit or 20),))
        return [dict(row) for row in cur.fetchall()]


# ── Attendance ───────────────────────────────────────────────────────────────

def get_attendance_row(branch_id: str, people_type: str, person_code: str, attendance_date: str) -> dict[str, Any] | None:
    """Read-only peek at one person's attendance_buffer row for a given
    date, without mutating anything. Used by manual_instructions_worker to
    check whether a real camera sighting already landed for this person+date
    before deciding whether to honor its timestamp (within the instruction's
    grace window) or fall back to the admin-typed instructed time.

    branch_id-scoped — without it, this could return a different branch's
    stale row for the same people_type+person_code (e.g. after a machine is
    reactivated for a new branch), causing a manual instruction to honor a
    stranger's sighting time instead of correctly falling back to the
    instructed time. See _ensure_schema_migrations' branch_id docstring."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM attendance_buffer WHERE branch_id = ? AND people_type = ? AND person_code = ? AND attendance_date = ?",
            (branch_id, people_type, person_code, attendance_date),
        )
        row = cur.fetchone()
        if not row:
            return None
        return dict(row) | {"metadata": json.loads(row["metadata"] or "{}")}

def _local_time_str(iso_dt: str | None, cfg: dict[str, Any]) -> str | None:
    """Shared HH:MM formatter for both note helpers below, rendered in the
    branch's own local time (same conversion shift_gate uses for every
    window comparison) rather than UTC or the node machine's clock. Returns
    None on anything unparsable so callers can bail out uniformly instead
    of each re-implementing the same try/except."""
    if not iso_dt:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_dt).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(shift_gate._branch_zone(cfg)).strftime("%H:%M")


def _format_late_check_in_note(people_type: str, person_code: str, marked_at: str | None) -> str | None:
    """Informational note for a check-in that auto-confirmed after the
    check-in window's grace period had already passed. Purely descriptive
    now — there is no operator decision attached to a late check-in, it
    simply is one. Returns None if there's no resolved shift window to
    quote a check-in time from."""
    cfg = shift_gate.load_config()
    window_info = shift_gate.resolve_window_for_debug(people_type, person_code, cfg)
    shift_check_in = (window_info.get("effective_window") or {}).get("check_in_time")
    local_time = _local_time_str(marked_at, cfg)
    if not shift_check_in or not local_time:
        return None
    return f"Checked in late at {local_time} — after the {shift_check_in} shift start."


def _format_checkout_info_note(
    people_type: str, person_code: str, sighted_at: str | None, timing: str,
) -> str | None:
    """Informational note for a checkout-leg sighting that fell outside its
    checkout window — early (left before the shift ended) or late (seen
    after the grace period, so never promoted to a confirmed checkout).
    Purely descriptive; unlike the old held-review flow, nothing here waits
    on an operator action. Returns None for anything other than
    'early'/'late', or when there's no shift window to quote a time from."""
    if timing not in ("early", "late"):
        return None
    cfg = shift_gate.load_config()
    window_info = shift_gate.resolve_window_for_debug(people_type, person_code, cfg)
    shift_check_out = (window_info.get("effective_window") or {}).get("check_out_time")
    local_time = _local_time_str(sighted_at, cfg)
    if not local_time:
        return None
    if timing == "early":
        where = f"before the {shift_check_out} shift end" if shift_check_out else "before the checkout window"
        return f"Seen at {local_time}, left early — {where}. Checkout not confirmed."
    where = f"after the {shift_check_out} shift end" if shift_check_out else "after the checkout window"
    return f"Seen at {local_time}, {where}. Checkout not confirmed."


# The notes column holds one string but a single day can carry a note from
# EACH leg independently (a late check-in note from the morning, an
# out-of-window checkout note in the evening for that same shift). Each
# leg's note is tagged with its own prefix and kept on its own line, so
# writing/refreshing one leg's note can never blow away the other leg's.
_NOTE_PREFIXES = {
    "check_in": "Check-in: ",
    "check_out": "Check-out: ",
}


def _merge_note(existing_notes: str | None, category: str, new_note: str | None) -> str | None:
    """Return the notes column value with `category`'s line replaced by
    `new_note` (its own leg re-firing updates its own line in place, same
    as before) while leaving any OTHER category's line untouched.

    - new_note=None: leave existing notes exactly as they were — nothing
      new to record for this leg on this call, so this must never disturb
      whatever the other leg already wrote.
    - new_note="" (empty string): explicitly CLEAR this leg's line (e.g. a
      held checkout note that's no longer accurate once a later in-window
      sighting resolves the checkout normally) while still leaving the
      other leg's line untouched.
    - new_note=<text>: set/replace this leg's line with that text.
    """
    if new_note is None:
        return existing_notes

    prefix = _NOTE_PREFIXES[category]
    kept_lines = [
        line for line in (existing_notes or "").split("\n")
        if line.strip() and not line.startswith(prefix)
    ]
    if new_note:
        kept_lines.append(f"{prefix}{new_note}")
    if not kept_lines:
        return None
    # Stable, predictable order regardless of which leg fired most recently
    # — check-in note (if any) always reads before the check-out note.
    ordered = sorted(kept_lines, key=lambda line: 0 if line.startswith(_NOTE_PREFIXES["check_in"]) else 1)
    return "\n".join(ordered)


def record_attendance_local(
    branch_id: str,
    people_type: str,
    person_code: str,
    staff_name: str,
    confidence: float,
    source: str = "camera",
    camera_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    event_dt_utc: datetime | None = None,
) -> dict[str, Any]:
    """Capture a raw presence detection for today, deciding INTERNALLY
    whether it's a check-in or check-out attempt and whether it lands
    inside that leg's shift window — see local_node.shift_gate.

    Simplified, payroll-free state machine (no held-for-review workflow —
    this client only ever needs a check-in time and a checkout time):

    · No confirmed check-in yet for today -> this is a check-in ATTEMPT.
        - Before the check-in window opens -> IGNORED entirely. Nothing is
          written: an early stray (loitering, walking past camera hours
          before a shift) carries no information worth keeping once we no
          longer need it to justify a payroll decision, and letting it sit
          in the DB unconfirmed only reintroduces the held-review state we
          removed. The person's real, in-window (or late) detection later
          in the day is what actually claims the slot.
        - Inside the check-in window -> CONFIRMS immediately. This is "the
          first detection in the shift window": check_in_confirmed=1,
          check_in_hold_reason=None, sync_status=pending.
        - After the window's grace period has passed (late) -> ALSO
          confirms immediately, just flagged: check_in_hold_reason='late'.
          Never held — a person arriving late still gets a real check-in,
          otherwise they could never transition to checkout tracking at
          all for the rest of the day.
      Whichever of these three fires is final for the day: once a check-in
      row exists it was, by construction, either in-window or late — there
      is no unconfirmed check-in state left to revisit.

    · Check-in already confirmed -> this is a check-out ATTEMPT.
        - Inside the checkout window -> CONFIRMS, overwriting any previous
          value. Every in-window sighting keeps overwriting it, so the
          LAST detection inside the checkout window is what ends up
          stored, per spec — this branch fires even if a checkout was
          already confirmed by an earlier in-window sighting.
        - Outside the window, and a real checkout is ALREADY confirmed ->
          the row is left completely untouched (event_type=stray_ignored).
          Without this, a person re-appearing on camera long after a valid
          checkout (hallway walk-through, camera glitch) would silently
          overwrite their real checkout with a bogus one.
        - Outside the window, no confirmed checkout yet (early OR after
          the grace period closes) -> stored INFORMATIONALLY only:
          check_out_marked_at/hold_reason track the most recent such
          sighting for display, but check_out_confirmed stays 0 forever —
          there is no operator action left to promote it. Per spec, once
          the checkout grace period has passed with no in-window sighting,
          no real attendance checkout is ever recorded for that day.

    A manual_override row is always authoritative and short-circuits all
    of the above, unchanged from before.
    """
    cfg = shift_gate.load_config()
    event_dt = event_dt_utc or datetime.now(timezone.utc)
    now = event_dt.isoformat()
    # Shift-aware bucket date, NOT the naive "today" _today() returns — see
    # shift_gate.resolve_attendance_bucket_date's own docstring for why an
    # overnight shift's checkout leg must file against the check-in's date.
    today = shift_gate.resolve_attendance_bucket_date(people_type, person_code, event_dt, config=cfg)

    def _metadata_json(ready_at: str | None) -> str:
        return json.dumps({**(metadata or {}), "ready_at": ready_at}, separators=(",", ":"))

    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM attendance_buffer WHERE branch_id = ? AND people_type = ? AND person_code = ? AND attendance_date = ?",
                (branch_id, people_type, person_code, today),
            )
            existing = cur.fetchone()
            existing_dict = dict(existing) if existing else None

            # Older local-node builds represented a manual absence as a row.
            # Absence is now represented by no attendance row at all; remove
            # any legacy row before processing a real camera sighting so it
            # can create a fresh check-in normally.
            if existing_dict is not None and str(existing_dict.get("status") or "").lower() == "absent":
                conn.execute("DELETE FROM attendance_buffer WHERE id = ?", (existing_dict["id"],))
                existing_dict = None

            if existing_dict is not None and existing_dict.get("source") == "manual_override":
                return {**existing_dict, "already_marked": True, "event_type": "locked_by_manual_override"}

            # A row only ever exists here once its check-in has already been
            # confirmed (in-window or late) — early strays are never written
            # (see the check-in branch below) — so existing_dict's mere
            # presence is itself the "check-in already confirmed" signal.
            if existing_dict is None:
                within = shift_gate.is_event_within_shift(
                    people_type, event_dt, is_check_out=False, person_code=person_code, config=cfg,
                )
                window_closed = (
                    not within
                    and shift_gate.is_check_in_window_closed(
                        people_type, event_dt, person_code=person_code, config=cfg,
                    )
                )

                if not within and not window_closed:
                    # Before the check-in window has even opened: ignore
                    # entirely, no row written — a later in-window (or late)
                    # sighting is what actually claims today's check-in.
                    return {
                        "branch_id": branch_id, "people_type": people_type, "person_code": person_code,
                        "staff_name": staff_name, "already_marked": False,
                        "event_type": "check_in_pre_shift_ignored",
                    }

                check_in_hold_reason = "late" if window_closed else None
                event_type = "check_in" if within else "check_in_late"
                note = _format_late_check_in_note(people_type, person_code, now) if window_closed else None
                notes = _merge_note(None, "check_in", note)
                check_in_ready_at = shift_gate.resolve_leg_ready_at_utc(
                    people_type, person_code, event_dt, is_check_out=False, config=cfg,
                )
                local_event_id = f"{branch_id}:{people_type}:{person_code}:{today}"
                cur.execute(
                    """
                    INSERT INTO attendance_buffer (
                        local_event_id, branch_id, people_type, person_code, staff_name, attendance_date,
                        status, confidence, source, camera_id, metadata, marked_at,
                        check_in_confirmed, check_out_confirmed, sync_status, check_in_hold_reason, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, 'present', ?, ?, ?, ?, ?, 1, 0, 'pending', ?, ?)
                    ON CONFLICT(branch_id, people_type, person_code, attendance_date) DO UPDATE SET
                        staff_name=excluded.staff_name, status='present', confidence=excluded.confidence,
                        source=excluded.source, camera_id=excluded.camera_id, metadata=excluded.metadata,
                        marked_at=excluded.marked_at, check_in_confirmed=1, sync_status=excluded.sync_status,
                        check_in_hold_reason=excluded.check_in_hold_reason, notes=excluded.notes
                    """,
                    (
                        local_event_id, branch_id, people_type, person_code, staff_name, today,
                        float(confidence), source, camera_id, _metadata_json(check_in_ready_at), now,
                        check_in_hold_reason, notes,
                    ),
                )
                conn.commit()
                return {
                    "local_event_id": local_event_id, "branch_id": branch_id,
                    "people_type": people_type, "person_code": person_code,
                    "staff_name": staff_name, "confidence": float(confidence), "camera_id": camera_id,
                    "marked_at": now, "check_out_marked_at": None, "sync_status": "pending",
                    "check_in_confirmed": 1, "check_out_confirmed": 0, "already_marked": False,
                    "event_type": event_type, "check_in_hold_reason": check_in_hold_reason, "notes": notes,
                }

            # Check-in already confirmed -> this detection is a checkout attempt.
            row = existing_dict
            within_co = shift_gate.is_event_within_shift(
                people_type, event_dt, is_check_out=True, person_code=person_code, config=cfg,
            )
            check_out_ready_at = shift_gate.resolve_leg_ready_at_utc(
                people_type, person_code, event_dt, is_check_out=True, config=cfg,
            )
            check_out_metadata_json = _metadata_json(check_out_ready_at)

            if within_co:
                # Inside the checkout window: always (re)confirm on THIS
                # sighting — the last in-window detection wins, even if an
                # earlier in-window sighting already confirmed a checkout.
                notes = _merge_note(row.get("notes"), "check_out", "")
                cur.execute(
                    """
                    UPDATE attendance_buffer
                    SET check_out_marked_at = ?, check_out_confidence = ?, check_out_camera_id = ?,
                        check_out_metadata = ?, check_out_confirmed = 1, check_out_hold_reason = NULL,
                        sync_status = 'pending', sync_error = NULL, notes = ?
                    WHERE id = ?
                    """,
                    (now, float(confidence), camera_id, check_out_metadata_json, notes, row["id"]),
                )
                conn.commit()
                return {
                    **row, "check_out_marked_at": now, "check_out_confidence": float(confidence),
                    "check_out_camera_id": camera_id, "check_out_confirmed": 1, "check_out_hold_reason": None,
                    "sync_status": "pending", "already_marked": False, "event_type": "check_out", "notes": notes,
                }

            if bool(row.get("check_out_confirmed")):
                # A valid checkout is already locked in. Later sightings are
                # unrelated camera noise and must not alter the row or notes.
                return {**row, "already_marked": True, "event_type": "stray_ignored"}

            # Outside the checkout window, record only the note. The checkout
            # timestamp fields are reserved for confirmed in-window sightings.
            timing = shift_gate.classify_check_out_timing(
                people_type, event_dt, person_code=person_code, config=cfg,
            )
            note = _format_checkout_info_note(people_type, person_code, now, timing)
            notes = _merge_note(row.get("notes"), "check_out", note)
            hold_reason = timing if timing in ("early", "late") else None
            cur.execute(
                """
                UPDATE attendance_buffer
                SET check_out_marked_at = ?, check_out_confidence = ?,
                    check_out_camera_id = ?, check_out_metadata = ?,
                    check_out_confirmed = 0, check_out_hold_reason = ?,
                    sync_status = 'pending', sync_error = NULL, notes = ?
                WHERE id = ?
                """,
                (now, float(confidence), camera_id, check_out_metadata_json, hold_reason, notes, row["id"]),
            )
            conn.commit()
            return {
                **row, "check_out_marked_at": now, "check_out_confidence": float(confidence),
                "check_out_camera_id": camera_id, "check_out_metadata": check_out_metadata_json,
                "check_out_confirmed": 0, "check_out_hold_reason": hold_reason,
                "already_marked": False, "event_type": "check_out_unconfirmed", "notes": notes,
            }


def record_attendance_manual(
    branch_id: str,
    people_type: str,
    person_code: str,
    staff_name: str,
    confidence: float,
    attendance_date: str,
    check_in_marked_at: str | None = None,
    check_out_marked_at: str | None = None,
    source: str = "manual_override",
    camera_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert or FULLY REPLACE an attendance row with explicit instructed
    timestamps. A manual instruction is authoritative for this person+date —
    by design it replaces whatever a camera detection (or an earlier
    override) put there, rather than merging into it. That's why, unlike
    record_attendance_local, every field below is written unconditionally
    on an existing row: check-in, check-out, source, and metadata all move
    to the instruction's values, and sync_status is re-armed to 'pending'
    even if the prior row had already synced, so the corrected version
    reaches the backend.

    Idempotent per instruction_id (carried in metadata by
    manual_instructions_worker): re-polling the same still-pending
    instruction before it's been acked is a no-op rather than re-writing
    identical data and re-publishing a duplicate live event.
    """
    today = str(attendance_date)
    meta = dict(metadata or {})
    instruction_id = meta.get("instruction_id")
    metadata_json = json.dumps(meta, separators=(",", ":"))
    check_in_final = check_in_marked_at or check_out_marked_at or utc_now()

    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            cur = conn.cursor()
        cur.execute(
            "SELECT * FROM attendance_buffer WHERE branch_id = ? AND people_type = ? AND person_code = ? AND attendance_date = ?",
            (branch_id, people_type, person_code, today),
        )
        existing = cur.fetchone()
        existing_dict = dict(existing) if existing else None

        if existing_dict is not None and instruction_id is not None:
            already_applied = (
                existing_dict.get("source") == "manual_override"
                and json.loads(existing_dict.get("metadata") or "{}").get("instruction_id") == instruction_id
            )
            if already_applied:
                return {**existing_dict, "already_marked": True, "event_type": "already_applied"}

        if existing_dict is not None:
            cur.execute(
                """
                UPDATE attendance_buffer
                SET staff_name = ?, status = 'present', confidence = ?, source = ?, camera_id = ?,
                    metadata = ?, marked_at = ?,
                    check_out_marked_at = ?, check_out_confidence = ?, check_out_camera_id = ?,
                    check_out_metadata = ?, sync_status = 'pending', sync_error = NULL, notes = NULL
                WHERE id = ?
                """,
                (
                    staff_name, float(confidence), source, camera_id,
                    metadata_json, check_in_final,
                    check_out_marked_at,
                    float(confidence) if check_out_marked_at else None,
                    camera_id if check_out_marked_at else None,
                    metadata_json if check_out_marked_at else "{}",
                    existing_dict["id"],
                ),
            )
            conn.commit()
            return {
                "local_event_id": existing_dict["local_event_id"],
                "people_type": people_type,
                "person_code": person_code,
                "staff_name": staff_name,
                "confidence": float(confidence),
                "camera_id": camera_id,
                "marked_at": check_in_final,
                "check_out_marked_at": check_out_marked_at,
                "sync_status": "pending",
                "already_marked": False,
                "event_type": "overridden",
            }

        # No existing row for this person+date — plain insert. branch_id
        # embedded in local_event_id itself for the same global-uniqueness
        # reason as record_attendance_local — see that function's comment.
        local_event_id = f"{branch_id}:{people_type}:{person_code}:{today}"
        # Insert the manual override; if a camera or another manual write
        # raced to create the row, merge by replacing fields so the
        # manual instruction remains authoritative.
        cur.execute(
            """
            INSERT INTO attendance_buffer (
                local_event_id, branch_id, people_type, person_code, staff_name, attendance_date,
                status, confidence, source, camera_id, metadata, marked_at,
                check_out_marked_at, check_out_confidence, check_out_camera_id, check_out_metadata, sync_status
            ) VALUES (?, ?, ?, ?, ?, ?, 'present', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT(branch_id, people_type, person_code, attendance_date) DO UPDATE SET
                local_event_id=excluded.local_event_id,
                staff_name=excluded.staff_name,
                status='present',
                confidence=excluded.confidence,
                source=excluded.source,
                camera_id=excluded.camera_id,
                metadata=excluded.metadata,
                marked_at=excluded.marked_at,
                check_out_marked_at=excluded.check_out_marked_at,
                check_out_confidence=excluded.check_out_confidence,
                check_out_camera_id=excluded.check_out_camera_id,
                check_out_metadata=excluded.check_out_metadata,
                sync_status=excluded.sync_status
            """,
            (
                local_event_id, branch_id, people_type, person_code, staff_name, today,
                float(confidence), source, camera_id, metadata_json, check_in_final,
                check_out_marked_at,
                float(confidence) if check_out_marked_at else None,
                camera_id if check_out_marked_at else None,
                metadata_json if check_out_marked_at else "{}",
            ),
        )
        conn.commit()
        return {
            "local_event_id": local_event_id,
            "branch_id": branch_id,
            "people_type": people_type,
            "person_code": person_code,
            "staff_name": staff_name,
            "confidence": float(confidence),
            "camera_id": camera_id,
            "marked_at": check_in_final,
            "check_out_marked_at": check_out_marked_at,
            "sync_status": "pending",
            "already_marked": False,
            "event_type": "check_in_and_out" if check_out_marked_at else "check_in",
        }


def update_attendance_record(
    branch_id: str,
    record_id: str,
    *,
    check_in: str | None = None,
    check_out: str | None = None,
    arrival_status: str | None = None,
    notes: str | None = None,
    check_in_provided: bool = False,
    check_out_provided: bool = False,
    notes_provided: bool = False,
) -> dict[str, Any] | None:
    """Update one locally stored attendance row without changing its owner."""
    updates: dict[str, Any] = {}
    if check_in_provided and check_in:
        updates["marked_at"] = check_in
    if check_out_provided:
        updates["check_out_marked_at"] = check_out or None
        updates["check_out_confirmed"] = 1 if check_out else 0
        updates["check_out_hold_reason"] = None
    if arrival_status:
        updates["check_in_hold_reason"] = "late" if arrival_status == "late" else None
    if notes_provided:
        updates["notes"] = notes or None
    if not updates:
        return None

    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM attendance_buffer WHERE branch_id = ? AND (id = ? OR local_event_id = ?)",
                (branch_id, record_id, record_id),
            ).fetchone()
            if row is None:
                return None

            assignments = ", ".join(f"{key} = ?" for key in updates)
            values = list(updates.values())
            values.extend([row["id"]])
            conn.execute(
                f"UPDATE attendance_buffer SET {assignments}, sync_status = 'pending', sync_error = NULL WHERE id = ?",
                values,
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM attendance_buffer WHERE id = ?",
                (row["id"],),
            ).fetchone()
            return dict(updated) if updated else None

def _person_code_variants(person_code: str | None) -> set[str]:
    """Return the legacy/current code forms that still point to the same
    person so we can delete stale attendance rows consistently.

    Local data historically mixed zero-padded numeric ids ("0001") and the
    current branch-local code format ("STF-0001"); a single staff member could
    therefore leave two rows for the same date, and the summary page would count
    one as present and one as absent even though the person was fully absent.
    """
    variants: set[str] = set()
    raw = str(person_code or "").strip()
    if not raw:
        return variants
    variants.add(raw)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits:
        variants.add(digits)
        if raw.startswith("STF-") or raw.startswith("staff-") or raw.startswith("staff:"):
            variants.add(digits.zfill(4))
            variants.add(f"STF-{digits.zfill(4)}")
            variants.add(f"staff:{digits.zfill(4)}")
    if raw.isdigit():
        variants.add(f"STF-{raw.zfill(4)}")
    return {v for v in variants if v}


def delete_attendance_for_staff(branch_id: str, person_code: str | None, attendance_date: str | None = None, *, people_type: str | None = None, staff_name: str | None = None) -> int:
    """Delete all attendance_buffer rows for one person on one date.

    This prevents stale rows created under alternate person-code formats from
    leaving a person counted as present even after the mark-absent action.
    """
    if not person_code:
        return 0
    variants = _person_code_variants(person_code)
    if not variants:
        return 0

    date_filter = attendance_date if attendance_date else _today()
    conditions: list[str] = ["branch_id = ?", "attendance_date = ?"]
    values: list[Any] = [branch_id, date_filter]

    if people_type:
        conditions.append("people_type = ?")
        values.append(people_type)
    if staff_name:
        conditions.append("staff_name = ?")
        values.append(staff_name)

    conditions.append(f"person_code IN ({', '.join('?' for _ in sorted(variants))})")
    values.extend(sorted(variants))

    query = f"DELETE FROM attendance_buffer WHERE {' AND '.join(conditions)}"
    with _write_lock:
        with _connect() as conn:
            cur = conn.execute(query, values)
            conn.commit()
            return cur.rowcount


def clear_today_attendance(branch_id: str) -> int:
    """Delete every attendance_buffer row for today's date AND this branch
    (local machine date, same as _today()/record_attendance_local). Used by
    the "Clear today's attendance" maintenance action — a full reset for
    testing/troubleshooting, not a normal operator action, since it also
    discards already-synced rows for today, not just pending/held ones.
    Does not touch the backend's copy of anything already synced before
    this is called. branch_id-scoped so this can never delete a different
    branch's rows sharing this machine's SQLite file. Returns the number of
    rows deleted."""
    today = _today()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM attendance_buffer WHERE branch_id = ? AND attendance_date = ?",
            (branch_id, today),
        )
        conn.commit()
        return cur.rowcount


def recent_attendance(
    branch_id: str, limit: int = 50, include_held: bool = False,
    current_shift_only: bool = False,
) -> list[dict[str, Any]]:
    """Feeds /api/live-events' and /api/live-detections' "attendance" array.
    Every row here is meaningful to show: with the held-review workflow
    removed, there's no "invisible until reviewed" state left to filter
    out — a row is either a confirmed (on-time or late) check-in, or that
    plus a confirmed or purely-informational checkout.

    branch_id-scoped so this can never return a different branch's rows
    sharing this machine's SQLite file — see _ensure_schema_migrations'
    branch_id docstring.

    When current_shift_only is true, rows are restricted to the attendance
    bucket currently owned by each person's shift. This makes an unfinished
    row from yesterday disappear from the live board when today's shift
    starts, while still keeping the previous date visible during an overnight
    shift's early-morning checkout window.
    """
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM attendance_buffer WHERE branch_id = ? ORDER BY id DESC" +
            ("" if current_shift_only else " LIMIT ?"),
            (branch_id,) if current_shift_only else (branch_id, int(limit or 50)),
        )
        rows = [dict(row) | {"metadata": json.loads(row["metadata"] or "{}")} for row in cur.fetchall()]

    if current_shift_only:
        now = datetime.now(timezone.utc)
        rows = [
            row for row in rows
            if row.get("attendance_date") == shift_gate.resolve_attendance_bucket_date(
                str(row.get("people_type") or "staff"),
                str(row.get("person_code") or ""),
                now,
            )
        ]
        return rows[: int(limit or 50)]

    return rows
    

def get_embeddings_grouped_by_person(branch_id: str) -> list[dict[str, Any]]:
    """Reshape staff_embeddings rows (one row per vector) into one record
    per person, ready for api_client.push_embeddings()."""
    rows = get_all_embeddings(branch_id)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["people_type"], row["person_code"])
        record = grouped.setdefault(key, {
            "people_type": row["people_type"],
            "person_code": row["person_code"],
            "full_name": row["full_name"],
            "model_version": row["model_version"],
            "embeddings": [],
        })
        record["embeddings"].append(row["embedding"])
    return list(grouped.values())


def find_person_code_for_staff(branch_id: str, staff_id: str, people_type: str | None = None) -> str | None:
    """Attempt to find a local `person_code` for a given `staff_id` by
    scanning the staff_embeddings table. If `people_type` is provided,
    restrict the search to that people_type for better accuracy.

    Returns the person_code (string) if found, otherwise None.
    """
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        if people_type:
            cur.execute(
                "SELECT person_code FROM staff_embeddings WHERE branch_id = ? AND staff_id = ? AND people_type = ? LIMIT 1",
                (branch_id, staff_id, people_type),
            )
        else:
            cur.execute(
                "SELECT person_code FROM staff_embeddings WHERE branch_id = ? AND staff_id = ? LIMIT 1",
                (branch_id, staff_id),
            )
        row = cur.fetchone()
        return str(row[0]) if row else None
    

def resolve_local_person_code(branch_id: str, people_type: str, backend_person_code: str) -> str:
    """A manual instruction's person_code comes from client_staff.person_code
    (dashboard's zero-padded Staff ID convention, e.g. "0003"). This node's
    own attendance_buffer/staff_embeddings key people by whatever person_code
    the trainer-enrolled embedding package used instead (e.g. "3") — same
    identity, two representations, exactly like shift_gate._lookup_personal_window
    and support_db.push_node_attendance already normalize on their own paths.
    Without this, record_attendance_manual silently writes to a brand-new,
    disconnected attendance_buffer row instead of the person's real one —
    the override "applies" and acks, but the person's actual attendance
    never changes.
    """
    if not backend_person_code:
        return backend_person_code
    with _connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT person_code FROM staff_embeddings WHERE branch_id = ? AND people_type = ?",
            (branch_id, people_type),
        )
        local_codes = [row[0] for row in cur.fetchall()]

    if backend_person_code in local_codes:
        return backend_person_code
    if backend_person_code.isdigit():
        target = int(backend_person_code)
        for candidate in local_codes:
            if candidate.isdigit() and int(candidate) == target:
                return candidate
    return backend_person_code


# ── Staff (People Management) ────────────────────────────────────────────────

def _next_person_code(conn: sqlite3.Connection, branch_id: str) -> str:
    """Local person codes only need to be unique within this one branch's
    local DB — there is no cloud allocator to coordinate with offline.
    STF-0001, STF-0002, ... mirrors the cloud's own person_code format
    closely enough that support staff reading logs don't need a second
    convention."""
    count = conn.execute(
        "SELECT COUNT(*) FROM staff WHERE branch_id = ?", (branch_id,)
    ).fetchone()[0]
    return f"STF-{count + 1:04d}"


def _row_to_staff_dict(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    """Joins in embedding status so the frontend's 'face trained?' badge
    (StaffRow.tsx) works without a second round trip. Doesn't return the
    embeddings themselves — those stay internal to the recognition engine."""
    data = dict(row)
    has_profile_image = bool(data.get("profile_image_data"))
    # Profile image bytes stay in SQLite and are served/uploaded through the
    # media endpoint; never put the BLOB into JSON staff responses.
    data.pop("profile_image_data", None)
    if has_profile_image and data.get("id"):
        data["profile_image_url"] = f"/api/staff/{data['id']}/photo"
    data["metadata"] = json.loads(data.get("metadata") or "{}")
    precomputed_embedding_count = data.pop("_embedding_count", None)
    if precomputed_embedding_count is None:
        precomputed_embedding_count = conn.execute(
            "SELECT COUNT(*) FROM staff_embeddings "
            "WHERE branch_id = ? AND people_type = ? AND person_code = ?",
            (data["branch_id"], data["people_type"], data["person_code"]),
        ).fetchone()[0]
    data["face_trained"] = int(precomputed_embedding_count) > 0
    data["embedding_count"] = int(precomputed_embedding_count)
    shift = None
    if data.get("_shift_label") is not None:
        shift = (
            data.pop("_shift_label"),
            data.pop("_shift_duty_start"),
            data.pop("_shift_duty_end"),
        )
    elif data.get("shift_id_ref"):
        shift = conn.execute(
            "SELECT name, check_in_time, check_out_time FROM shifts WHERE id = ?",
            (data["shift_id_ref"],),
        ).fetchone()
    data["shift_id_ref"] = data.get("shift_id_ref")
    data["shift_label"] = shift[0] if shift else None
    data["duty_start"] = shift[1] if shift else data.get("shift_check_in_time")
    data["duty_end"] = shift[2] if shift else data.get("shift_check_out_time")
    return data


def list_staff(
    branch_id: str,
    *,
    include_archived: bool = False,
    department: str | None = None,
) -> list[dict[str, Any]]:
    query = """
        SELECT staff.*,
                             COALESCE(embeddings.embedding_count, 0) AS _embedding_count,
               shifts.name AS _shift_label,
               shifts.check_in_time AS _shift_duty_start,
               shifts.check_out_time AS _shift_duty_end
        FROM staff
                LEFT JOIN (
                        SELECT branch_id, people_type, person_code, COUNT(*) AS embedding_count
                        FROM staff_embeddings
                        GROUP BY branch_id, people_type, person_code
                ) AS embeddings
                    ON embeddings.branch_id = staff.branch_id
                 AND embeddings.people_type = staff.people_type
                 AND embeddings.person_code = staff.person_code
        LEFT JOIN shifts
          ON shifts.id = staff.shift_id_ref
         AND shifts.branch_id = staff.branch_id
        WHERE staff.branch_id = ?
    """
    params: list[Any] = [branch_id]
    if not include_archived:
        query += " AND staff.archived_at IS NULL"
    if department:
        query += " AND staff.department = ?"
        params.append(department)
    query += " ORDER BY staff.full_name COLLATE NOCASE"

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        return [_row_to_staff_dict(conn, r) for r in rows]


def get_staff(branch_id: str, staff_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM staff WHERE branch_id = ? AND id = ?",
            (branch_id, staff_id),
        ).fetchone()
        if row is None:
            # Fall back to person_code (e.g. "STF-0002"). Every live
            # detection card / attendance row on the wire identifies a
            # person by person_code, not this table's internal `id` — see
            # camera_stream_manager's live_event["staff_id"] = row["person_code"]
            # and dashboard_routes._shape_attendance_row's personCode. A
            # caller that only ever saw person_code (the dashboard's photo
            # lookup for a live detection, in particular) must still
            # resolve here, or every such lookup 404s even when the staff
            # row and its uploaded photo both exist.
            row = conn.execute(
                "SELECT * FROM staff WHERE branch_id = ? AND person_code = ?",
                (branch_id, staff_id),
            ).fetchone()
        return _row_to_staff_dict(conn, row) if row else None


def resolve_staff_pk(branch_id: str, identifier: str) -> str | None:
    """Resolve either the staff table's own `id` or a person_code
    (e.g. "STF-0002") to that row's canonical `id`, or None if neither
    matches. Shared by the staff-photo routes so a live detection card
    (which only ever knows person_code) and the Staff Management page
    (which knows the real id) both resolve to the same row.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM staff WHERE branch_id = ? AND id = ?",
            (branch_id, identifier),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM staff WHERE branch_id = ? AND person_code = ?",
                (branch_id, identifier),
            ).fetchone()
        return row[0] if row else None


def _row_to_shift_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["is_active"] = bool(data.get("is_active"))
    return data


def list_shifts(branch_id: str, people_type: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM shifts WHERE branch_id = ? AND is_active = 1"
    params: list[Any] = [branch_id]
    if people_type:
        query += " AND people_type = ?"
        params.append(people_type)
    query += " ORDER BY name COLLATE NOCASE"
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [_row_to_shift_dict(row) for row in conn.execute(query, params).fetchall()]


def list_break_shifts(branch_id: str, people_type: str | None = None) -> list[dict[str, Any]]:
    query = (
        "SELECT * FROM shifts WHERE branch_id = ? AND shift_type = 'break' "
        "AND is_active = 1"
    )
    params: list[Any] = [branch_id]
    if people_type:
        query += " AND people_type = ?"
        params.append(people_type)
    query += " ORDER BY check_in_time, name COLLATE NOCASE"
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [_row_to_shift_dict(row) for row in conn.execute(query, params).fetchall()]


def create_shift(branch_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    import uuid
    now = utc_now()
    name = str(payload.get("name") or "Shift")
    people_type = str(payload.get("people_type") or "staff")
    check_in_time = str(payload.get("check_in_time") or "09:00")
    grace_minutes = int(payload.get("grace_minutes") or 15)
    check_out_time = payload.get("check_out_time")
    checkout_grace_minutes = payload.get("checkout_grace_minutes")
    sync_delay_minutes = int(payload.get("sync_delay_minutes") or 0)
    shift_type = "break" if str(payload.get("shift_type") or "main").lower() == "break" else "main"

    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            # branch_id + name is only unique among *active* shifts (see
            # idx_shifts_branch_name_active in init_db). A soft-deleted shift
            # with the same name may still be sitting in the table, so check
            # for it explicitly instead of racing the INSERT against it.
            existing = conn.execute(
                "SELECT id, is_active FROM shifts WHERE branch_id = ? AND name = ? "
                "COLLATE NOCASE",
                (branch_id, name),
            ).fetchone()
            if existing is not None and existing["is_active"]:
                raise ShiftNameConflict(
                    f"An active shift named {name!r} already exists for this branch."
                )
            if existing is not None:
                # Revive the soft-deleted shift under the same id rather than
                # leaving it orphaned and inserting a second row.
                shift_id = existing["id"]
                conn.execute(
                    """UPDATE shifts SET
                        people_type = ?, check_in_time = ?, grace_minutes = ?,
                        check_out_time = ?, checkout_grace_minutes = ?,
                        sync_delay_minutes = ?, shift_type = ?, is_active = 1,
                        updated_at = ?
                    WHERE id = ?""",
                    (people_type, check_in_time, grace_minutes, check_out_time,
                     checkout_grace_minutes, sync_delay_minutes, shift_type,
                     now, shift_id),
                )
            else:
                shift_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO shifts
                    (id, branch_id, name, people_type, check_in_time, grace_minutes,
                     check_out_time, checkout_grace_minutes, sync_delay_minutes,
                     shift_type, is_active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (shift_id, branch_id, name, people_type, check_in_time,
                     grace_minutes, check_out_time, checkout_grace_minutes,
                     sync_delay_minutes, shift_type, now, now),
                )
            conn.commit()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return _row_to_shift_dict(
            conn.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        )


def assign_staff_shift(branch_id: str, staff_id: str, shift_id: str | None) -> dict[str, Any] | None:
    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            shift = None
            if shift_id:
                shift = conn.execute(
                    "SELECT * FROM shifts WHERE branch_id = ? AND id = ? "
                    "AND is_active = 1 AND shift_type = 'main'",
                    (branch_id, shift_id),
                ).fetchone()
                if shift is None:
                    return None
            conn.execute(
                "UPDATE staff SET shift_id_ref = ?, shift_check_in_time = ?, "
                "shift_check_out_time = ?, shift_check_in_grace_minutes = ?, "
                "shift_check_out_grace_minutes = ?, updated_at = ? "
                "WHERE branch_id = ? AND id = ?",
                (shift_id, shift["check_in_time"] if shift else None,
                 shift["check_out_time"] if shift else None,
                 shift["grace_minutes"] if shift else 15,
                 (shift["checkout_grace_minutes"] or 15) if shift else 15,
                 utc_now(), branch_id, staff_id),
            )
            conn.commit()
    _rebuild_staff_shift_windows(branch_id)
    return get_staff(branch_id, staff_id)


def list_staff_break_shifts(branch_id: str, staff_id: str) -> list[dict[str, Any]]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        staff = conn.execute(
            "SELECT shift_id_ref FROM staff WHERE branch_id = ? AND id = ?",
            (branch_id, staff_id),
        ).fetchone()
        if staff is None or not staff["shift_id_ref"]:
            return []
        main = conn.execute(
            "SELECT check_in_time, check_out_time FROM shifts "
            "WHERE branch_id = ? AND id = ? AND shift_type = 'main' AND is_active = 1",
            (branch_id, staff["shift_id_ref"]),
        ).fetchone()
        if main is None:
            return []
        rows = conn.execute(
            """
            SELECT shifts.*
            FROM staff_shift_assignments
            JOIN shifts ON shifts.id = staff_shift_assignments.shift_id
                       AND shifts.branch_id = staff_shift_assignments.branch_id
            WHERE staff_shift_assignments.branch_id = ?
              AND staff_shift_assignments.staff_id = ?
              AND shifts.shift_type = 'break'
              AND shifts.is_active = 1
            ORDER BY shifts.check_in_time, shifts.name COLLATE NOCASE
            """,
            (branch_id, staff_id),
        ).fetchall()
        return [
            _row_to_shift_dict(row)
            for row in rows
            if _break_fits_main_shift(
                main["check_in_time"], main["check_out_time"],
                row["check_in_time"], row["check_out_time"],
            )
        ]


def _clock_minutes(value: str | None) -> int | None:
    try:
        parts = str(value or "").split(":")
        hours, minutes = int(parts[0]), int(parts[1])
        if not (0 <= hours <= 23 and 0 <= minutes <= 59):
            return None
        return hours * 60 + minutes
    except (TypeError, ValueError, IndexError):
        return None


def _shift_duration_minutes(start: str | None, end: str | None) -> int | None:
    start_minutes = _clock_minutes(start)
    end_minutes = _clock_minutes(end)
    if start_minutes is None or end_minutes is None or start_minutes == end_minutes:
        return None
    return (end_minutes - start_minutes) % 1440 or 1440


def _break_fits_main_shift(
    main_start: str | None, main_end: str | None,
    break_start: str | None, break_end: str | None,
) -> bool:
    main_start_minutes = _clock_minutes(main_start)
    break_start_minutes = _clock_minutes(break_start)
    main_duration = _shift_duration_minutes(main_start, main_end)
    break_duration = _shift_duration_minutes(break_start, break_end)
    if (
        main_start_minutes is None or break_start_minutes is None
        or main_duration is None or break_duration is None
        or break_duration > main_duration
    ):
        return False
    break_offset = (break_start_minutes - main_start_minutes) % 1440
    return break_offset + break_duration <= main_duration


def assign_staff_break_shifts(
    branch_id: str, staff_id: str, shift_ids: list[str]
) -> dict[str, Any] | None:
    requested_ids = list(dict.fromkeys(str(value) for value in shift_ids if value))
    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            staff = conn.execute(
                "SELECT id, shift_id_ref FROM staff WHERE branch_id = ? AND id = ?",
                (branch_id, staff_id),
            ).fetchone()
            if staff is None:
                return None
            if not staff["shift_id_ref"]:
                raise ValueError("Assign a main attendance shift before assigning break shifts.")
            main = conn.execute(
                "SELECT check_in_time, check_out_time FROM shifts "
                "WHERE branch_id = ? AND id = ? AND shift_type = 'main' AND is_active = 1",
                (branch_id, staff["shift_id_ref"]),
            ).fetchone()
            if main is None:
                raise ValueError("The employee's main attendance shift is missing or inactive.")
            valid_ids = {
                row["id"]
                for row in conn.execute(
                    "SELECT id FROM shifts WHERE branch_id = ? AND shift_type = 'break' "
                    "AND is_active = 1 AND id IN ({})".format(",".join("?" for _ in requested_ids) or "NULL"),
                    [branch_id, *requested_ids],
                ).fetchall()
            }
            if valid_ids != set(requested_ids):
                raise ValueError("One or more selected break shifts are invalid or inactive.")
            selected_breaks = conn.execute(
                "SELECT check_in_time, check_out_time FROM shifts "
                "WHERE branch_id = ? AND shift_type = 'break' AND is_active = 1 "
                "AND id IN ({})".format(",".join("?" for _ in requested_ids) or "NULL"),
                [branch_id, *requested_ids],
            ).fetchall()
            if any(
                not _break_fits_main_shift(
                    main["check_in_time"], main["check_out_time"],
                    row["check_in_time"], row["check_out_time"],
                )
                for row in selected_breaks
            ):
                raise ValueError("Every break shift must fall completely inside the employee's main shift.")
            conn.execute(
                "DELETE FROM staff_shift_assignments WHERE branch_id = ? AND staff_id = ?",
                (branch_id, staff_id),
            )
            conn.executemany(
                "INSERT INTO staff_shift_assignments "
                "(branch_id, staff_id, shift_id, assigned_at) VALUES (?, ?, ?, ?)",
                [(branch_id, staff_id, shift_id, utc_now()) for shift_id in requested_ids],
            )
            conn.commit()
    return get_staff(branch_id, staff_id)


def _break_window_for_local_time(
    local_dt: datetime,
    check_in_time: str,
    check_out_time: str | None,
    grace_minutes: int = 0,
) -> tuple[datetime, datetime] | None:
    """Return the local-time occurrence containing local_dt.

    Grace allows an early detection, while the scheduled end remains fixed.
    """
    try:
        start_parts = [int(part) for part in str(check_in_time).split(":")[:2]]
        start_time = datetime.min.time().replace(hour=start_parts[0], minute=start_parts[1])
        if not check_out_time:
            return None
        end_parts = [int(part) for part in str(check_out_time).split(":")[:2]]
        end_time = datetime.min.time().replace(hour=end_parts[0], minute=end_parts[1])
    except (TypeError, ValueError, IndexError):
        return None

    start_date = local_dt.date()
    if end_time <= start_time and local_dt.time() < end_time:
        start_date -= timedelta(days=1)

    start = datetime.combine(start_date, start_time, tzinfo=local_dt.tzinfo)
    end_date = start_date + timedelta(days=1) if end_time <= start_time else start_date
    end = datetime.combine(end_date, end_time, tzinfo=local_dt.tzinfo)
    start -= timedelta(minutes=max(0, int(grace_minutes or 0)))
    return (start, end) if start <= local_dt < end else None


def _format_break_duration(duration_seconds: int | None) -> str | None:
    if duration_seconds is None:
        return None
    total_minutes = max(0, int(duration_seconds)) // 60
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def record_break_detection(
    branch_id: str,
    people_type: str,
    person_code: str,
    staff_name: str,
    confidence: float,
    camera_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    event_dt_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """Record a recognized face inside every assigned active break window.

    The first detection in a break window is the employee's outside time.
    Every later detection in that same window replaces the return time, so the
    final sighting before the window closes is the effective return time.
    Calls outside the window never modify an existing row.
    """
    event_dt = event_dt_utc or datetime.now(timezone.utc)
    if event_dt.tzinfo is None:
        event_dt = event_dt.replace(tzinfo=timezone.utc)
    cfg = shift_gate.load_config()
    local_dt = event_dt.astimezone(shift_gate._branch_zone(cfg))
    now = event_dt.isoformat()
    local_date = local_dt.date().isoformat()
    metadata_json = json.dumps(metadata or {}, separators=(",", ":"))

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        assigned = conn.execute(
            """
                 SELECT staff.id AS staff_id, shifts.id AS shift_id, shifts.name,
                     shifts.check_in_time, shifts.grace_minutes, shifts.check_out_time,
                                     main_shift.check_in_time AS main_check_in_time,
                                     main_shift.check_out_time AS main_check_out_time
            FROM staff
            JOIN staff_shift_assignments assigned
              ON assigned.branch_id = staff.branch_id AND assigned.staff_id = staff.id
            JOIN shifts ON shifts.branch_id = assigned.branch_id AND shifts.id = assigned.shift_id
                        JOIN shifts main_shift
                            ON main_shift.branch_id = staff.branch_id
                         AND main_shift.id = staff.shift_id_ref
                         AND main_shift.shift_type = 'main'
                         AND main_shift.is_active = 1
            WHERE staff.branch_id = ? AND staff.people_type = ? AND staff.person_code = ?
              AND staff.archived_at IS NULL AND shifts.shift_type = 'break'
              AND shifts.is_active = 1
            """,
            (branch_id, people_type, person_code),
        ).fetchall()

    updates: list[dict[str, Any]] = []
    for assignment in assigned:
        window = _break_window_for_local_time(
            local_dt,
            assignment["check_in_time"],
            assignment["check_out_time"],
            assignment["grace_minutes"],
        )
        if not _break_fits_main_shift(
            assignment["main_check_in_time"], assignment["main_check_out_time"],
            assignment["check_in_time"], assignment["check_out_time"],
        ):
            continue
        if window is None:
            continue
        period_start, period_end = window
        attendance_date = period_start.date().isoformat()
        period_start_at = period_start.astimezone(timezone.utc).isoformat()
        period_end_at = period_end.astimezone(timezone.utc).isoformat()
        with _write_lock:
            with _connect() as conn:
                conn.row_factory = sqlite3.Row
                existing = conn.execute(
                    "SELECT * FROM break_attendance WHERE branch_id = ? AND staff_id = ? "
                    "AND shift_id = ? AND attendance_date = ?",
                    (branch_id, assignment["staff_id"], assignment["shift_id"], attendance_date),
                ).fetchone()
                if existing is not None and existing["correction_source"] == "manual":
                    continue
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO break_attendance (
                            branch_id, staff_id, people_type, person_code, staff_name,
                            shift_id, shift_name, attendance_date, period_start_at,
                            period_end_at, outside_at, status, outside_confidence,
                            outside_camera_id, outside_metadata, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outside', ?, ?, ?, ?, ?)
                        """,
                        (
                            branch_id, assignment["staff_id"], people_type, person_code,
                            staff_name, assignment["shift_id"], assignment["name"],
                            attendance_date, period_start_at, period_end_at, now,
                            float(confidence), camera_id, metadata_json, now, now,
                        ),
                    )
                    result = {
                        "event_type": "break_out",
                        "status": "outside",
                        "outside_at": now,
                        "returned_at": None,
                        "duration_seconds": None,
                        "duration_minutes": None,
                        "duration_label": None,
                    }
                else:
                    duration = max(
                        0,
                        int((event_dt - datetime.fromisoformat(existing["outside_at"])).total_seconds()),
                    )
                    conn.execute(
                        """
                        UPDATE break_attendance
                        SET returned_at = ?, duration_seconds = ?, duration_minutes = ?, status = 'returned',
                            return_confidence = ?, return_camera_id = ?,
                            return_metadata = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, duration, duration // 60, float(confidence), camera_id, metadata_json, now, existing["id"]),
                    )
                    result = {
                        "event_type": "break_return",
                        "status": "returned",
                        "outside_at": existing["outside_at"],
                        "returned_at": now,
                        "duration_seconds": duration,
                        "duration_minutes": duration // 60,
                        "duration_label": _format_break_duration(duration),
                    }
                conn.commit()
        updates.append({
            **result,
            "branch_id": branch_id,
            "staff_id": assignment["staff_id"],
            "people_type": people_type,
            "person_code": person_code,
            "staff_name": staff_name,
            "shift_id": assignment["shift_id"],
            "shift_name": assignment["name"],
            "attendance_date": attendance_date,
            "period_start_at": period_start_at,
            "period_end_at": period_end_at,
        })
    return updates


def _break_attendance_row(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["duration_label"] = _format_break_duration(data.get("duration_seconds"))
    for key in ("outside_metadata", "return_metadata"):
        try:
            data[key] = json.loads(data.get(key) or "{}")
        except (TypeError, json.JSONDecodeError):
            data[key] = {}
    return data


def update_break_attendance(
    branch_id: str,
    record_id: str,
    *,
    outside_at: str | None,
    returned_at: str | None,
    correction_reason: str | None = None,
    corrected_by: str = "local_admin",
) -> dict[str, Any] | None:
    """Apply a supervisor correction and permanently protect it from camera updates."""
    if not outside_at:
        raise ValueError("outside_at is required.")

    def parse_timestamp(value: str, field: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ValueError(f"{field} must be a valid ISO timestamp.") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    outside_dt = parse_timestamp(str(outside_at), "outside_at")
    returned_dt = parse_timestamp(str(returned_at), "returned_at") if returned_at else None
    if returned_dt is not None and returned_dt < outside_dt:
        raise ValueError("returned_at cannot be earlier than outside_at.")

    duration_seconds = (
        int((returned_dt - outside_dt).total_seconds()) if returned_dt else None
    )
    now = utc_now()
    status = "returned" if returned_dt else "missing_return"
    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM break_attendance WHERE branch_id = ? AND id = ?",
                (branch_id, record_id),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """UPDATE break_attendance
                   SET outside_at = ?, returned_at = ?, duration_seconds = ?,
                       duration_minutes = ?, status = ?, correction_source = 'manual',
                       corrected_by = ?, corrected_at = ?, correction_reason = ?,
                       updated_at = ?
                 WHERE branch_id = ? AND id = ?""",
                (
                    outside_dt.isoformat(),
                    returned_dt.isoformat() if returned_dt else None,
                    duration_seconds,
                    duration_seconds // 60 if duration_seconds is not None else None,
                    status,
                    corrected_by or "local_admin",
                    now,
                    (correction_reason or "").strip() or None,
                    now,
                    branch_id,
                    record_id,
                ),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM break_attendance WHERE branch_id = ? AND id = ?",
                (branch_id, record_id),
            ).fetchone()
            return _break_attendance_row(updated) if updated else None


def finalize_expired_break_attendance(
    branch_id: str,
    attendance_date: str | None = None,
    now_utc: datetime | None = None,
) -> int:
    """Close ended break periods with no return detection for review."""
    now = now_utc or datetime.now(timezone.utc)
    now_text = now.isoformat()
    clauses = ["branch_id = ?", "status = 'outside'", "returned_at IS NULL", "period_end_at <= ?"]
    params: list[Any] = [branch_id, now_text]
    if attendance_date:
        clauses.insert(1, "attendance_date = ?")
        params.insert(1, attendance_date)
    with _write_lock:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE break_attendance SET status = 'missing_return', updated_at = ? "
                "WHERE " + " AND ".join(clauses),
                [now_text, *params],
            )
            conn.commit()
            return cur.rowcount


def list_break_attendance(
    branch_id: str,
    attendance_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return break attendance rows for review, newest period first."""
    clauses = ["branch_id = ?"]
    params: list[Any] = [branch_id]
    if attendance_date:
        clauses.append("attendance_date = ?")
        params.append(attendance_date)
    elif start_date or end_date:
        if start_date:
            clauses.append("attendance_date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("attendance_date <= ?")
            params.append(end_date)
    if status:
        clauses.append("status = ?")
        params.append(status)
    params.append(int(limit or 500))
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM break_attendance WHERE "
            + " AND ".join(clauses)
            + " ORDER BY attendance_date DESC, period_start_at DESC, staff_name COLLATE NOCASE LIMIT ?",
            params,
        ).fetchall()
        return [_break_attendance_row(row) for row in rows]


def update_shift(branch_id: str, shift_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    editable = ("name", "check_in_time", "grace_minutes", "check_out_time",
                "checkout_grace_minutes", "sync_delay_minutes", "shift_type", "is_active")
    fields = {key: payload[key] for key in editable if key in payload}
    if not fields:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM shifts WHERE branch_id = ? AND id = ?", (branch_id, shift_id)).fetchone()
            return _row_to_shift_dict(row) if row else None
    fields["updated_at"] = utc_now()
    values = list(fields.values()) + [branch_id, shift_id]
    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                f"UPDATE shifts SET {', '.join(f'{key} = ?' for key in fields)} "
                "WHERE branch_id = ? AND id = ?",
                values,
            )
            updated_shift = conn.execute(
                "SELECT check_in_time, grace_minutes, check_out_time, "
                "checkout_grace_minutes, shift_type FROM shifts "
                "WHERE branch_id = ? AND id = ?",
                (branch_id, shift_id),
            ).fetchone()
            if updated_shift is not None and updated_shift["shift_type"] == "main":
                conn.execute(
                    """
                    UPDATE staff
                    SET shift_check_in_time = ?,
                        shift_check_in_grace_minutes = ?,
                        shift_check_out_time = ?,
                        shift_check_out_grace_minutes = ?,
                        updated_at = ?
                    WHERE branch_id = ? AND shift_id_ref = ?
                    """,
                    (
                        updated_shift["check_in_time"],
                        updated_shift["grace_minutes"],
                        updated_shift["check_out_time"],
                        updated_shift["checkout_grace_minutes"] or 15,
                        utc_now(),
                        branch_id,
                        shift_id,
                    ),
                )
            conn.commit()
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM shifts WHERE branch_id = ? AND id = ?", (branch_id, shift_id)).fetchone()
        result = _row_to_shift_dict(row) if row else None
    if result is not None:
        _rebuild_staff_shift_windows(branch_id)
    return result


def _rebuild_staff_shift_windows(branch_id: str) -> None:
    """Materializes every active staff member's personal shift override
    into node_config.json's staff_shift_windows — the exact structure
    shift_gate._lookup_personal_window already reads (keyed
    "people_type:person_code"). This is the ONLY writer of that config
    key in local/offline mode: cloud-synced nodes get it from
    /v1/node/config instead, but for this client the Staff Management
    page IS the config author, so shift_gate.py itself needs zero
    changes. Rebuilt wholesale (not patched key-by-key) so an archived
    or edited staff member's stale override can never linger — cheap
    at this table's scale (per-branch staff counts, not per-org)."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT people_type, person_code, shift_check_in_time,
                   shift_check_in_grace_minutes, shift_check_out_time,
                   shift_check_out_grace_minutes
            FROM staff
            WHERE branch_id = ? AND archived_at IS NULL
              AND (shift_check_in_time IS NOT NULL OR shift_check_out_time IS NOT NULL)
            """,
            (branch_id,),
        ).fetchall()

    staff_shift_windows = {
        f"{row['people_type']}:{row['person_code']}": {
            "check_in_time": row["shift_check_in_time"],
            "check_in_grace_minutes": row["shift_check_in_grace_minutes"],
            "check_out_time": row["shift_check_out_time"],
            "check_out_grace_minutes": row["shift_check_out_grace_minutes"],
        }
        for row in rows
    }
    config_store.save_config({"staff_shift_windows": staff_shift_windows})


def count_active_staff(branch_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM staff WHERE branch_id = ? AND archived_at IS NULL AND status <> 'inactive'",
            (branch_id,),
        ).fetchone()
        return int(row[0] if row else 0)


def create_staff(branch_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Raises ValueError on missing required fields — caller (the Flask
    route) turns that into a 400."""
    full_name = str(payload.get("full_name") or "").strip()
    if not full_name:
        raise ValueError("full_name is required")

    import uuid

    staff_id = str(uuid.uuid4())
    now = utc_now()

    with _write_lock:
        with _connect() as conn:
            person_code = str(payload.get("person_code") or "").strip() or _next_person_code(
                conn, branch_id
            )
            conn.execute(
                """
                INSERT INTO staff (
                    id, branch_id, people_type, person_code, full_name, cnic, email,
                    phone, salary, department, designation, role, status, capacity_flag,
                    shift_check_in_time, shift_check_in_grace_minutes,
                    shift_check_out_time, shift_check_out_grace_minutes,
                    metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    staff_id, branch_id, str(payload.get("people_type") or "staff"),
                    person_code, full_name, payload.get("cnic"), payload.get("email"), payload.get("phone"),
                    float(payload.get("salary") or 0),
                    payload.get("department"), payload.get("designation"),
                    str(payload.get("role") or "staff"), str(payload.get("status") or "active"),
                    payload.get("capacity_flag"),
                    payload.get("shift_check_in_time"),
                    int(payload.get("shift_check_in_grace_minutes") or 15),
                    payload.get("shift_check_out_time"),
                    int(payload.get("shift_check_out_grace_minutes") or 15),
                    json.dumps(payload.get("metadata") or {}, separators=(",", ":")),
                    now, now,
                ),
            )
            conn.commit()

    _rebuild_staff_shift_windows(branch_id)
    result = get_staff(branch_id, staff_id)
    assert result is not None
    return result


def update_staff(branch_id: str, staff_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    editable = (
        "full_name", "cnic", "email", "phone", "salary", "department", "designation",
        "role", "status", "capacity_flag",
        "shift_check_in_time", "shift_check_in_grace_minutes",
        "shift_check_out_time", "shift_check_out_grace_minutes",
    )
    fields = {k: payload[k] for k in editable if k in payload}
    requested_person_code = payload.get("person_code", payload.get("personCode"))
    if requested_person_code is not None:
        requested_person_code = str(requested_person_code).strip()
        if not requested_person_code:
            raise ValueError("person_code cannot be empty")
    if "metadata" in payload:
        fields["metadata"] = json.dumps(payload["metadata"] or {}, separators=(",", ":"))
    if not fields and requested_person_code is None:
        return get_staff(branch_id, staff_id)

    with _write_lock:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            current = conn.execute(
                "SELECT people_type, person_code FROM staff WHERE branch_id = ? AND id = ?",
                (branch_id, staff_id),
            ).fetchone()
            if current is None:
                return None

            old_person_code = str(current["person_code"])
            people_type = str(current["people_type"])
            new_person_code = requested_person_code or old_person_code
            keep_existing_embeddings = False
            if new_person_code != old_person_code:
                duplicate = conn.execute(
                    "SELECT 1 FROM staff WHERE branch_id = ? AND person_code = ? AND id <> ?",
                    (branch_id, new_person_code, staff_id),
                ).fetchone()
                if duplicate:
                    raise ValueError(f"person_code '{new_person_code}' is already assigned")
                embedding_duplicate = conn.execute(
                    "SELECT DISTINCT full_name FROM staff_embeddings WHERE branch_id = ? AND people_type = ? AND person_code = ?",
                    (branch_id, people_type, new_person_code),
                ).fetchall()
                if embedding_duplicate:
                    current_name = str(payload.get("full_name") or "").strip().casefold()
                    imported_names = {str(row["full_name"] or "").strip().casefold() for row in embedding_duplicate}
                    if not current_name or imported_names != {current_name}:
                        raise ValueError(
                            f"person_code '{new_person_code}' already has face embeddings for another person. "
                            "Use a new staff ID or remove the other enrollment first."
                        )
                    keep_existing_embeddings = True
                fields["person_code"] = new_person_code

            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [utc_now(), branch_id, staff_id]
            conn.execute(
                f"UPDATE staff SET {set_clause}, updated_at = ? "
                f"WHERE branch_id = ? AND id = ?",
                values,
            )
            if new_person_code != old_person_code:
                if keep_existing_embeddings:
                    conn.execute(
                        "DELETE FROM staff_embeddings WHERE branch_id = ? AND people_type = ? AND person_code = ?",
                        (branch_id, people_type, old_person_code),
                    )
                else:
                    conn.execute(
                        "UPDATE staff_embeddings SET person_code = ? WHERE branch_id = ? AND people_type = ? AND person_code = ?",
                        (new_person_code, branch_id, people_type, old_person_code),
                    )
                conn.execute(
                    "UPDATE attendance_buffer SET person_code = ? WHERE branch_id = ? AND people_type = ? AND person_code = ?",
                    (new_person_code, branch_id, people_type, old_person_code),
                )
            if "full_name" in fields:
                conn.execute(
                    "UPDATE staff_embeddings SET full_name = ? WHERE branch_id = ? AND people_type = ? AND person_code = ?",
                    (fields["full_name"], branch_id, people_type, new_person_code),
                )
            conn.commit()

    if {"person_code", "shift_check_in_time", "shift_check_in_grace_minutes",
        "shift_check_out_time", "shift_check_out_grace_minutes"} & fields.keys():
        _rebuild_staff_shift_windows(branch_id)
    return get_staff(branch_id, staff_id)


DEFAULT_ARCHIVED_STAFF_RETENTION_DAYS = 60
# Keep in sync with Settings.tsx's RETENTION_DAY_OPTIONS — that's the
# dropdown the client-dashboard Settings page offers; this is what's
# actually enforced here if a stored value is ever missing or malformed
# (edited directly in the DB, an older payload from before this field
# existed, etc).
ALLOWED_ARCHIVED_STAFF_RETENTION_DAYS = (30, 60, 90)


def get_archived_staff_retention_days(branch_id: str) -> int:
    """Per-branch auto-delete window for archived staff, set from the
    Settings page's Archived Staff Retention dropdown and persisted in
    dashboard_config alongside company_profile/network/etc (see
    dashboard_routes.py's api_local_onboarding_complete, which round-trips
    this field the same way as every other Settings section — no
    dedicated endpoint needed). Falls back to the default for branches
    that haven't set one, or a value outside the allowed set.
    """
    saved = get_dashboard_config(branch_id)
    per_branch = saved.get("archived_staff_retention_days")
    raw = per_branch.get(branch_id) if isinstance(per_branch, dict) else None
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_ARCHIVED_STAFF_RETENTION_DAYS
    return (
        days
        if days in ALLOWED_ARCHIVED_STAFF_RETENTION_DAYS
        else DEFAULT_ARCHIVED_STAFF_RETENTION_DAYS
    )


def archive_staff(branch_id: str, staff_id: str) -> dict[str, Any] | None:
    """Archives a staff member: removes them from the active directory,
    immediately deletes their face embeddings, and stamps purge_after so
    retention_worker.py permanently deletes the now-empty record once the
    branch's retention window elapses.

    Embeddings are deleted here — at archive time, not at final purge —
    so an archived person stops being matched by the cameras right away
    instead of staying recognizable until the retention window runs out.
    This is a deliberate, confirmed trade-off: restoring an archived staff
    member (restore_staff) does NOT restore their face; re-enrollment is
    required after restore, same as the cloud backend's own
    archive_user_for_retention.

    Returns None if the record doesn't exist or is already archived
    (mirrors the previous bool-returning signature's "not found" case,
    just with archive details attached on success instead of a bare True).
    """
    retention_days = get_archived_staff_retention_days(branch_id)
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    purge_after = (now_dt + timedelta(days=retention_days)).isoformat()

    with _write_lock:
        with _connect() as conn:
            row = conn.execute(
                "SELECT people_type, person_code FROM staff "
                "WHERE branch_id = ? AND id = ? AND archived_at IS NULL",
                (branch_id, staff_id),
            ).fetchone()
            if not row:
                return None
            people_type, person_code = row

            cur = conn.execute(
                "DELETE FROM staff_embeddings "
                "WHERE branch_id = ? AND people_type = ? AND person_code = ?",
                (branch_id, people_type, person_code),
            )
            deleted_embeddings = cur.rowcount

            conn.execute(
                "UPDATE staff SET archived_at = ?, purge_after = ?, updated_at = ? "
                "WHERE branch_id = ? AND id = ?",
                (now, purge_after, now, branch_id, staff_id),
            )
            conn.commit()

    _rebuild_staff_shift_windows(branch_id)  # drop the archived person's override

    return {
        "deleted_embeddings": int(deleted_embeddings or 0),
        "retention_days": retention_days,
        "archived_at": now,
        "purge_after": purge_after,
    }


def restore_staff(branch_id: str, staff_id: str) -> bool:
    with _write_lock:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE staff SET archived_at = NULL, purge_after = NULL, updated_at = ? "
                "WHERE branch_id = ? AND id = ?",
                (utc_now(), branch_id, staff_id),
            )
            conn.commit()
            changed = cur.rowcount > 0
    if changed:
        _rebuild_staff_shift_windows(branch_id)
    return changed


def delete_staff_permanently(branch_id: str, staff_id: str) -> bool:
    """Hard-deletes one archived staff record and any face embeddings
    still on file for that person. The single code path both the manual
    "Delete" button (api_delete_archived_staff) and the automatic
    retention purge (purge_expired_archived_staff) go through, so
    embeddings can never be orphaned by one path cleaning up something
    the other forgot — in practice archive_staff already deleted them, so
    this is normally a no-op DELETE, but it's the backstop for any record
    archived before that fix existed.

    Requires the record to already be archived — this is an unrecoverable
    hard delete, never meant to run on an active staff member.
    """
    with _write_lock:
        with _connect() as conn:
            row = conn.execute(
                "SELECT people_type, person_code FROM staff "
                "WHERE branch_id = ? AND id = ? AND archived_at IS NOT NULL",
                (branch_id, staff_id),
            ).fetchone()
            if not row:
                return False
            people_type, person_code = row

            conn.execute(
                "DELETE FROM staff_embeddings "
                "WHERE branch_id = ? AND people_type = ? AND person_code = ?",
                (branch_id, people_type, person_code),
            )
            conn.execute(
                "DELETE FROM staff WHERE branch_id = ? AND id = ?",
                (branch_id, staff_id),
            )
            conn.commit()
    return True


def purge_expired_archived_staff(branch_id: str) -> dict[str, Any]:
    """Hard-deletes every archived staff record in this branch whose
    purge_after has passed. Called by retention_worker.py on a timer; also
    safe to call directly (e.g. a future manual "purge now" admin action).

    Compares timestamps in Python rather than via SQLite's datetime()
    against the stored ISO-8601-with-offset strings archive_staff writes
    (datetime.isoformat()) — avoids depending on exactly how permissive
    this SQLite build's date parsing is with a "+00:00" suffix, at the
    cost of one extra parse per archived row, which is negligible at this
    scale.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, purge_after FROM staff "
            "WHERE branch_id = ? AND archived_at IS NOT NULL AND purge_after IS NOT NULL",
            (branch_id,),
        ).fetchall()

    now = datetime.now(timezone.utc)
    purged_ids: list[str] = []
    for staff_id, purge_after in rows:
        try:
            due = datetime.fromisoformat(purge_after)
        except (TypeError, ValueError):
            continue
        if due <= now and delete_staff_permanently(branch_id, staff_id):
            purged_ids.append(staff_id)

    return {"purged_count": len(purged_ids), "purged_staff_ids": purged_ids}


# ── Local dashboard login (admin_auth) ───────────────────────────────────────
# Pure persistence only — password hashing/verification logic lives in
# local_node/auth.py, which calls these. Keeps this module doing exactly
# what it does everywhere else: store and retrieve rows, no policy.

def get_admin_by_email(branch_id: str, email: str) -> dict[str, Any] | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM admin_auth WHERE branch_id = ? AND email = ?",
            (branch_id, email.strip().lower()),
        ).fetchone()
        return dict(row) if row else None


def has_any_admin(branch_id: str) -> bool:
    with _connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM admin_auth WHERE branch_id = ?", (branch_id,)
        ).fetchone()[0]
        return count > 0


def upsert_admin(
    branch_id: str, email: str, password_hash: str, password_salt: str, full_name: str = "",
) -> None:
    now = utc_now()
    with _write_lock:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO admin_auth (branch_id, email, password_hash, password_salt, full_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(branch_id, email) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    password_salt = excluded.password_salt,
                    full_name = excluded.full_name,
                    updated_at = excluded.updated_at
                """,
                (branch_id, email.strip().lower(), password_hash, password_salt, full_name, now, now),
            )
            conn.commit()


# ── License token cache ──────────────────────────────────────────────────────

def get_cached_license_token() -> str | None:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT token FROM license_cache WHERE id = 1").fetchone()
        return row["token"] if row and row["token"] else None


def set_cached_license_token(token: str) -> None:
    with _write_lock:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO license_cache (id, token, cached_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    token = excluded.token,
                    cached_at = excluded.cached_at
                """,
                (token, utc_now()),
            )
            conn.commit()


# ── Backup state + incremental cursor helper ─────────────────────────────────

def get_backup_state() -> dict[str, Any]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM backup_state WHERE id = 1").fetchone()
        if not row:
            return {"id": 1, "last_backup_at": None, "last_prompted_at": None, "last_consent": None, "cursors": {}}
        data = dict(row)
        data["cursors"] = json.loads(data.get("cursors") or "{}")
        return data


def set_backup_state(**fields: Any) -> None:
    """Partial update, upserting the single row. `cursors`, if passed, must
    already be a dict (json-encoded here, not by the caller)."""
    current = get_backup_state()
    merged = {**current, **fields}
    with _write_lock:
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO backup_state (id, last_backup_at, last_prompted_at, last_consent, cursors)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    last_backup_at = excluded.last_backup_at,
                    last_prompted_at = excluded.last_prompted_at,
                    last_consent = excluded.last_consent,
                    cursors = excluded.cursors
                """,
                (
                    merged.get("last_backup_at"), merged.get("last_prompted_at"),
                    merged.get("last_consent"),
                    json.dumps(merged.get("cursors") or {}, separators=(",", ":")),
                ),
            )
            conn.commit()


_BACKUP_TABLE_ID_COLUMNS = {
    "staff": "id",
    "attendance_buffer": "local_event_id",
    "staff_embeddings": "id",
}


def rows_changed_since(
    table_name: str, timestamp_column: str, branch_id: str, since: str | None,
) -> list[dict[str, Any]]:
    """Generic incremental-cursor reader shared by every table
    backup_worker.py backs up — one query shape, so a future table added to
    the backup set is a tuple in backup_worker.py's list, not a new
    function here. Ordered by the same timestamp column so the caller's
    'new cursor = max(timestamp seen)' logic is correct even if this
    returns partial results some day (it doesn't currently cap rows, but
    keeping the ORDER BY means a future LIMIT stays safe to add)."""
    if table_name not in _BACKUP_TABLE_ID_COLUMNS:
        raise ValueError(f"Unknown backup table: {table_name}")
    query = f"SELECT * FROM {table_name} WHERE branch_id = ?"
    params: list[Any] = [branch_id]
    if since:
        query += f" AND {timestamp_column} > ?"
        params.append(since)
    query += f" ORDER BY {timestamp_column} ASC"

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]