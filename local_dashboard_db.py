"""Local SQLite dashboard aggregates for local-only deployments."""
from __future__ import annotations

from datetime import date
from typing import Any

import attendance_sqlite
import local_people_db


def overview(org_id: str, branch_id: str | None = None, people_type: str | None = None) -> dict[str, Any]:
    staff = local_people_db.list_staff(org_id, branch_id, role="all", archived=False, people_type=people_type)
    attendance = attendance_sqlite.get_dashboard_logs(
        org_id, branch_id=branch_id, limit=5000,
        people_type=people_type, start=date.today().isoformat(), end=date.today().isoformat(),
    )
    present_ids = {str(row.get("user_id")) for row in attendance}
    late_count = sum(1 for row in attendance if row.get("check_in_status") == "late")
    branches: dict[str, dict[str, Any]] = {}
    for person in staff:
        key = str(person.get("branch_id") or "")
        branch = branches.setdefault(key, {"branchId": key, "branchName": key or "Main Branch", "staffCount": 0, "presentToday": 0, "absentToday": 0, "departments": []})
        branch["staffCount"] += 1
        if str(person.get("id")) in present_ids:
            branch["presentToday"] += 1
    for branch in branches.values():
        branch["absentToday"] = max(0, branch["staffCount"] - branch["presentToday"])
    present = len(present_ids)
    total = len(staff)
    return {
        "success": True,
        "stats": {
            "totalBranches": len(branches), "totalStaff": total,
            "presentToday": present, "absentToday": max(0, total - present),
            "avgAttendance": round((present / total) * 100, 2) if total else 0,
            "lateToday": late_count,
        },
        "staff": staff,
        "liveLog": attendance[:10],
        "shiftDistribution": [],
        "todayStatus": [
            {"name": "Present", "value": present},
            {"name": "Absent", "value": max(0, total - present)},
            {"name": "Late", "value": late_count},
        ],
        "weeklyAttendance": [], "branchWeeklyAttendance": [],
        "pendingLeaves": [], "cctvStatus": [],
        "attendancePerformance": [], "branchAttendancePerformance": [],
        "payrollTrends": [], "branchPayrollTrends": [],
        "branchPerformance": list(branches.values()),
    }
