# from __future__ import annotations

# from collections import deque
# from datetime import date, datetime, timezone
# from threading import Lock
# from typing import Any

# _events: deque[dict[str, Any]] = deque(maxlen=200)
# _suppressed_today: set[str] = set()
# _suppressed_date: str | None = None
# _lock = Lock()


# def utc_now() -> str:
#     return datetime.now(timezone.utc).isoformat()


# def publish_event(event: dict[str, Any]) -> dict[str, Any]:
#     row = {
#         "id": str(event.get("id") or event.get("local_event_id") or utc_now()),
#         "type": str(event.get("type") or "attendance"),
#         "name": str(event.get("name") or event.get("staff_name") or "Unknown"),
#         "staff_id": str(event.get("staff_id") or ""),
#         "status": str(event.get("status") or "marked_local"),
#         "confidence": float(event.get("confidence") or 0),
#         "message": str(event.get("message") or "Attendance marked locally."),
#         "marked_at": str(event.get("marked_at") or utc_now()),
#         "check_out_marked_at": event.get("check_out_marked_at"),
#         "sync_status": str(event.get("sync_status") or "pending"),
#         "camera_id": event.get("camera_id"),
#         "camera_name": event.get("camera_name"),
#         "department": event.get("department"),
#         "snapshot": event.get("snapshot"),
#         # Operator-facing context (e.g. "Detected early at 11:46, before the
#         # 11:50 shift start" — see local_db._format_late_check_in_note /
#         # _format_checkout_hold_note). Previously missing from this dict
#         # entirely, so every caller that passed "notes" into publish_event
#         # (attendance_sync_worker, manual_instructions_worker) had it
#         # silently dropped — /api/live-events never surfaced it and
#         # LiveAttendancePanel's `event.notes &&` check was always falsy.
#         "notes": event.get("notes"),
#     }
#     with _lock:
#         global _suppressed_date
#         today = date.today().isoformat()
#         if _suppressed_date != today:
#             _suppressed_today.clear()
#             _suppressed_date = today
#         if any(
#             identifier in _suppressed_today
#             for identifier in (row["staff_id"], row["name"])
#             if identifier
#         ):
#             return row
#         # Upsert by id (== local_event_id, stable per person/day): a repeat
#         # sighting updates that same person's existing card in place (e.g.
#         # check_in -> check_out) instead of appending a duplicate. Remove-
#         # then-appendleft also promotes it back to the front of the feed,
#         # same "most recent activity first" behavior a plain append gave us.
#         existing_index = next((i for i, e in enumerate(_events) if e["id"] == row["id"]), None)
#         if existing_index is not None:
#             del _events[existing_index]
#         _events.appendleft(row)
#     return row


# def list_events(limit: int = 100) -> list[dict[str, Any]]:
#     with _lock:
#         return list(_events)[: int(limit or 100)]


# def clear_events() -> None:
#     with _lock:
#         _events.clear()


# def suppress_person_for_today(staff_id: str | None, name: str | None) -> int:
#     """Remove and suppress one person's live card for the current local day."""
#     identifiers = {
#         str(value).strip()
#         for value in (staff_id, name)
#         if value is not None and str(value).strip()
#     }
#     if not identifiers:
#         return 0
#     global _suppressed_date
#     with _lock:
#         _suppressed_date = date.today().isoformat()
#         _suppressed_today.update(identifiers)
#         before = len(_events)
#         kept = deque(
#             (
#                 event
#                 for event in _events
#                 if event.get("staff_id") not in identifiers
#                 and event.get("name") not in identifiers
#             ),
#             maxlen=_events.maxlen,
#         )
#         _events.clear()
#         _events.extend(kept)
#         return before - len(_events)


from __future__ import annotations

from collections import deque
from datetime import date, datetime, timezone
from threading import Lock
from typing import Any


# Upper bound on distinct (person, day) cards this process holds in memory.
# ui_server.api_live_events() sizes its own per-request limit to the
# branch's actual enrolled headcount (feed_limit = max(enrolled_count,
# 100)), so this buffer only needs to be at least as large as any
# realistic branch headcount — 5000 is comfortably above that while still
# being a trivially small amount of memory (small dicts).
_MAX_EVENTS = 5000
_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_suppressed_today: set[str] = set()
_suppressed_date: str | None = None
_lock = Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def publish_event(event: dict[str, Any]) -> dict[str, Any]:
    row = {
        "id": str(event.get("id") or event.get("local_event_id") or utc_now()),
        "type": str(event.get("type") or "attendance"),
        "name": str(event.get("name") or event.get("staff_name") or "Unknown"),
        "staff_id": str(event.get("staff_id") or ""),
        "status": str(event.get("status") or "marked_local"),
        "confidence": float(event.get("confidence") or 0),
        "message": str(event.get("message") or "Attendance marked locally."),
        "marked_at": str(event.get("marked_at") or utc_now()),
        "check_out_marked_at": event.get("check_out_marked_at"),
        "sync_status": str(event.get("sync_status") or "pending"),
        "camera_id": event.get("camera_id"),
        "camera_name": event.get("camera_name"),
        "department": event.get("department"),
        "designation": event.get("designation"),
        "check_in_hold_reason": event.get("check_in_hold_reason"),
        "snapshot": event.get("snapshot"),
        # Operator-facing context (e.g. "Detected early at 11:46, before the
        # 11:50 shift start" — see local_db._format_late_check_in_note /
        # _format_checkout_hold_note). Previously missing from this dict
        # entirely, so every caller that passed "notes" into publish_event
        # (attendance_sync_worker, manual_instructions_worker) had it
        # silently dropped — /api/live-events never surfaced it and
        # LiveAttendancePanel's `event.notes &&` check was always falsy.
        "notes": event.get("notes"),
    }
    with _lock:
        global _suppressed_date
        today = date.today().isoformat()
        if _suppressed_date != today:
            _suppressed_today.clear()
            _suppressed_date = today
        if any(
            identifier in _suppressed_today
            for identifier in (row["staff_id"], row["name"])
            if identifier
        ):
            return row
        # Upsert by id (== local_event_id, stable per person/day): a repeat
        # sighting updates that same person's existing card in place (e.g.
        # check_in -> check_out) instead of appending a duplicate. Remove-
        # then-appendleft also promotes it back to the front of the feed,
        # same "most recent activity first" behavior a plain append gave us.
        existing_index = next((i for i, e in enumerate(_events) if e["id"] == row["id"]), None)
        if existing_index is not None:
            del _events[existing_index]
        _events.appendleft(row)
    return row


def list_events(limit: int = 100) -> list[dict[str, Any]]:
    with _lock:
        return list(_events)[: int(limit or 100)]


def clear_events() -> None:
    with _lock:
        _events.clear()


def suppress_person_for_today(staff_id: str | None, name: str | None) -> int:
    """Remove and suppress one person's live card for the current local day."""
    identifiers = {
        str(value).strip()
        for value in (staff_id, name)
        if value is not None and str(value).strip()
    }
    if not identifiers:
        return 0
    global _suppressed_date
    with _lock:
        _suppressed_date = date.today().isoformat()
        _suppressed_today.update(identifiers)
        before = len(_events)
        kept = deque(
            (
                event
                for event in _events
                if event.get("staff_id") not in identifiers
                and event.get("name") not in identifiers
            ),
            maxlen=_events.maxlen,
        )
        _events.clear()
        _events.extend(kept)
        return before - len(_events)