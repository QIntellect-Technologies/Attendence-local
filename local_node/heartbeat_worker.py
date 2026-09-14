from __future__ import annotations

import socket
import threading
from datetime import datetime, timezone
from typing import Any
from local_node import local_db

from local_node.api_client import heartbeat
from local_node.config_store import has_node_api_key, load_config, read_runtime_status, write_runtime_status

AGENT_VERSION = "universal-node-1.0.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class HeartbeatWorker:
    def __init__(self, interval_seconds: int = 60) -> None:
        self.interval_seconds = max(15, int(interval_seconds or 60))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="heartbeat-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> dict[str, Any] | None:
        cfg = load_config()
        if not has_node_api_key(cfg):
            # Heartbeat/cloud-sync is install_token-only (needs the
            # X-Node-Api-Key credential minted by activate_node_with_
            # install_token). License-key-activated installs — the on-prem
            # default — have no such key and rely solely on
            # license_check.py's throttled revocation+config-refresh
            # channel; running this loop for them would just retry a 401
            # every interval_seconds forever. is_activated() alone can't
            # distinguish the two paths anymore (see config_store.py).
            return None
        runtime = read_runtime_status()
        payload = {
            "node_id": cfg.get("node_id"),
            "node_label": cfg.get("node_label"),
            "hostname": cfg.get("hostname") or socket.gethostname(),
            "agent_version": AGENT_VERSION,
            "attendance_mode": cfg.get("attendance_mode"),
            "local_time": utc_now(),
            "status": "online",
            **runtime,
        }
        result = heartbeat(payload)
        write_runtime_status({**runtime, "last_heartbeat_at": utc_now(), "last_heartbeat_status": "ok", "last_heartbeat_error": None})
        if isinstance(result, dict):
            if result.get("license_token"):
                local_db.set_cached_license_token(result["license_token"])
            elif str(result.get("license_status")).lower() in ("revoked", "inactive", "expired"):
                local_db.set_cached_license_token("")
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                runtime = read_runtime_status()
                write_runtime_status({**runtime, "last_heartbeat_at": utc_now(), "last_heartbeat_status": "failed", "last_heartbeat_error": str(exc)})
            self._stop.wait(self.interval_seconds)