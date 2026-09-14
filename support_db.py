"""
support_db.py
───────────────────────────────────────────────────────────────────────────────
Backward-compatible facade that re-exports functions from all modular
support_db_* packages.
"""

from support_db_core import *
from support_db_organizations import *
from support_db_branches import *
from support_db_billing import *
from support_db_licenses import *
from support_db_nodes import *
from support_db_client_users import *
from support_db_internal import *
from support_db_attendance_dashboard import *
from support_db_attendance_exceptions import *
from support_db_attendance_gate import *
from support_db_attendance_mobile import *
from support_db_attendance_settings import *
from support_db_dashboard_summary import *
from support_db_fast import *
from support_db_hierarchy import *
from support_db_hr_assistant import *
from support_db_notifications import *
from support_db_payroll import *
from support_db_shift_overlap import *
from support_db_shifts import *
from support_db_staff import *
from support_db_time_utils import *
from support_db_visits import *


def _normalize_camera_type(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    if val in ("webcam", "usb", "integrated"):
        return "webcam"
    if val in ("ip", "rtsp", "network"):
        return "ip"
    return "nvr" if val in ("nvr", "dvr") else (val or "nvr")