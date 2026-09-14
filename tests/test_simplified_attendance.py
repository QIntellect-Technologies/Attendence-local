"""
tests/test_simplified_attendance.py
─────────────────────────────────────────────────────────────────────────────
Regression coverage for the simplified check-in/checkout-only state machine
(no payroll concepts, no held-for-review workflow) in
local_node.local_db.record_attendance_local.

Spec under test:
  - First detection INSIDE the check-in window        -> confirmed check-in.
  - First detection AFTER the check-in window's grace  -> confirmed check-in,
    flagged late (check_in_hold_reason='late'). Never held.
  - A detection BEFORE the check-in window opens       -> ignored entirely,
    no row written.
  - The LAST detection inside the checkout window      -> confirmed checkout
    (each in-window sighting overwrites the previous one).
  - A checkout detection outside its window (early OR after grace closes)
    -> stored informationally only; check_out_confirmed stays 0 forever,
    never promotable to a real checkout.
"""
from __future__ import annotations

import datetime as real_dt_mod
from unittest.mock import patch

import pytest

import local_node.local_db as local_db
import local_node.shift_gate as shift_gate

CONFIG = {
    "shift_mode_enabled": True,
    "branch": {"timezone": "UTC"},
    "shift_windows": {
        "staff": {
            "check_in_time": "09:00",
            "check_in_grace_minutes": 15,
            "check_out_time": "17:00",
            "check_out_grace_minutes": 15,
        }
    },
}


def _dt(hour: int, minute: int) -> real_dt_mod.datetime:
    return real_dt_mod.datetime(2026, 9, 5, hour, minute, tzinfo=real_dt_mod.timezone.utc)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(local_db, "LOCAL_DB_PATH", db_path)
    local_db.init_db()
    yield


def _record(event_dt, **overrides):
    kwargs = dict(
        branch_id="branch-1", people_type="staff", person_code="0001",
        staff_name="Test Person", confidence=0.95, source="camera",
        camera_id="cam-1", metadata={}, event_dt_utc=event_dt,
    )
    kwargs.update(overrides)
    with patch.object(shift_gate, "load_config", return_value=CONFIG):
        return local_db.record_attendance_local(**kwargs)


def test_in_window_check_in_confirms_on_time():
    result = _record(_dt(9, 5))
    assert result["event_type"] == "check_in"
    assert result["check_in_hold_reason"] is None
    assert bool(result["check_in_confirmed"]) is True


def test_early_stray_before_window_is_ignored():
    result = _record(_dt(7, 0))
    assert result["event_type"] == "check_in_pre_shift_ignored"
    assert result["already_marked"] is False
    # Nothing should have been written for this person yet.
    stored = local_db.recent_attendance("branch-1", limit=10)
    assert stored == []


def test_late_check_in_auto_confirms_flagged_late():
    result = _record(_dt(9, 30))  # window closes at 09:15 (09:00 + 15min grace)
    assert result["event_type"] == "check_in_late"
    assert result["check_in_hold_reason"] == "late"
    assert bool(result["check_in_confirmed"]) is True


def test_checkout_last_in_window_sighting_wins():
    _record(_dt(9, 5))  # check-in
    _record(_dt(16, 50))  # first in-window checkout sighting
    result = _record(_dt(16, 58))  # second, later in-window sighting
    assert result["event_type"] == "check_out"
    assert bool(result["check_out_confirmed"]) is True
    assert result["check_out_marked_at"].startswith("2026-09-05T16:58")


def test_late_checkout_after_grace_never_confirms():
    _record(_dt(9, 5))  # check-in
    result = _record(_dt(17, 30))  # window closes at 17:15; this is after grace
    assert result["event_type"] == "check_out_unconfirmed"
    assert bool(result["check_out_confirmed"]) is False
    # Informational timestamp is still tracked though.
    assert result["check_out_marked_at"].startswith("2026-09-05T17:30")


def test_early_checkout_before_window_never_confirms():
    _record(_dt(9, 5))  # check-in
    result = _record(_dt(14, 0))  # well before 17:00 window opens
    assert result["event_type"] == "check_out_unconfirmed"
    assert bool(result["check_out_confirmed"]) is False


def test_stray_after_confirmed_checkout_is_ignored():
    _record(_dt(9, 5))
    _record(_dt(17, 5))  # confirms checkout
    result = _record(_dt(19, 0))  # random later sighting
    assert result["event_type"] == "stray_ignored"
    assert result["already_marked"] is True


def test_mark_absent_clears_all_person_code_variants_for_today():
    today = "2026-09-05"
    with local_db._connect() as conn:
        conn.execute(
            """
            INSERT INTO attendance_buffer (
                local_event_id, branch_id, people_type, person_code, staff_name,
                attendance_date, status, confidence, source, camera_id, metadata,
                marked_at, sync_status, check_in_confirmed, check_out_confirmed
            ) VALUES (?, ?, ?, ?, ?, ?, 'present', 0.9, 'camera', 'cam-1', '{}', ?, 'pending', 1, 0)
            """,
            ("branch-1:staff:STF-0001:2026-09-05", "branch-1", "staff", "STF-0001", "Alpha", today, "2026-09-05T09:00:00+00:00"),
        )
        conn.execute(
            """
            INSERT INTO attendance_buffer (
                local_event_id, branch_id, people_type, person_code, staff_name,
                attendance_date, status, confidence, source, camera_id, metadata,
                marked_at, sync_status, check_in_confirmed, check_out_confirmed
            ) VALUES (?, ?, ?, ?, ?, ?, 'present', 0.9, 'camera', 'cam-1', '{}', ?, 'pending', 1, 0)
            """,
            ("branch-1:staff:0001:2026-09-05", "branch-1", "staff", "0001", "Alpha", today, "2026-09-05T09:05:00+00:00"),
        )
        conn.commit()

    deleted = local_db.delete_attendance_for_staff("branch-1", "STF-0001", today)
    assert deleted == 2
    assert local_db.recent_attendance("branch-1", limit=20) == []
