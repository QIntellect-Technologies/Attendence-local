"""
local_node/backup_worker.py

Weekly, consent-gated backup of this branch's local data to QIntellect's
side. Purely incremental and additive — this module never deletes or
wholesale-replaces anything already backed up: each run only pushes rows
whose cursor column (see local_db._BACKUP_TABLE_ID_COLUMNS /
rows_changed_since) advanced past the cursor recorded on the last
successful push, and the backend endpoint upserts each row by
(org_id, branch_id, table_name, row_id) — so re-sending a row overwrites
only its own prior copy with the newer version, never anyone else's data,
and never wipes rows this run didn't touch. This is the direct
implementation of "do not replace the data, only update the one that is
new."

Cadence: the dashboard SPA calls GET /api/backup/status once on boot
(the client's morning launch) and shows the consent popup when `due` is
True. "Weekly" is measured from last_prompted_at (when the popup was
last SHOWN), not from last_backup_at (when a backup last SUCCEEDED) —
otherwise a client who declines every single day would never stop being
asked more than the intended once-a-week cadence. Consent is asked fresh
every time it's due; there is deliberately no "always allow" setting,
since the client controls each backup individually, per spec.

Destination: pushed over the SAME transport/auth this node already uses
for attendance/embedding sync (POST .../v1/node/push-backup, node_api_key
header — see api_client.push_backup_rows) rather than a local file, for
two reasons:
(1) this node has no direct Supabase credentials on the client machine
by design (see api_client.py's docstring precedent), and giving it one
just for backups would be a new secret to protect for a narrower case
the existing transport already covers; (2) it gives support one place
(Supabase) to check backup health across every offline client, instead
of a flat file scattered on N client machines that nobody is watching.

Scope: exactly the three tables this client's purchased modules
(CCTV/Attendance/Staff) actually populate locally — the same set
local_db._BACKUP_TABLE_ID_COLUMNS already declares as backup-eligible.
Adding a table to the backup set means adding it there FIRST (it defines
the row-id column rows_changed_since needs), then adding its cursor
column here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from local_node import local_db
from local_node.api_client import NodeApiError, push_backup_rows
from local_node.config_store import get_branch_id, get_org_id

_PROMPT_INTERVAL_DAYS = 7

# (table_name, cursor/timestamp column) pairs, one per table this client's
# active modules populate locally. Column choice matters:
#  - staff.updated_at / staff_embeddings.imported_at: bumped on every
#    write already, nothing special needed.
#  - attendance_buffer.updated_at (NOT marked_at): marked_at only ever
#    reflects the check-in leg, so a checkout confirmed hours later (or a
#    held-review resolution) would never re-qualify an already-backed-up
#    row for re-sync if we cursor'd on marked_at. updated_at was added to
#    the schema specifically for this (see local_db.py's migration
#    docstring) and every INSERT/UPDATE in local_db.py sets it.
_BACKUP_TABLES: tuple[tuple[str, str], ...] = (
    ("staff", "updated_at"),
    ("attendance_buffer", "updated_at"),
    ("staff_embeddings", "imported_at"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_due(last_prompted_at: str | None) -> bool:
    last = _parse_iso(last_prompted_at)
    if last is None:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(days=_PROMPT_INTERVAL_DAYS)


def status() -> dict[str, Any]:
    """Backs GET /api/backup/status. Read-only — decides whether the
    dashboard should show this morning's consent popup, without
    performing or assuming any backup itself."""
    state = local_db.get_backup_state()
    return {
        "due": _is_due(state.get("last_prompted_at")),
        "last_backup_at": state.get("last_backup_at"),
        "last_prompted_at": state.get("last_prompted_at"),
        "last_consent": state.get("last_consent"),
    }


def run_backup_if_consented(allow: bool) -> dict[str, Any]:
    """Backs POST /api/backup/consent. Always records that the popup was
    answered (so `due` correctly waits another week either way) —
    actually pushes data only when allow=True.

    Per-table failures are isolated: one table erroring (e.g. a
    connectivity drop mid-push) never blocks the others, and that
    table's own cursor is left un-advanced so the exact same rows are
    simply retried on the next consented run — safe, because the backend
    upserts by row id rather than appending.
    """
    now = _utc_now()
    if not allow:
        local_db.set_backup_state(last_prompted_at=now, last_consent="declined")
        return {"success": True, "backed_up": False, "reason": "declined"}

    branch_id = get_branch_id()
    org_id = get_org_id()
    state = local_db.get_backup_state()
    cursors = dict(state.get("cursors") or {})
    pushed_counts: dict[str, int] = {}
    errors: dict[str, str] = {}

    for table_name, cursor_column in _BACKUP_TABLES:
        since = cursors.get(table_name)
        rows = local_db.rows_changed_since(table_name, cursor_column, branch_id, since)
        if not rows:
            continue
        try:
            push_backup_rows(org_id=org_id, branch_id=branch_id, table_name=table_name, rows=rows)
        except NodeApiError as exc:
            errors[table_name] = str(exc)
            continue
        cursors[table_name] = str(rows[-1][cursor_column])
        pushed_counts[table_name] = len(rows)

    local_db.set_backup_state(
        last_prompted_at=now,
        last_consent="allowed",
        # Only claim a completed backup when every table that had
        # pending rows actually succeeded — a partial failure must stay
        # visible in last_backup_at rather than being silently masked by
        # the tables that did succeed.
        last_backup_at=now if not errors else state.get("last_backup_at"),
        cursors=cursors,
    )
    return {
        "success": not errors,
        "backed_up": bool(pushed_counts),
        "pushed": pushed_counts,
        "errors": errors or None,
    }
