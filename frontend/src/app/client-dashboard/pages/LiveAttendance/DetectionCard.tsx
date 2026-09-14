/**
 * DetectionCard.tsx
 * ─────────────────────────────────────────────────────────────────────────────
 * The single visual definition of a "detection card" for the Live Attendance
 * Marker page, plus the design tokens/card shell it's built on. Pulled out
 * of LiveAttendanceMarker.tsx so both the sidebar (which shows the latest
 * few) and AllDetectionsModal (the "View All" popup, which shows every one)
 * import the same component instead of one importing it from the other —
 * that would create a circular module dependency between the two files.
 */

import { memo, useState } from "react";
import { profilePhotoUrl, type LiveDetection } from "./api/liveStreamApi";
import { T } from "../../components/ui/theme";
import { useAuthenticatedImageUrl } from "../../hooks/useAuthenticatedImageUrl";

// ─── Design tokens (aligned with the light-card system) ──────────────────────

export const TOKEN = {
  // Surface
  pageBg: T.bg,
  cardBg: T.card,
  border: T.border,
  // Camera viewport — keeps its dark background for feed contrast
  camBg: "#080d14",
  camBorder: "#1e293b",
  // Teal accent system
  teal: T.teal600,
  tealLight: T.teal50,
  tealBorder: T.teal200,
  // Text
  head: T.head,
  body: T.body,
  muted: T.muted,
  // Status
  online: T.success,
  onlineBg: T.successBg,
  alert: T.amber,
  alertBg: T.amberBg,
  offline: T.slate300,
  // Detection badge
  matched: "#16a34a",
  matchedBg: "#f0fdf4",
  matchedBorder: "#bbf7d0",
  unknown: "#dc2626",
  unknownBg: "#fef2f2",
  unknownBorder: "#fecaca",
} as const;

// ─── Shared card style ────────────────────────────────────────────────────────

export const cardStyle: React.CSSProperties = {
  background: TOKEN.cardBg,
  borderRadius: 14,
  border: `1px solid ${TOKEN.border}`,
  boxShadow: "0 1px 4px rgba(15,45,74,0.06)",
};

// ─── Detection card ───────────────────────────────────────────────────────────

export const DetectionCard = memo(function DetectionCard({
  det,
  compact,
}: {
  det: LiveDetection;
  compact: boolean;
}) {
  const [imgErr, setImgErr] = useState(false);
  // /api/users/<id>/photo sits behind @require_client_dashboard_auth, but a
  // plain <img src> can't attach the Bearer token — route it through the
  // authenticated-fetch hook instead, same fix as the staff directory.
  const authedPhotoUrl = useAuthenticatedImageUrl(
    det.userId != null ? profilePhotoUrl(det.userId) : null,
  );
  const isUnknown = det.name === "Unknown";
  // Only meaningful for a check-in leg — a late CHECKOUT is a separate
  // concept (check_out_hold_reason) not currently surfaced on this card.
  const isLateCheckIn =
    det.status !== "checked_out" && det.checkInHoldReason === "late";
  const empId =
    det.userId != null ? `EMP-${String(det.userId).padStart(3, "0")}` : "—";
  const initials = det.name
    .split(" ")
    .map((n) => n[0] ?? "?")
    .join("")
    .toUpperCase()
    .slice(0, 2);
  const accentColor = isUnknown ? "#94a3b8" : TOKEN.teal;

  const time = det.timestamp
    ? new Date(det.timestamp).toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
    : "—";

  const isLocalNodeSource = (det.source ?? "").toLowerCase() === "local_node";
  const source = isLocalNodeSource
    ? null
    : (det.source ?? "")
      .replace("stream_", "")
      .replace(/_/g, " ")
      .toUpperCase() || "CAMERA";

  const faceCropSrc = det.faceCrop
    ? `data:image/jpeg;base64,${det.faceCrop}`
    : null;
  const avatarSrc =
    det.userId != null && !imgErr && authedPhotoUrl
      ? authedPhotoUrl
      : faceCropSrc;

  const departmentDesignation = [det.department, det.designation]
    .filter(Boolean)
    .join(" • ");

  return (
    <div
      style={{
        ...cardStyle,
        overflow: "hidden",
        animation: "detEnter 0.3s cubic-bezier(0.34,1.56,0.64,1)",
        transition: "border-color 0.15s, box-shadow 0.15s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = TOKEN.teal;
        e.currentTarget.style.boxShadow = "0 4px 18px rgba(58,175,169,0.12)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = TOKEN.border;
        e.currentTarget.style.boxShadow = "0 1px 4px rgba(15,45,74,0.06)";
      }}
    >
      <div style={{ display: "flex" }}>
        {/* Accent strip */}
        <div style={{ width: 4, background: accentColor, flexShrink: 0 }} />

        <div
          style={{
            flex: 1,
            padding: compact ? "10px 12px" : "12px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: compact ? 10 : 12,
              flexDirection: compact ? "column" : "row",
            }}
          >
            {/* Avatar */}
            <div style={{ position: "relative", flexShrink: 0 }}>
              <div
                style={{
                  width: compact ? 76 : 96,
                  height: compact ? 76 : 96,
                  borderRadius: 14,
                  background: isUnknown
                    ? "linear-gradient(135deg,#94a3b8,#64748b)"
                    : `linear-gradient(135deg, ${TOKEN.teal}, #1d8a84)`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: 800,
                  fontSize: compact ? "1.3em" : "1.6em",
                  color: "#fff",
                  overflow: "hidden",
                  border: `2px solid ${TOKEN.border}`,
                }}
              >
                {avatarSrc ? (
                  <img
                    src={avatarSrc}
                    alt={det.name}
                    onError={() => setImgErr(true)}
                    style={{ width: "100%", height: "100%", objectFit: "cover" }}
                  />
                ) : (
                  initials
                )}
              </div>
              <span
                style={{
                  position: "absolute",
                  bottom: -2,
                  right: -2,
                  width: 16,
                  height: 16,
                  borderRadius: "50%",
                  border: "2px solid #fff",
                  background: isUnknown ? "#94a3b8" : "#22c55e",
                }}
              />
            </div>

            {/* Info */}
            <div
              style={{
                flex: 1,
                minWidth: 0,
                textAlign: compact ? "center" : "left",
              }}
            >
              <div
                style={{
                  fontSize: compact ? "0.85em" : "0.95em",
                  fontWeight: 800,
                  color: TOKEN.head,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {det.name}
              </div>
              {departmentDesignation ? (
                <div style={{ fontSize: "0.72em", color: TOKEN.teal, fontWeight: 700, marginTop: 2 }}>
                  {departmentDesignation}
                </div>
              ) : (
                <div
                  style={{ fontSize: "0.7em", color: "#94a3b8", marginTop: 2 }}
                >
                  No department
                </div>
              )}
              <div
                style={{
                  fontSize: "0.68em",
                  color: TOKEN.muted,
                  fontFamily: "monospace",
                  marginTop: 2,
                }}
              >
                {empId}
              </div>
              <div
                style={{
                  fontSize: "0.67em",
                  color: "#94a3b8",
                  marginTop: 2,
                  display: "flex",
                  gap: 8,
                  justifyContent: compact ? "center" : "flex-start",
                }}
              >
                <span>{time}</span>
                {source ? <span>{source}</span> : null}
                {det.cameraName ? <span>{det.cameraName}</span> : null}
              </div>
            </div>
          </div>

          <div style={{ height: 1, background: TOKEN.border }} />

          <div
            style={{
              display: "flex",
              justifyContent: compact ? "center" : "flex-end",
            }}
          >
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                fontSize: "0.62em",
                fontWeight: 800,
                padding: "3px 9px",
                borderRadius: 6,
                background: isUnknown
                  ? TOKEN.unknownBg
                  : isLateCheckIn
                    ? TOKEN.alertBg
                    : TOKEN.matchedBg,
                border: `1px solid ${isUnknown
                  ? TOKEN.unknownBorder
                  : isLateCheckIn
                    ? TOKEN.alert
                    : TOKEN.matchedBorder
                  }`,
                color: isUnknown
                  ? TOKEN.unknown
                  : isLateCheckIn
                    ? TOKEN.alert
                    : TOKEN.matched,
              }}
            >
              {isUnknown ? "⚠ Unknown" : det.status === "checked_out" ? "✓ Checked Out"
                : isLateCheckIn ? "⚠ Checked In — Late" : "✓ Checked In"}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});