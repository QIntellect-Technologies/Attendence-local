"""
live_detections_feed.py
────────────────────────────────────────────────────────────────────────────
Single source of truth for the in-process "live detections" feed backing
GET /api/live-detections (the Live Attendance Monitoring page's cards).

Why this exists as its own module: it used to be three module-level globals
inside app.py (LATEST_STREAM_DETECTIONS, DETECTED_USERS_SESSION, cache_lock)
written to from exactly one place — CameraDetector._ai_loop, the cloud-mode
in-browser recognition pipeline. push_node_attendance() (support_db_nodes.py)
— the local-node sync path — never touched them, so any org running a local
node (this product's primary deployment) got check-ins on the Attendance
page immediately (it reads the `attendance` table directly) but NEVER got a
live-detections card, since nothing ever populated this feed for them.
Pulling the feed out into its own module lets both producers call the same
function instead of the local-node path re-implementing (and inevitably
drifting from) app.py's private globals.

Concurrency note: this is a per-process, in-memory list. Safe ONLY because
this service currently runs as a single Railway replica with a single
gunicorn worker (railway.json numReplicas=1; Dockerfile --workers 1 — the
face-model warm-up cache is the reason for that, not this feed, but this
feed piggybacks on the same guarantee). If that ever changes to multiple
workers/replicas, this must move to a shared store (Redis, or a small
Supabase table) or requests will nondeterministically miss detections
recorded by a different process.

Card lifecycle (upsert, not append-and-forget):
- A NEW check-in for a (org, staff, day) key inserts a new card.
- A CHECK-OUT for the same key updates that SAME card's fields in place
  (timestamp/status/confidence) rather than adding a second card or being
  silently dropped by dedup — the Live Attendance page should show one card
  per person per day that updates as their day progresses, matching how the
  cloud path's own single detection_entry per matched face already behaves
  within one session.
- Keys are not evicted on a timer; the ring buffer is capped at
  _MAX_DETECTIONS entries (oldest by insertion order dropped first), and a
  key naturally ages out once enough newer detections push it past the cap.
"""

import threading

_lock = threading.Lock()
_detections: list[dict] = []
_seen_dedup_keys: set = set()  # unbounded, permanent for process life — matches
                                # the pre-existing DETECTED_USERS_SESSION behavior
                                # this replaces; NOT the same thing as membership
                                # in the (capped) _detections buffer above.
_MAX_DETECTIONS = 10


def upsert_detection(key: str, entry: dict) -> None:
    """Insert a new card for `key`, or update it in place if one already
    exists in the current buffer (e.g. a check-out following that same
    person's check-in). `entry` must be a fully-formed detection dict —
    every field on it replaces the previous card's fields wholesale, so
    callers should pass the complete current state, not a partial patch.
    """
    with _lock:
        entry = dict(entry)
        entry["_key"] = key

        for index, existing in enumerate(_detections):
            if existing.get("_key") == key:
                del _detections[index]
                break

        # Always re-inserted at the front — an update (e.g. a checkout)
        # is itself a new, more recent event and should bubble back to the
        # top of the feed rather than staying wherever the original
        # check-in card had aged to.
        _detections.insert(0, entry)
        del _detections[_MAX_DETECTIONS:]


def record_detection(entry: dict, dedup_key: str | None = None) -> None:
    """Cloud-path compatible insert: always adds at the front, optionally
    suppressing repeats of the same dedup_key for the life of this process
    (matches the pre-existing CameraDetector ticker behavior). Kept
    separate from upsert_detection because the cloud path's "show once per
    session" semantics are intentionally different from the local-node
    path's "one card per person per day, updated on checkout" semantics —
    collapsing them into one function would have made one of the two
    behaviors an accidental side effect of the other.
    """
    with _lock:
        if dedup_key is not None:
            if dedup_key in _seen_dedup_keys:
                return
            _seen_dedup_keys.add(dedup_key)

        stored_entry = dict(entry)
        if dedup_key is not None:
            stored_entry["_key"] = dedup_key
        _detections.insert(0, stored_entry)
        del _detections[_MAX_DETECTIONS:]


def get_detections() -> list[dict]:
    """Snapshot of the current feed, newest first, with internal bookkeeping
    keys stripped out — callers (the /api/live-detections route) should
    never see `_key`/`_dedup_key` in the response.
    """
    with _lock:
        snapshot = list(_detections)

    return [
        {k: v for k, v in entry.items() if not k.startswith("_")}
        for entry in snapshot
    ]
