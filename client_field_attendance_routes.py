"""
client_field_attendance_routes.py
──────────────────────────────────────────────────────────────────────────────
Mobile self-service attendance for FIELD staff (client_staff rows with
staff_type='field') — the geofence counterpart of
client_staff_attendance_routes.py's office/WiFi flow.

Writes into the same Supabase `attendance` table the Client Dashboard reads,
tagged source='mobile_field' (or 'mobile_fallback' for a delayed offline
sync), so a field check-in shows up on the dashboard immediately, distinct
from an office WiFi mark, with the geofence evaluation (inside/distance/
radius/configured) folded into metadata for admin review.

Previously the app called /api/attendance/check-geofence and
/api/field/geo-alert (geofence_service.dart) and /api/field/mark-attendance,
/api/field/attendance-logs (api_service.dart) — none of which existed as
routes; every field attendance attempt was silently hitting a 404 and the
app's local fallback logic. This blueprint is the actual fix. Register it
in app.py alongside client_staff_attendance_bp.

Geofence evaluation itself runs on-device (GeofenceService.evaluateGeofence
in the Flutter app, mirroring support_db.evaluate_field_geofence's exact
contract) against config already pushed to the app at login — the same
trust boundary this module already uses for office WiFi (wifi_verified in
client_staff_attendance_routes.py is likewise computed on-device and taken
as-is). mark-attendance below stores what the device computed instead of
re-fetching the staff row and recomputing the distance server-side on
every mark; /check-geofence is kept only as an unused-by-the-app fallback.

Face verification (verify-face below) is the one check in this flow that
does NOT move client-side: face_verification_screen.dart posts a single
still frame and this route runs the actual match against the caller's own
enrolled embeddings (face_embeddings_cloud) server-side, then returns
verified true/false. Unlike geofence/WiFi, this is the signal that exists
specifically to stop one person marking attendance for another, so the
match decision has to live somewhere the device itself can't assert it
away. The app previously posted to /api/attendance/verify-face, which
never existed as a route either — fixed to /api/field/verify-face to sit
alongside this blueprint's other field-staff endpoints.
"""
from __future__ import annotations

from flask import Blueprint, request, g, jsonify

from client_staff_auth import require_client_staff_auth
from logger_config import get_logger
from client_routes_helpers import ok, handle
import support_db as support_cp_db

client_field_attendance_bp = Blueprint(
    "client_field_attendance", __name__, url_prefix="/api/field"
)

logger = get_logger(__name__)


def _require_lat_lng(payload: dict) -> tuple[float, float]:
    lat = payload.get("latitude", payload.get("lat"))
    lng = payload.get("longitude", payload.get("lng"))
    if lat is None or lng is None:
        raise ValueError("latitude/longitude are required")
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        raise ValueError("latitude/longitude must be numbers")


def _sanitize_geofence(payload: dict) -> dict:
    """Shape a client-supplied geofence object for storage/display only.

    NOT used to decide anything any more (see mark_field_attendance below,
    which now calls support_db.evaluate_field_geofence itself instead of
    trusting this). Geofence evaluation happening purely on-device used to
    be a deliberate trust boundary, the same one this app still uses for
    office WiFi (mark_attendance in client_staff_attendance_routes.py
    takes wifi_verified from the client body as-is) -- but unlike WiFi
    SSID/BSSID, GPS coordinates are trivially spoofable via a mock-location
    provider (a standard "Fake GPS" app), which let an employee mark
    attendance from anywhere while the app's own geofence check reported
    "inside" the whole time. This helper is kept only for the
    /check-geofence fallback route and any other read-only display use --
    it must never again be the thing that decides inside/outside for a
    mark.
    """
    raw = payload.get("geofence")
    if not isinstance(raw, dict):
        return {"configured": False, "inside": False, "distance": 0.0, "radius": None, "label": None}

    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    label = raw.get("label")
    return {
        "configured": bool(raw.get("configured")),
        "inside": bool(raw.get("inside")),
        "distance": _num(raw.get("distance")) or 0.0,
        "radius": _num(raw.get("radius")),
        "label": str(label).strip() or None if label is not None else None,
    }


@client_field_attendance_bp.route("/check-geofence", methods=["POST"])
@require_client_staff_auth
def check_geofence():
    """
    Body: { "latitude": float, "longitude": float }

    The mobile app no longer calls this route on its attendance path —
    it evaluates the geofence on-device instead (GeofenceService.
    evaluateGeofence, mirroring evaluate_field_geofence's exact contract)
    using the config already pushed to it at login, so it doesn't need a
    server round trip just to preview its own status. This route is kept
    for any other caller (e.g. a future admin-side preview) that still
    wants the server-computed answer; it costs nothing while unused.

    staff_id/org_id come from g.client_staff (verified JWT), never the
    request body — a mobile client can't check (or spoof) another staff
    member's geofence by editing user_id, because there is no such field
    read here.

    Returns { configured, inside, distance, radius, label } — see
    support_db.evaluate_field_geofence's docstring for what `configured`
    means and why it matters.
    """
    def _run():
        payload = request.get_json(silent=True) or {}
        lat, lng = _require_lat_lng(payload)
        result = support_cp_db.evaluate_field_geofence(
            org_id=g.client_staff["org_id"],
            staff_id=g.client_staff["id"],
            latitude=lat,
            longitude=lng,
        )
        return ok(result)

    return handle(_run)


@client_field_attendance_bp.route("/liveness-challenge", methods=["POST"])
def liveness_challenge():
    """Retired: face-verification liveness challenge is no longer an
    active feature. Kept as a 410 rather than removed outright in case
    the mobile app still has an old build calling it."""
    return jsonify({
        "success": False,
        "error": "This endpoint has been retired.",
    }), 410


@client_field_attendance_bp.route("/verify-face", methods=["POST"])
def verify_face():
    """Retired: server-side face-verification for field/remote staff is
    no longer an active feature — see liveness_challenge() above."""
    return jsonify({
        "success": False,
        "error": "This endpoint has been retired.",
    }), 410


@client_field_attendance_bp.route("/mark-attendance", methods=["POST"])
@require_client_staff_auth
def mark_field_attendance():
    """
    Body: { "latitude"|"lat": float, "longitude"|"lng": float,
            "is_mocked": bool (optional, defaults false -- Geolocator's
              on-device mock-location signal, see geofence_service.dart's
              isMockLocation),
            "geofence": {"configured": bool, "inside": bool,
                         "distance": float, "radius": float,
                         "label": str|null} (optional -- see below,
                         no longer trusted for the actual decision),
            "synced_after_offline": bool,
            "client_action_id": str (optional -- offline queue's
              idempotency key, see mark_client_staff_attendance's
              docstring for the exact replay contract),
            "face_verified": bool (optional -- only present when this
              mark is the sync-time completion of an offline-queued
              selfie, i.e. OfflineQueueService's 'field_attendance_offline'
              case; omit entirely for the normal live path, which already
              ran /verify-face synchronously before calling here),
            "face_similarity": float (optional, accompanies face_verified) }

    Recomputes the geofence evaluation itself server-side
    (support_db.evaluate_field_geofence, from `lat`/`lng` against this
    staff member's assigned geofence_lat/geofence_lng/
    geofence_radius_meters) rather than trusting whatever the client's
    on-device GeofenceService.evaluateGeofence claimed. GPS coordinates,
    unlike WiFi SSID/BSSID, are trivially spoofable with a mock-location
    provider ("Fake GPS" apps) -- a spoofed lat/lng would make the
    client's own on-device check report "inside" too, so the fix isn't
    "trust the server's math instead of the client's math" (both would
    compute the same distance from the same fake coordinates), it's that
    this route now (a) is the single place that computes the number used
    for the decision at all, so it can't silently diverge from what an
    admin later reviews, and (b) actually acts on `is_mocked` and
    "outside the assigned geofence" by routing the mark into the same
    admin-review hold a face mismatch gets (see
    mark_field_staff_attendance's identity_hold_reasons), instead of the
    old behavior of only firing a geo-alert nothing downstream consumed.
    A mark is still never *rejected* outright for either reason -- same
    as a face mismatch, it lands but is flagged -- so a genuine GPS drift
    or a legitimately reassigned work site doesn't just fail silently;
    an admin decides.

    The client's own `geofence` object (if sent) is intentionally NOT fed
    into the decision here -- it's a legacy field from when the on-device
    check was authoritative, kept only so `_sanitize_geofence` can still
    validate/shape it for any caller that still sends it, but it is
    discarded in favor of the freshly recomputed result below.

    face_verified here is NEVER a client-asserted "trust me" the way
    geofence used to be -- it only ever carries the result of a real
    server-side /verify-face call the app already made (either
    synchronously on the live path, or at sync time for a queued offline
    capture). This route doesn't run face matching itself; it just
    threads through what already happened.
    """
    def _run():
        payload = request.get_json(silent=True) or {}
        lat, lng = _require_lat_lng(payload)
        org_id = g.client_staff["org_id"]
        staff_id = g.client_staff["id"]
        branch_id = g.client_staff.get("branch_id")

        # Authoritative, server-computed geofence result -- this, not
        # _sanitize_geofence(payload), is what actually decides
        # inside/outside for this mark. Reuses evaluate_field_geofence
        # (the same function /check-geofence already exposed but the
        # marking path never called) instead of duplicating the haversine
        # math here.
        geofence_result = support_cp_db.evaluate_field_geofence(
            org_id=org_id,
            staff_id=staff_id,
            latitude=lat,
            longitude=lng,
        )
        is_mocked = bool(payload.get("is_mocked", False))
        face_verified = payload.get("face_verified")
        result = support_cp_db.mark_field_staff_attendance(
            org_id=org_id,
            branch_id=branch_id,
            staff_id=staff_id,
            latitude=lat,
            longitude=lng,
            geofence_result=geofence_result,
            is_mocked=is_mocked,
            synced_after_offline=bool(payload.get("synced_after_offline", False)),
            client_action_id=payload.get("client_action_id"),
            face_verified=bool(face_verified) if face_verified is not None else None,
            face_similarity=payload.get("face_similarity"),
        )
        return ok(result)

    return handle(_run)


@client_field_attendance_bp.route("/geo-alert", methods=["POST"])
@require_client_staff_auth
def geo_alert():
    """
    Body: { "latitude": float, "longitude": float, "distance": float }

    Best-effort log that this employee marked (or attempted to mark)
    attendance while outside their assigned geofence — see
    support_db.record_field_geo_alert's docstring for why this is a log
    today rather than a queryable table. Never fails the request even if
    logging itself has a problem, matching the mobile app's own
    "silent fail" GeofenceService.sendGeoAlert.
    """
    def _run():
        payload = request.get_json(silent=True) or {}
        try:
            lat, lng = _require_lat_lng(payload)
        except ValueError:
            lat, lng = None, None
        distance = payload.get("distance")
        support_cp_db.record_field_geo_alert(
            org_id=g.client_staff["org_id"],
            staff_id=g.client_staff["id"],
            latitude=lat,
            longitude=lng,
            distance=distance,
        )
        return ok({"logged": True})

    return handle(_run)


@client_field_attendance_bp.route("/attendance-logs", methods=["GET"])
@require_client_staff_auth
def field_attendance_logs():
    """Own attendance history for the field app's history screen — same
    Supabase `attendance` table and the same per-staff isolation
    (g.client_staff) as client_staff_attendance_routes.py's /history."""
    def _run():
        limit = request.args.get("limit", type=int) or 100
        logs = support_cp_db.get_client_staff_attendance_history(
            org_id=g.client_staff["org_id"],
            staff_id=g.client_staff["id"],
            limit=limit,
        )
        return ok({"logs": logs})

    return handle(_run)