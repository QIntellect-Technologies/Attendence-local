"""
local_node/retention_worker.py
─────────────────────────────────────────────────────────────────────────────
Background worker that permanently deletes archived staff records once
their per-branch retention window (30/60/90 days, set from the Settings
page's Archived Staff Retention dropdown — see
local_db.get_archived_staff_retention_days) has elapsed.

Face embeddings for an archived person are already deleted the moment
they're archived (local_db.archive_staff) — this worker's only job is
removing the now-empty staff row itself once its purge_after timestamp has
passed, which is what actually keeps the local SQLite database from
growing without bound. It also acts as a backstop for embeddings left over
from before that fix existed (local_db.delete_staff_permanently, which
this calls indirectly via purge_expired_archived_staff, always re-checks
for and deletes any straggling staff_embeddings rows too).

Runs on the same start/stop/interval convention as every other node
worker — see manual_instructions_worker.py and heartbeat_worker.py.
"""

from __future__ import annotations

import logging
import threading

from local_node import local_db
from local_node.config_store import get_branch_id, load_config

logger = logging.getLogger(__name__)


class RetentionWorker:
    def __init__(self, interval_seconds: int = 3600) -> None:
        # Hourly by default: this is housekeeping, not a latency-sensitive
        # job — a record living a few extra minutes past its exact
        # purge_after is harmless, and polling more often than that would
        # just be extra SQLite reads for no observable benefit.
        self.interval_seconds = max(60, int(interval_seconds or 3600))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, name="retention-worker", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> dict:
        branch_id = get_branch_id(load_config()) or "local-branch"
        result = local_db.purge_expired_archived_staff(branch_id)
        if result.get("purged_count"):
            logger.info(
                "retention-worker: permanently deleted %d archived staff "
                "record(s) past their retention window: %s",
                result["purged_count"], result["purged_staff_ids"],
            )
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.warning(
                    "retention-worker: run_once() raised, will retry next cycle",
                    exc_info=True,
                )
            self._stop.wait(self.interval_seconds)
