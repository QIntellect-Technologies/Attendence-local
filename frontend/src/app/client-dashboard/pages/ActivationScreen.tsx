import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  KeyRound,
  ShieldCheck,
  ShieldAlert,
  ArrowRight,
  Loader2,
  CheckCircle2,
  Building2,
  Calendar,
  AlertTriangle,
} from "lucide-react";
import { toastSuccess, toastError } from "../utils/notifications";
import {
  A,
  SplitAuthLayout,
  BrandPanel,
  AuthCard,
  AuthLabel,
  AuthButton,
  AuthError,
} from "../components/auth/AuthShared";

interface LicenseStatus {
  success?: boolean;
  org_status?: "active" | "not_activated" | "expired" | "invalid" | string;
  blocked?: boolean;
  message?: string | null;
  org_id?: string | null;
  expires_at?: number | null;
  issued_at?: number | null;
}

const BULLETS = [
  "Offline AI Facial Recognition",
  "Cryptographically Signed License",
  "Tamper-proof Local Attendance",
  "Real-time Camera Streaming",
  "Automated Shift & Break Tracking",
];

export const ActivationScreen: React.FC = () => {
  const navigate = useNavigate();
  const [licenseKey, setLicenseKey] = useState("");
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [isLoadingStatus, setIsLoadingStatus] = useState(true);
  const [isActivating, setIsActivating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const fetchStatus = async () => {
    try {
      const res = await fetch("/api/license/status");
      const data: LicenseStatus = await res.json();
      setStatus(data);
      if (data.org_status === "active" && !data.blocked) {
        setSuccess("License is active!");
      }
    } catch (err) {
      console.error("Failed to load license status:", err);
    } finally {
      setIsLoadingStatus(false);
    }
  };

  useEffect(() => {
    void fetchStatus();
  }, []);

  const handleActivate = async (e: React.FormEvent) => {
    e.preventDefault();
    const cleanKey = licenseKey.trim();
    if (!cleanKey) {
      setError("Please enter or paste a license key.");
      return;
    }

    setError(null);
    setSuccess(null);
    setIsActivating(true);

    try {
      const res = await fetch("/api/license/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ license_key: cleanKey }),
      });

      const data = await res.json();

      if (!res.ok || data.success === false) {
        const msg = data.message || data.error || "License activation failed.";
        setError(msg);
        toastError(msg);
      } else {
        toastSuccess("License activated successfully!");
        setSuccess("Dashboard unlocked! Redirecting to login…");
        setStatus({
          org_status: "active",
          blocked: false,
          org_id: data.org_id,
          expires_at: data.expires_at,
        });
        setTimeout(() => {
          window.location.replace("/login");
        }, 1500);
      }
    } catch (err: any) {
      const msg = err?.message || "Failed to reach node service.";
      setError(msg);
      toastError(msg);
    } finally {
      setIsActivating(false);
    }
  };

  const isExpired = status?.org_status === "expired";
  const isNotActivated = status?.org_status === "not_activated" || !status?.org_status;
  const isActive = status?.org_status === "active" && !status?.blocked;

  const formattedExpiry = status?.expires_at
    ? new Date(status.expires_at * 1000).toLocaleDateString(undefined, {
        year: "numeric",
        month: "long",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  return (
    <SplitAuthLayout
      left={<BrandPanel bullets={BULLETS} />}
      right={
        <AuthCard>
          <div
            className="auth-fade"
            style={{
              animationDelay: "0.1s",
              marginBottom: 24,
              textAlign: "center",
            }}
          >
            <div
              style={{
                width: 52,
                height: 52,
                borderRadius: 16,
                background: isExpired ? "#fee2e2" : isActive ? "#ccfbf1" : A.tealLight,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                marginBottom: 14,
              }}
            >
              {isExpired ? (
                <ShieldAlert size={28} color="#dc2626" />
              ) : isActive ? (
                <ShieldCheck size={28} color="#0f766e" />
              ) : (
                <KeyRound size={28} color={A.primary} />
              )}
            </div>

            <h2
              style={{
                fontFamily: "var(--font-heading)",
                fontSize: 26,
                fontWeight: 800,
                color: A.primaryDarker,
                margin: "0 0 8px",
                letterSpacing: -0.6,
              }}
            >
              {isExpired
                ? "License Expired — Dashboard Locked"
                : isActive
                ? "Dashboard is Activated"
                : "Activate Attendance System"}
            </h2>
            <p style={{ fontSize: 13.5, color: A.textSub, lineHeight: 1.6, margin: 0 }}>
              {isExpired
                ? "Your organization's license has lapsed. Enter the renewed license key from QIntellect Support to unlock the dashboard."
                : isActive
                ? `Active license verified for ${status?.org_id || "this node"}.`
                : "Paste the signed license key issued by QIntellect Support to activate this on-premises installation."}
            </p>
          </div>

          {/* Status badge / banner */}
          {isLoadingStatus ? (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                padding: 16,
                gap: 8,
                color: A.textMuted,
                fontSize: 13,
              }}
            >
              <Loader2 size={16} style={{ animation: "spin-auth 0.8s linear infinite" }} />
              Verifying license status…
            </div>
          ) : isExpired ? (
            <div
              className="auth-fade"
              style={{
                background: "#fef2f2",
                border: "1px solid #fecaca",
                borderRadius: 12,
                padding: "12px 14px",
                marginBottom: 18,
                fontSize: 12.5,
                color: "#991b1b",
                display: "flex",
                alignItems: "flex-start",
                gap: 10,
              }}
            >
              <AlertTriangle size={17} style={{ flexShrink: 0, marginTop: 2 }} />
              <div>
                <strong style={{ display: "block", marginBottom: 2 }}>
                  Access Blocked
                </strong>
                {formattedExpiry
                  ? `Expired on ${formattedExpiry}. Attendance capture continues running locally, but dashboard management is locked.`
                  : "Attendance capture continues running locally, but dashboard management is locked."}
              </div>
            </div>
          ) : null}

          {/* Success Banner */}
          {success && (
            <div
              className="auth-fade"
              style={{
                background: "#f0fdfa",
                border: "1px solid #99f6e4",
                borderRadius: 12,
                padding: "12px 14px",
                fontSize: 13,
                color: A.success,
                fontWeight: 600,
                marginBottom: 18,
                display: "flex",
                alignItems: "center",
                gap: 8,
              }}
            >
              <CheckCircle2 size={18} />
              <span>{success}</span>
            </div>
          )}

          {error && <AuthError message={error} />}

          {/* If already active, offer Go to Login */}
          {isActive && !isActivating ? (
            <div className="auth-fade" style={{ animationDelay: "0.2s" }}>
              <div
                style={{
                  background: A.tealPale,
                  border: `1px solid ${A.tealMedium}`,
                  borderRadius: 14,
                  padding: 16,
                  marginBottom: 18,
                  fontSize: 13,
                  color: A.text,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                  <Building2 size={16} color={A.primary} />
                  <span>
                    Organization ID: <strong>{status?.org_id || "Bound"}</strong>
                  </span>
                </div>
                {formattedExpiry && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Calendar size={16} color={A.primary} />
                    <span>
                      Licensed until: <strong>{formattedExpiry}</strong>
                    </span>
                  </div>
                )}
              </div>

              <button
                type="button"
                className="auth-btn"
                onClick={() => navigate("/login", { replace: true })}
              >
                <span>Continue to Sign In</span>
                <ArrowRight size={16} />
              </button>
            </div>
          ) : (
            /* License Entry Form */
            <form
              onSubmit={handleActivate}
              style={{ display: "flex", flexDirection: "column", gap: 16 }}
            >
              <div className="auth-fade" style={{ animationDelay: "0.2s" }}>
                <AuthLabel>License Key Token</AuthLabel>
                <textarea
                  value={licenseKey}
                  onChange={(e) => {
                    setLicenseKey(e.target.value);
                    setError(null);
                  }}
                  required
                  placeholder="Paste your signed Ed25519 license token here (e.g. eyJhbGciOiJFZERTQSI...)"
                  rows={4}
                  style={{
                    width: "100%",
                    padding: "12px 14px",
                    borderRadius: 14,
                    outline: "none",
                    border: `1.5px solid ${A.border}`,
                    fontSize: 12.5,
                    fontFamily: "monospace",
                    color: A.text,
                    background: A.white,
                    resize: "vertical",
                    boxSizing: "border-box",
                    lineHeight: 1.4,
                  }}
                />
                <span
                  style={{
                    display: "block",
                    fontSize: 11.5,
                    color: A.textMuted,
                    marginTop: 6,
                  }}
                >
                  Issued by QIntellect Support. Works completely offline.
                </span>
              </div>

              <div className="auth-fade" style={{ animationDelay: "0.3s" }}>
                <AuthButton
                  loading={isActivating}
                  loadingLabel="Verifying License…"
                >
                  <ShieldCheck size={17} />
                  <span>{isExpired ? "Unlock Dashboard" : "Activate Dashboard"}</span>
                </AuthButton>
              </div>
            </form>
          )}
        </AuthCard>
      }
    />
  );
};

export default ActivationScreen;
