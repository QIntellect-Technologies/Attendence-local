"""
cloud_shift_gate.py

Real-time shift gate for the Flask backend's own camera recognition
pipeline (CameraDetector in app.py) — the cloud equivalent of
local_node/shift_gate.py, but a deliberately simpler variant with NO
held-for-review state at all.

Where this differs from both existing gates in this codebase:

  * local_node/shift_gate.py + local_db.record_attendance_local (on the
    local node) hold ambiguous detections for a human operator to
    resolve (held_for_review, check_in_hold_reason, check_out_hold_reason).

  * support_db_nodes.record_cloud_camera_attendance (the older,
    Supabase-`attendance`-table cloud path, now unused by the live
    camera loop — see attendance_sqlite.py's module docstring) accepts
    the FIRST post-check-in detection as the final checkout, whatever
    its timing, and flags it early/late/overtime for a dashboard
    exception queue.

  * THIS gate never holds and never raises an exception queue. Its
    rules, per product spec:

      - No attendance row yet today for this person -> a CHECK-IN
        attempt. The first detection at or before the check-in grace
        deadline (check_in_time + check_in_grace_minutes, branch-local)
        is confirmed immediately as an ON-TIME check-in. The first
        detection after that deadline is confirmed immediately as a
        LATE check-in. Either way it is final the instant it happens —
        there is nothing to review and nothing later can change it.

      - Check-in already recorded -> every further detection is a
        CHECK-OUT candidate. Only a detection landing inside the
        checkout grace window (check_out_time +/- check_out_grace_minutes)
        is accepted as the checkout, and it OVERWRITES any earlier
        in-window sighting — so the LAST detection inside the window
        wins, exactly like local_node's checkout leg. A detection
        outside that window (too early to be a real checkout, or after
        the window has closed) is never written as the checkout; it
        only updates a `last_seen_at` timestamp for informational
        purposes. There is no early/late/overtime classification here
        and nothing is ever held — the checkout is either confirmed by
        an in-window sighting or it silently stays whatever it already
        was.

Deliberately NOT handled by this module (kept out to match the
requested scope exactly — see attendance_sqlite.record_gated_attendance
for where these could be layered on later if wanted):
  - Overnight (midnight-crossing) shift bucket-date handling
    (local_node's shift_gate.resolve_attendance_bucket_date).
  - Overtime-extended checkout windows
    (support_db_attendance_gate.resolve_check_out_status's overtime_hours).
  - Manual attendance instructions / half-day leave are still honored
    because they arrive as part of the SAME resolved TimingWindow
    (resolve_timing_source already folds those in) — this module only
    ever sees the final merged window, it doesn't re-implement that
    precedence.
"""
from __future__ import annotations

from datetime import datetime, time as dt_time, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from support_db_attendance_gate import (
    resolve_timing_source,
    _get_branch_timezone,
    _parse_time,
)
from supabase_client import get_supabase

CheckInStatus = Literal["on_time", "late", "unscheduled"]
CheckOutAction = Literal["confirm", "info_only"]


def resolve_window(
    *,
    org_id: str,
    branch_id: str | None,
    staff: dict[str, Any],
    people_type: str,
    event_dt_utc: datetime,
) -> dict[str, Any] | None:
    """Thin pass-through to support_db_attendance_gate.resolve_timing_source
    so callers in this module (and attendance_sqlite.py) have one place to
    import from. `staff` must carry at least: id, shift_id_ref,
    check_in_grace_override, check_out_grace_override, person_code —
    exactly what SUPABASE_EMBEDDING_CACHE's 'staff' entry now carries (see
    app.py's refresh_supabase_embedding_cache / support_db_nodes.
    get_org_recognition_embeddings)."""
    return resolve_timing_source(
        org_id=org_id,
        branch_id=branch_id,
        staff=staff,
        people_type=people_type,
        event_time_utc=event_dt_utc,
    )


def get_branch_zone(org_id: str, branch_id: str | None) -> ZoneInfo:
    if not branch_id:
        return ZoneInfo("UTC")
    sb = get_supabase()
    return _get_branch_timezone(sb, org_id, branch_id)


def _local_minutes(event_dt_utc: datetime, branch_zone: ZoneInfo) -> int:
    dt = event_dt_utc if event_dt_utc.tzinfo else event_dt_utc.replace(tzinfo=timezone.utc)
    local = dt.astimezone(branch_zone)
    return local.hour * 60 + local.minute


def _target_minutes(value: dt_time | None) -> int | None:
    if value is None:
        return None
    return value.hour * 60 + value.minute


def classify_check_in(
    window: dict[str, Any] | None,
    event_dt_utc: datetime,
    branch_zone: ZoneInfo,
) -> CheckInStatus:
    """First-ever detection today for this person. No window configured
    at all -> 'unscheduled', treated by the caller the same as 'on_time'
    (nothing to be late against). Otherwise: on_time up through the
    grace deadline, late after it. There is no 'too early' bucket here —
    unlike local_node's shift_gate, this gate does not hold an early
    arrival waiting for a later on-time sighting; the first sighting IS
    the check-in, full stop."""
    if not window:
        return "unscheduled"
    target = _target_minutes(_parse_time(window.get("check_in_time")))
    if target is None:
        return "unscheduled"

    grace = int(window.get("check_in_grace_minutes") or 0)
    minutes = _local_minutes(event_dt_utc, branch_zone)
    return "on_time" if minutes <= target + grace else "late"


def classify_check_out_action(
    window: dict[str, Any] | None,
    event_dt_utc: datetime,
    branch_zone: ZoneInfo,
) -> CheckOutAction:
    """A detection after check-in is a checkout only inside the configured
    checkout grace window. Before that window opens, detections are ignored
    for checkout purposes. Missing or disabled checkout configuration also
    remains informational; it must never turn the next face sighting into an
    immediate checkout."""
    if not window or not window.get("capture_check_out"):
        return "info_only"
    target = _target_minutes(_parse_time(window.get("check_out_time")))
    if target is None:
        return "info_only"

    grace = int(window.get("check_out_grace_minutes") or 0)
    minutes = _local_minutes(event_dt_utc, branch_zone)
    return "confirm" if (target - grace) <= minutes <= (target + grace) else "info_only"
