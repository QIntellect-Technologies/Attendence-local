from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from local_node import local_db
from local_node.api_client import NodeApiError, sync_attendance
from local_node.config_store import load_config
from local_node.live_events import publish_event

logger = logging.getLogger(__name__)


def should_sync_attendance_event(
    *,
    marked_at: str | datetime,
    now: str | datetime | None = None,
    configured_delay_minutes: int = 0,
    grace_minutes: int = 0,
) -> bool:
    if isinstance(marked_at, str):
        try:
            marked_dt = datetime.fromisoformat(marked_at.replace("Z", "+00:00"))
        except Exception:
            return True
    else:
        return max(300, delay_minutes * 60)

    if marked_dt.tzinfo is None:
        marked_dt = marked_dt.replace(tzinfo=timezone.utc)

    if now is None:
        current_dt = datetime.now(timezone.utc)
    elif isinstance(now, str):
        try:
            current_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        except Exception:
            current_dt = datetime.now(timezone.utc)
    else:
        current_dt = now

    if current_dt.tzinfo is None:
        current_dt = current_dt.replace(tzinfo=timezone.utc)

    delay_minutes = max(0, int(configured_delay_minutes or 0))
    grace_window_minutes = max(0, int(grace_minutes or 0))
    required_minutes = max(delay_minutes, grace_window_minutes)
    return (current_dt - marked_dt).total_seconds() >= required_minutes * 60


class AttendanceSyncWorker:
    def __init__(self, batch_size: int = 100) -> None:
        # No fixed interval anymore — computed live from the branch's
        # configured sync_delay_minutes every time the loop wakes up, so
        # this worker only does its own polling as often as the client
        # dashboard's OWN setting actually requires. Manual "Sync
        # attendance" (force_all=True) and "Sync selected" bypass this
        # entirely via NodeService.sync_all_attendance /
        # sync_selected_attendance, unaffected by this loop.
        self.batch_size = max(1, min(int(batch_size or 100), 500))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _current_interval_seconds(self) -> int:
        cfg = load_config()
        delay_minutes = max(0, int(cfg.get("sync_delay_minutes") or 0))
        # Floor of 5 minutes: even a delay of 0 (sync ASAP once ready)
        # should not turn into a tight polling loop.
        return max(300, delay_minutes * 60)

    def _sync_ready(self, row: dict[str, Any]) -> bool:
        if row.get("check_out_marked_at") and row.get("check_out_confirmed"):
            marked_at = row["check_out_marked_at"]
            metadata = row.get("check_out_metadata")
        else:
            marked_at = row.get("marked_at")
            metadata = row.get("metadata")

        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata or "{}")
            except Exception:
                metadata = {}

        ready_at = (metadata or {}).get("ready_at")
        if ready_at:
            return should_sync_attendance_event(marked_at=ready_at, now=None, configured_delay_minutes=0)

        # No shift/simple-mode window resolved at write time at all — the
        # one true fallback case. Measure from the real detection instant
        # using the branch-wide cfg default.
        if not marked_at:
            return True
        cfg = load_config()
        fallback_delay = max(0, int(cfg.get("sync_delay_minutes") or 0))
        return should_sync_attendance_event(marked_at=marked_at, now=None, configured_delay_minutes=fallback_delay)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="attendance-sync-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    

    
    def run_once(self, force_all: bool = False, local_event_ids: list[str] | None = None) -> int:
        """Three call shapes, in increasing order of scope:

                - default: only pending rows past their sync delay/grace window.
                - force_all=True: push every pending row, ignoring delay/grace.
                - local_event_ids=<list>: push the selected pending rows.

        The push-to-cloud and post-sync publish/local-DB-update logic below
        is identical for all three shapes; only which rows get selected
        differs."""
        current_branch_id = str(load_config().get("branch_id") or "")

        if local_event_ids:
            rows = [
                row for row in local_db.attendance_rows_by_ids(local_event_ids)
                if row.get("sync_status") == "pending"
            ]
        elif force_all:
            rows = local_db.pending_attendance(current_branch_id, self.batch_size)
        else:
            rows = local_db.pending_attendance(current_branch_id, self.batch_size)

        if not rows:
            logger.debug("attendance-sync: nothing unsynced/pending on this node right now")
            return 0

        explicit_selection = force_all or bool(local_event_ids)
        ready_rows = rows if explicit_selection else [row for row in rows if self._sync_ready(row)]
        if not ready_rows:
            logger.debug(
                "attendance-sync: %d row(s) pending but not yet past sync_delay_minutes/grace", len(rows),
            )
            return 0

        records: list[dict[str, Any]] = []
        for row in ready_rows:
            metadata = dict(row.get("metadata") or {})
            records.append({
                "local_event_id": row["local_event_id"],
                "people_type": row["people_type"],
                "person_code": row["person_code"],
                "confidence": row["confidence"],
                "source": row["source"],
                "camera_id": row.get("camera_id"),
                "marked_at": row["marked_at"],
                "check_in_confirmed": bool(row.get("check_in_confirmed")),
                "metadata": metadata,
                "check_out_marked_at": row.get("check_out_marked_at") if row.get("check_out_confirmed") else None,
                "check_out_confidence": row.get("check_out_confidence") if row.get("check_out_confirmed") else None,
                "check_out_camera_id": row.get("check_out_camera_id") if row.get("check_out_confirmed") else None,
                "check_out_metadata": row.get("check_out_metadata") if row.get("check_out_confirmed") else {},
                "notes": row.get("notes"),
                "status": row.get("status"),
            })

        logger.info(
            "attendance-sync: pushing %d record(s) to cloud%s -> %s",
            len(records),
            " (selected by operator)" if local_event_ids else (" (forced flush)" if force_all else ""),
            ", ".join(f'{r["people_type"]}:{r["person_code"]}' for r in records),
        )

        try:
            response = sync_attendance(records)
        except Exception as exc:
            logger.warning(
                "attendance-sync: push FAILED, request never reached/completed "
                "against the cloud endpoint (%d record(s) held locally): %s",
                len(records), exc,
            )
            local_db.mark_attendance_failed([r["local_event_id"] for r in records], str(exc))
            raise NodeApiError(str(exc)) from exc

        results_by_id = {r["local_event_id"]: r for r in response.get("results", [])}
        synced_ids, failed = [], []

        # 'already_marked' is a legitimate terminal outcome from the backend
        # (e.g. checkout detected but capture_check_out is off for this
        # people_type, or a genuine same-day duplicate) — not a sync error.
        # Treating it as a failure here retried the same event every 30s
        # forever, since retrying never changes that outcome.
        TERMINAL_SYNCED_STATUSES = ("inserted", "updated", "already_marked")

        for row in ready_rows:
            result = results_by_id.get(row["local_event_id"])
            if result and result.get("status") in TERMINAL_SYNCED_STATUSES:
                synced_ids.append(row["local_event_id"])
                logger.info(
                    "attendance-sync: %s (%s:%s) -> cloud accepted: %s",
                    row.get("staff_name") or row["person_code"], row["people_type"], row["person_code"],
                    result.get("status"),
                )
            else:
                reason = result.get("reason") if result else "No acknowledgment from backend"
                failed.append((row["local_event_id"], reason))
                logger.warning(
                    "attendance-sync: %s (%s:%s) -> cloud REJECTED/no-ack: %s",
                    row.get("staff_name") or row["person_code"], row["people_type"], row["person_code"],
                    reason,
                )

        if synced_ids:
            local_db.mark_attendance_synced(synced_ids)
            for row in rows:
                if row["local_event_id"] in synced_ids:
                    # status (event type: checked_in/checked_out) and
                    # sync_status (pending/synced) are
                    # independent axes — this re-publish must only update
                    # the latter. Overwriting status with "synced" here
                    # used to erase the check-in/check-out label at the
                    # exact moment sync succeeded, since live_events now
                    # upserts this same card in place rather than
                    # appending a new one.
                    is_check_out = bool(row.get("check_out_confirmed"))
                    # Same fix as camera_stream_manager._detect_and_record:
                    # row["marked_at"] is always the original check-in
                    # time, so a checked-out card must be given
                    # check_out_marked_at as its displayed timestamp
                    # instead, or it keeps showing the check-in time even
                    # after sync confirms the checkout.
                    effective_marked_at = row.get("check_out_marked_at") if is_check_out else row["marked_at"]
                    publish_event({
                        "id": row["local_event_id"],
                        "name": row.get("staff_name") or row["person_code"],
                        "staff_id": row["person_code"],
                        "status": "checked_out" if is_check_out else "checked_in",
                        "confidence": row["confidence"],
                        "message": "Attendance synced to cloud.",
                        "marked_at": effective_marked_at,
                        "check_out_marked_at": row.get("check_out_marked_at") if is_check_out else None,
                        "sync_status": "synced",
                        "camera_id": row.get("camera_id"),
                        "notes": row.get("notes"),
                    })

        for local_event_id, reason in failed:
            local_db.mark_attendance_failed([local_event_id], reason)

        return len(synced_ids)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                pass
            self._stop.wait(self._current_interval_seconds())