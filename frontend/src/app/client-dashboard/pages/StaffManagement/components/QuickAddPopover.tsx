/**
 * modules/staff/components/QuickAddPopover.tsx
 * ─────────────────────────────────────────────────────────────────────────────
 * Small "+" icon button that opens a floating single-input form. Built for
 * StaffModal's "add a department/designation without leaving this modal"
 * flow, but deliberately generic (label/placeholder/onSubmit are all props,
 * no department/designation knowledge here) so any future "add X inline"
 * need reuses this instead of hand-rolling another popover.
 * ─────────────────────────────────────────────────────────────────────────────
 */
import React, { useEffect, useRef, useState, type FC } from "react";
import { Plus } from "lucide-react";
import { T } from "../../../components/ui/theme";

export const QuickAddPopover: FC<{
  ariaLabel: string;
  placeholder: string;
  maxLength: number;
  /** Disables the trigger entirely (e.g. no department selected yet to
   * attach a new designation to). */
  disabled?: boolean;
  /** Shown as the trigger's title/tooltip while disabled. */
  disabledHint?: string;
  /** Rejecting (throwing) surfaces the error inline and keeps the popover
   * open; resolving closes it and clears the input. */
  onSubmit: (value: string) => Promise<void>;
}> = ({ ariaLabel, placeholder, maxLength, disabled, disabledHint, onSubmit }) => {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;

    const handleOutside = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("mousedown", handleOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [open]);

  const reset = () => {
    setValue("");
    setError(null);
    setIsSubmitting(false);
  };

  const handleSubmit = async () => {
    const clean = value.trim();
    if (!clean || isSubmitting) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await onSubmit(clean);
      reset();
      setOpen(false);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : "Failed to add.",
      );
      setIsSubmitting(false);
    }
  };

  return (
    <div style={{ position: "relative", display: "inline-block" }} ref={containerRef}>
      <button
        type="button"
        aria-label={ariaLabel}
        title={disabled ? disabledHint : ariaLabel}
        disabled={disabled}
        onClick={() => setOpen((prev) => !prev)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 22,
          height: 22,
          borderRadius: 6,
          border: `1px solid ${T.teal200}`,
          background: disabled ? T.slate50 : T.teal50,
          color: disabled ? T.muted : T.teal600,
          cursor: disabled ? "not-allowed" : "pointer",
          opacity: disabled ? 0.6 : 1,
          padding: 0,
        }}
      >
        <Plus size={13} />
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            right: 0,
            zIndex: 20,
            width: 220,
            padding: 10,
            borderRadius: 10,
            border: `1px solid ${T.border}`,
            background: T.card,
            boxShadow: "0 8px 24px rgba(16, 42, 63, 0.14)",
          }}
        >
          <input
            autoFocus
            value={value}
            maxLength={maxLength}
            placeholder={placeholder}
            onChange={(event) => {
              setValue(event.target.value);
              if (error) setError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") void handleSubmit();
            }}
            style={{
              width: "100%",
              height: 34,
              boxSizing: "border-box",
              border: `1px solid ${error ? T.red : T.border}`,
              borderRadius: 8,
              padding: "0 10px",
              fontSize: 12.5,
              fontFamily: "inherit",
              outline: "none",
              marginBottom: 8,
            }}
          />
          {error && (
            <div style={{ fontSize: 11, color: T.red, marginBottom: 8 }}>
              {error}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button
              type="button"
              onClick={() => {
                reset();
                setOpen(false);
              }}
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: T.muted,
                background: "transparent",
                border: "none",
                cursor: "pointer",
                padding: "6px 8px",
              }}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={isSubmitting || !value.trim()}
              style={{
                fontSize: 12,
                fontWeight: 700,
                color: "#fff",
                background: T.teal600,
                border: "none",
                borderRadius: 6,
                padding: "6px 12px",
                cursor: isSubmitting || !value.trim() ? "not-allowed" : "pointer",
                opacity: isSubmitting || !value.trim() ? 0.6 : 1,
              }}
            >
              {isSubmitting ? "Adding…" : "Add"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
