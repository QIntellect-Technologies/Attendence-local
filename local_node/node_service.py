# from __future__ import annotations

# import threading
# from datetime import datetime, timezone

# from local_node import local_db
# from local_node.api_client import fetch_node_config
# from local_node.camera_config import get_enabled_cameras
# from local_node.camera_stream_manager import get_camera_stream_manager
# from local_node.config_store import get_branch_id, is_activated, load_config, save_config, write_runtime_status
# from local_node.heartbeat_worker import HeartbeatWorker
# from local_node.manual_instructions_worker import ManualInstructionsWorker
# from logger_config import get_logger


# logger = get_logger(__name__)

# def utc_now() -> str:
#     return datetime.now(timezone.utc).isoformat()


# class NodeService:
#     def __init__(self) -> None:
#         cfg = load_config()
#         self.interval_seconds = max(10, min(int(cfg.get("poll_interval_seconds") or 30), 300))
#         self._stop = threading.Event()
#         self._thread: threading.Thread | None = None
#         self.heartbeat_worker = HeartbeatWorker(15)
#         self.manual_instructions_worker = ManualInstructionsWorker(20)
#         self.camera_manager = get_camera_stream_manager()

#     def start(self) -> None:
#         local_db.init_db()
#         from local_node import recognition_engine
#         recognition_engine.warmup()   # blocks briefly, once, before any camera thread exists
#         self.heartbeat_worker.start()
#         self.manual_instructions_worker.start()
#         if self._thread and self._thread.is_alive():
#             return
#         self._thread = threading.Thread(target=self._run, name="node-service", daemon=True)
#         self._thread.start()


#     def stop(self) -> None:
#         self._stop.set()
#         self.heartbeat_worker.stop()
#         self.manual_instructions_worker.stop()
#         self.camera_manager.stop_all()

#     def run_cycle(self) -> dict:
#         if not is_activated():
#             status = {"cycle_status": "waiting_for_activation", "last_cycle_at": utc_now()}
#             write_runtime_status(status)
#             self.camera_manager.stop_all()
#             return status

#         cfg = load_config()
#         branch_id = get_branch_id(cfg) or "local-branch"
#         local_dashboard_config = local_db.get_dashboard_config(branch_id) if branch_id else {}
#         local_profile = (
#             local_dashboard_config.get("company_profile")
#             if isinstance(local_dashboard_config, dict)
#             else {}
#         )
#         local_timezone = (
#             local_profile.get("timezone")
#             if isinstance(local_profile, dict)
#             else None
#         )
#         if local_timezone and str(local_timezone).strip() != str(
#             (cfg.get("branch") or {}).get("timezone") or ""
#         ).strip():
#             save_config({
#                 "branch": {
#                     **(cfg.get("branch") or {}),
#                     "timezone": str(local_timezone).strip(),
#                 },
#             })
#             cfg = load_config()

#         # Network can be down for extended periods — this must never prevent
#         # cameras from running on the LAST KNOWN GOOD config. Only apply
#         # updates when the fetch actually succeeds; on failure, fall through
#         # using whatever is already saved in node_config.json from the
#         # previous successful poll.
#         offline = False
#         try:
#             runtime = fetch_node_config()
#         except Exception as exc:
#             offline = True
#             runtime = {}
#             logger.warning("run_cycle: cloud config fetch failed (%s) — continuing offline on last-known config", exc)

#         if isinstance(runtime, dict) and runtime:
#             sync_updates = {}
#             if "sync_delay_minutes" in runtime:
#                 sync_updates["sync_delay_minutes"] = int(runtime.get("sync_delay_minutes") or 0)
#             if "shift_mode_enabled" in runtime:
#                 sync_updates["shift_mode_enabled"] = bool(runtime.get("shift_mode_enabled", False))
#             if "shift_windows" in runtime:
#                 sync_updates["shift_windows"] = runtime.get("shift_windows") or {}
#             if "staff_shift_windows" in runtime:
#                 sync_updates["staff_shift_windows"] = runtime.get("staff_shift_windows") or {}
#             # Persist the camera list too — this is what run_cycle falls back to
#             # via get_enabled_cameras(cfg) when offline. Without this, cfg never
#             # carries cameras at all, so the very first failed fetch makes the
#             # offline branch see an empty camera list and sync_cameras() tears
#             # down every running camera (including local webcams that need no
#             # internet at all) instead of leaving them on the last-known set.
#             if "cameras" in runtime:
#                 sync_updates["cameras"] = runtime.get("cameras") or []
#             branch_info = runtime.get("branch") if isinstance(runtime.get("branch"), dict) else {}
#             if branch_info.get("timezone"):
#                 sync_updates["branch"] = {"timezone": branch_info["timezone"]}
#             if sync_updates:
#                 save_config(sync_updates)
#                 cfg = load_config()

#         mode = str((runtime.get("attendance_mode") if runtime else None) or cfg.get("attendance_mode") or "local").lower()
#         camera_config = cfg if offline else (runtime if runtime else cfg)
#         # The local dashboard stores onboarding edits in SQLite because they
#         # are UI configuration, not node activation settings. Use that latest
#         # local value so saving a camera takes effect without restarting the
#         # process or waiting for a cloud config refresh.
#         if mode == "local" and isinstance(local_dashboard_config, dict):
#             dashboard_cameras = local_dashboard_config.get("cameras")
#             if isinstance(dashboard_cameras, dict):
#                 dashboard_cameras = dashboard_cameras.get(branch_id) or dashboard_cameras.get("1")
#             if isinstance(dashboard_cameras, list):
#                 camera_config = {**camera_config, "cameras": dashboard_cameras}
#         cameras = get_enabled_cameras(camera_config)

#         if mode == "local" and branch_id:
#             camera_changes = self.camera_manager.sync_cameras(branch_id, cameras)
#             # Watchdog: a reader thread can stay alive while blocked forever
#             # inside cv2.VideoCapture's grab()/retrieve() after the RTSP
#             # socket stalls (no read failure ever raised, so
#             # _handle_open_or_read_failure/_publish_jpeg(reconnecting) never
#             # fire). Without this call, mjpeg_frames() just keeps re-sending
#             # the last real JPEG on every MJPEG_WAIT_TIMEOUT_SECONDS tick —
#             # the browser tile freezes on a stale frame with no error and no
#             # "Reconnecting…" placeholder. recover_stale_cameras() tears
#             # down and restarts capture for any camera whose last decoded
#             # frame is older than CAMERA_STALE_AFTER_SECONDS, which is the
#             # only thing that unblocks a hung capture short of a process
#             # restart. Runs every cycle (~poll_interval_seconds) right after
#             # sync_cameras so a stuck stream is caught quickly and the
#             # snapshot below reflects the restarted state.
#             stale_changes = self.camera_manager.recover_stale_cameras()
#             if stale_changes:
#                 camera_changes = [*camera_changes, *stale_changes]
#         else:
#             camera_changes = self.camera_manager.stop_all()

#         camera_snapshot = self.camera_manager.list_cameras()
#         status = {
#             "attendance_mode": mode,
#             "cycle_status": "ok" if not offline else "ok_offline",
#             "configured_cameras": len(cameras),
#             "streaming_cameras": len(camera_snapshot),
#             # Per-camera online/offline, keyed by camera_id inside each dict.
#             # heartbeat_worker.run_once() spreads this whole status dict into
#             # the heartbeat payload, so it lands in node_api_keys.
#             # last_heartbeat_payload['cameras'] with no separate call needed.
#             # Consumed by support_db_nodes.get_camera_live_status() to drive
#             # the Dashboard Overview CCTV Status card.
#             "cameras": camera_snapshot,
#             "last_cycle_at": utc_now(),
#             "last_error": None,
#             "camera_changes": camera_changes,
#             "camera_changes_at": utc_now() if camera_changes else None,
#             "offline": offline,
#         }
#         write_runtime_status(status)
#         return status



#     def _run(self) -> None:
#         while not self._stop.is_set():
#             try:
#                 self.run_cycle()
#             except Exception as exc:
#                 write_runtime_status({"cycle_status": "error", "last_cycle_at": utc_now(), "last_error": str(exc)})
#             self._stop.wait(self.interval_seconds)

#     # Attendance is check-in/checkout only now (no payroll module, no
#     # held-for-review workflow, no cloud attendance sync) — see
#     # local_db.record_attendance_local for the current, simplified state
#     # machine. The AttendanceSyncWorker and every mark_held_*/sync_*/
#     # delete_held_attendance wrapper that used to live here were removed
#     # along with it; attendance lives purely in local SQLite, and the only
#     # thing that ever leaves this machine for the cloud is the weekly,
#     # consent-gated backup (see backup_worker.py), not this table directly.

from __future__ import annotations

import threading
from datetime import datetime, timezone

from local_node import local_db
from local_node.api_client import fetch_node_config
from local_node.camera_config import get_enabled_cameras
from local_node.camera_stream_manager import get_camera_stream_manager
from local_node.config_store import get_branch_id, is_activated, load_config, save_config, write_runtime_status
from local_node.heartbeat_worker import HeartbeatWorker
from local_node.manual_instructions_worker import ManualInstructionsWorker
from local_node.retention_worker import RetentionWorker
from logger_config import get_logger


logger = get_logger(__name__)

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NodeService:
    def __init__(self) -> None:
        cfg = load_config()
        self.interval_seconds = max(10, min(int(cfg.get("poll_interval_seconds") or 30), 300))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.heartbeat_worker = HeartbeatWorker(15)
        self.manual_instructions_worker = ManualInstructionsWorker(20)
        self.retention_worker = RetentionWorker(3600)
        self.camera_manager = get_camera_stream_manager()

    def start(self) -> None:
        local_db.init_db()
        from local_node import recognition_engine
        recognition_engine.warmup()   # blocks briefly, once, before any camera thread exists
        self.heartbeat_worker.start()
        self.manual_instructions_worker.start()
        self.retention_worker.start()
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="node-service", daemon=True)
        self._thread.start()


    def stop(self) -> None:
        self._stop.set()
        self.heartbeat_worker.stop()
        self.manual_instructions_worker.stop()
        self.retention_worker.stop()
        self.camera_manager.stop_all()

    def run_cycle(self) -> dict:
        if not is_activated():
            status = {"cycle_status": "waiting_for_activation", "last_cycle_at": utc_now()}
            write_runtime_status(status)
            self.camera_manager.stop_all()
            return status

        cfg = load_config()
        branch_id = get_branch_id(cfg) or "local-branch"
        local_dashboard_config = local_db.get_dashboard_config(branch_id) if branch_id else {}
        local_profile = (
            local_dashboard_config.get("company_profile")
            if isinstance(local_dashboard_config, dict)
            else {}
        )
        local_timezone = (
            local_profile.get("timezone")
            if isinstance(local_profile, dict)
            else None
        )
        if local_timezone and str(local_timezone).strip() != str(
            (cfg.get("branch") or {}).get("timezone") or ""
        ).strip():
            save_config({
                "branch": {
                    **(cfg.get("branch") or {}),
                    "timezone": str(local_timezone).strip(),
                },
            })
            cfg = load_config()

        # Network can be down for extended periods — this must never prevent
        # cameras from running on the LAST KNOWN GOOD config. Only apply
        # updates when the fetch actually succeeds; on failure, fall through
        # using whatever is already saved in node_config.json from the
        # previous successful poll.
        offline = False
        try:
            runtime = fetch_node_config()
        except Exception as exc:
            offline = True
            runtime = {}
            logger.warning("run_cycle: cloud config fetch failed (%s) — continuing offline on last-known config", exc)

        if isinstance(runtime, dict) and runtime:
            sync_updates = {}
            if "sync_delay_minutes" in runtime:
                sync_updates["sync_delay_minutes"] = int(runtime.get("sync_delay_minutes") or 0)
            if "shift_mode_enabled" in runtime:
                sync_updates["shift_mode_enabled"] = bool(runtime.get("shift_mode_enabled", False))
            if "shift_windows" in runtime:
                sync_updates["shift_windows"] = runtime.get("shift_windows") or {}
            if "staff_shift_windows" in runtime:
                sync_updates["staff_shift_windows"] = runtime.get("staff_shift_windows") or {}
            # Persist the camera list too — this is what run_cycle falls back to
            # via get_enabled_cameras(cfg) when offline. Without this, cfg never
            # carries cameras at all, so the very first failed fetch makes the
            # offline branch see an empty camera list and sync_cameras() tears
            # down every running camera (including local webcams that need no
            # internet at all) instead of leaving them on the last-known set.
            if "cameras" in runtime:
                sync_updates["cameras"] = runtime.get("cameras") or []
            branch_info = runtime.get("branch") if isinstance(runtime.get("branch"), dict) else {}
            if branch_info.get("timezone"):
                sync_updates["branch"] = {"timezone": branch_info["timezone"]}
            if sync_updates:
                save_config(sync_updates)
                cfg = load_config()

        mode = str((runtime.get("attendance_mode") if runtime else None) or cfg.get("attendance_mode") or "local").lower()
        camera_config = cfg if offline else (runtime if runtime else cfg)
        # The local dashboard stores onboarding edits in SQLite because they
        # are UI configuration, not node activation settings. Use that latest
        # local value so saving a camera takes effect without restarting the
        # process or waiting for a cloud config refresh.
        if mode == "local" and isinstance(local_dashboard_config, dict):
            dashboard_cameras = local_dashboard_config.get("cameras")
            if isinstance(dashboard_cameras, dict):
                dashboard_cameras = dashboard_cameras.get(branch_id) or dashboard_cameras.get("1")
            if isinstance(dashboard_cameras, list):
                camera_config = {**camera_config, "cameras": dashboard_cameras}
        cameras = get_enabled_cameras(camera_config)

        if mode == "local" and branch_id:
            camera_changes = self.camera_manager.sync_cameras(branch_id, cameras)
            # Watchdog: a reader thread can stay alive while blocked forever
            # inside cv2.VideoCapture's grab()/retrieve() after the RTSP
            # socket stalls (no read failure ever raised, so
            # _handle_open_or_read_failure/_publish_jpeg(reconnecting) never
            # fire). Without this call, mjpeg_frames() just keeps re-sending
            # the last real JPEG on every MJPEG_WAIT_TIMEOUT_SECONDS tick —
            # the browser tile freezes on a stale frame with no error and no
            # "Reconnecting…" placeholder. recover_stale_cameras() tears
            # down and restarts capture for any camera whose last decoded
            # frame is older than CAMERA_STALE_AFTER_SECONDS, which is the
            # only thing that unblocks a hung capture short of a process
            # restart. Runs every cycle (~poll_interval_seconds) right after
            # sync_cameras so a stuck stream is caught quickly and the
            # snapshot below reflects the restarted state.
            stale_changes = self.camera_manager.recover_stale_cameras()
            if stale_changes:
                camera_changes = [*camera_changes, *stale_changes]
        else:
            camera_changes = self.camera_manager.stop_all()

        camera_snapshot = self.camera_manager.list_cameras()
        status = {
            "attendance_mode": mode,
            "cycle_status": "ok" if not offline else "ok_offline",
            "configured_cameras": len(cameras),
            "streaming_cameras": len(camera_snapshot),
            # Per-camera online/offline, keyed by camera_id inside each dict.
            # heartbeat_worker.run_once() spreads this whole status dict into
            # the heartbeat payload, so it lands in node_api_keys.
            # last_heartbeat_payload['cameras'] with no separate call needed.
            # Consumed by support_db_nodes.get_camera_live_status() to drive
            # the Dashboard Overview CCTV Status card.
            "cameras": camera_snapshot,
            "last_cycle_at": utc_now(),
            "last_error": None,
            "camera_changes": camera_changes,
            "camera_changes_at": utc_now() if camera_changes else None,
            "offline": offline,
        }
        write_runtime_status(status)
        return status



    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_cycle()
            except Exception as exc:
                write_runtime_status({"cycle_status": "error", "last_cycle_at": utc_now(), "last_error": str(exc)})
            self._stop.wait(self.interval_seconds)

    # Attendance is check-in/checkout only now (no payroll module, no
    # held-for-review workflow, no cloud attendance sync) — see
    # local_db.record_attendance_local for the current, simplified state
    # machine. The AttendanceSyncWorker and every mark_held_*/sync_*/
    # delete_held_attendance wrapper that used to live here were removed
    # along with it; attendance lives purely in local SQLite, and the only
    # thing that ever leaves this machine for the cloud is the weekly,
    # consent-gated backup (see backup_worker.py), not this table directly.