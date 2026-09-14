/**
 * AllDetectionsModal.tsx
 * ─────────────────────────────────────────────────────────────────────────────
 * "View All" popup for the Live Attendance Marker page's detection sidebar.
 * The sidebar itself only ever renders the latest MAX_VISIBLE_DETECTIONS
 * cards (see LiveAttendanceMarker.tsx); this modal shows the full list —
 * newest on top, oldest at the bottom, matching the order `detections`
 * already arrives in from both the cloud and local-node feeds.
 *
 * Deliberately reuses DetectionCard/TOKEN/cardStyle from DetectionCard.tsx
 * instead of re-implementing card markup here — one visual source of truth
 * for what a detection card looks like, whether it's in the sidebar or the
 * popup. (Importing from LiveAttendanceMarker.tsx itself would create a
 * circular module dependency, since it imports this file too.)
 */

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import ModernSelect from "../../components/ui/ModernSelect";
import type { LiveDetection } from "./api/liveStreamApi";
import { DetectionCard, TOKEN, cardStyle } from "./DetectionCard";

const VIEW_MODE_OPTIONS: { value: string; label: string }[] = [
  { value: "list", label: "List" },
  { value: "grid", label: "Grid" },
];

interface AllDetectionsModalProps {
  open: boolean;
  onClose: () => void;
  detections: LiveDetection[];
}

export default function AllDetectionsModal({
  open,
  onClose,
  detections,
}: AllDetectionsModalProps) {
  const [viewMode, setViewMode] = useState<"list" | "grid">("grid");

  // Escape-to-close + background scroll lock while the popup is open —
  // matches the click-outside-to-close convention already used by the
  // other modals in this app (RejectReasonModal, ShiftTimingsModal, etc.).
  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="All detections"
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1400,
        background: "rgba(15,23,42,0.48)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 20,
        boxSizing: "border-box",
      }}
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        style={{
          ...cardStyle,
          width: "100%",
          maxWidth: 960,
          // Fixed viewport-relative ceiling so the popup never stretches to
          // fit its content — the body scrolls internally instead (see the
          // overflowY: "auto" panel below).
          maxHeight: "85vh",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          overflow: "hidden",
          boxShadow: "0 20px 70px rgba(15,23,42,0.25)",
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: "14px 20px",
            borderBottom: `1px solid ${TOKEN.border}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            flexShrink: 0,
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h3
              style={{
                margin: 0,
                fontSize: 16,
                fontWeight: 800,
                color: TOKEN.head,
              }}
            >
              All Detections
            </h3>
            <span
              style={{
                background: TOKEN.teal,
                color: "#fff",
                fontSize: "0.7em",
                fontWeight: 800,
                padding: "2px 9px",
                borderRadius: 20,
                minWidth: 22,
                textAlign: "center",
              }}
            >
              {detections.length}
            </span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <ModernSelect
              value={viewMode}
              options={VIEW_MODE_OPTIONS}
              onChange={(v) => setViewMode(v as "list" | "grid")}
              ariaLabel="Detection view"
              minWidth={90}
            />
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                border: `1px solid ${TOKEN.border}`,
                background: TOKEN.cardBg,
                color: TOKEN.muted,
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        {/* Scrollable body — this is what grows/shrinks; the modal shell
            above stays capped at maxHeight, so overflow becomes a vertical
            scrollbar here rather than stretching the whole popup. Grid mode
            uses auto-fill/minmax so column count adapts to viewport width
            without any media queries (2 columns on a phone, more on a wide
            desktop popup). */}
        <div
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            overflowX: "hidden",
            padding: 14,
            display: "grid",
            gridTemplateColumns:
              viewMode === "grid" ? "repeat(auto-fill, minmax(230px, 1fr))" : "1fr",
            gap: 10,
            alignContent: "start",
          }}
        >
          {detections.map((d) => (
            <DetectionCard key={d.key} det={d} compact={viewMode === "grid"} />
          ))}
        </div>
      </div>
    </div>
  );
}