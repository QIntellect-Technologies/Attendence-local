import React, { useEffect, useState } from "react";
import { Building2, Loader2, UserCircle2 } from "lucide-react";
import { useAuth } from "../../contexts/useAuth";
import { ChangePasswordCard } from "../../components/ui/ChangePasswordCard";
import { loadClientBootstrap } from "../../services/clintApi";
import {
  C,
  ConfigCard,
  ReadOnlyLine,
  companyProfileFromBootstrap,
  type BootstrapResponse,
  type BootstrapOrganization,
  type Branch,
} from "../Settings/Settings";

/**
 * AccountSettings
 * ─────────────────────────────────────────────────────────────────────────
 * "My Account" — personal, self-service account settings for the currently
 * logged-in user. Deliberately separate from Settings.tsx ("Dashboard
 * Setup"), which is org-level configuration gated behind the "settings"
 * module grant.
 *
 * That gate is the whole reason this page exists: a staff/manager account
 * without the settings module grant can still change their own password —
 * that's a "who am I" action, not a "configure the organization" one, and
 * it shouldn't require a permission meant for the latter. Every
 * authenticated dashboard user (admin, HR, staff, manager — any account
 * type this dashboard supports) reaches this page the same way, via the
 * "My Account" button in AdminLayout.tsx's header, which is never
 * conditionally hidden the way the Dashboard Setup gear icon is.
 *
 * The Organization Profile card below is a deliberate exception to
 * "personal only": every user benefits from seeing which org/branch context
 * they're signed into, so it's surfaced here read-only for everyone. It is
 * NOT editable from this page — Settings.tsx's single combined save request
 * posts departments/roles/cameras/network/company_profile together in one
 * payload, and this page has no reason to carry that whole object around
 * just to edit four fields. Anyone who needs to change the organization's
 * address/city/phone/timezone still does so from Dashboard Setup.
 *
 * Add future self-only account settings here (e.g. a name/email/phone
 * editor) — never in Settings.tsx, to keep that module-gated screen
 * strictly organization-level.
 */
export default function AccountSettings() {
  const { user } = useAuth();
  // Same `|| ""` widening pattern AdminLayout.tsx already uses for these
  // pass-through (index-signature) fields — kept consistent rather than
  // introducing a different cast here.
  const displayName = (user?.name as string) || (user?.email as string) || "";
  const organizationId = user?.organizationId ?? user?.organization_id;

  const [organization, setOrganization] = useState<
    BootstrapOrganization | undefined
  >(undefined);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [companyProfile, setCompanyProfile] = useState(
    companyProfileFromBootstrap({}),
  );
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    if (!organizationId) {
      setIsLoading(false);
      setError("Organization is missing from the logged-in user.");
      return;
    }

    (async () => {
      try {
        setIsLoading(true);
        setError(null);
        const data = await loadClientBootstrap<BootstrapResponse>(
          organizationId,
        );
        if (cancelled) return;
        setOrganization(data.organization);
        setBranches(data.branches || []);
        setCompanyProfile(companyProfileFromBootstrap(data));
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof Error
            ? err.message
            : "Failed to load organization profile.",
        );
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [organizationId]);

  const maxStaffCapacity =
    organization?.max_capacity ??
    organization?.maxCapacity ??
    organization?.max_users ??
    organization?.maxUsers ??
    (organization?.vertical_config as Record<string, unknown> | undefined)
      ?.max_users ??
    (organization?.vertical_config as Record<string, unknown> | undefined)
      ?.max_capacity;

  return (
    <div
      style={{
        minHeight: "100%",
        background: C.bg,
        padding: 28,
        fontFamily: "'DM Sans','Inter','Segoe UI',sans-serif",
      }}
    >
      <div style={{ maxWidth: 720, margin: "0 auto" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 22,
          }}
        >
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: 12,
              background: C.tealPale,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <UserCircle2 size={24} color={C.primary} />
          </div>
          <div>
            <h1
              style={{
                margin: 0,
                fontSize: 24,
                color: C.primary,
                fontWeight: 950,
                letterSpacing: "-.03em",
              }}
            >
              My Account
            </h1>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: C.textSub }}>
              {displayName
                ? `Signed in as ${displayName}`
                : "Your personal account settings."}
            </p>
          </div>
        </div>

        <div style={{ display: "grid", gap: 18 }}>
          {error ? (
            <div
              style={{
                padding: 14,
                borderRadius: 10,
                background: "#fef2f2",
                border: "1px solid #fecaca",
                color: C.danger,
                fontSize: 13,
                fontWeight: 800,
              }}
            >
              {error}
            </div>
          ) : (
            <ConfigCard
              icon={<Building2 size={18} />}
              title="Organization Profile"
            >
              {isLoading ? (
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "8px 0",
                    color: C.textSub,
                    fontWeight: 700,
                    fontSize: 13,
                  }}
                >
                  <Loader2
                    size={16}
                    style={{ animation: "spin .8s linear infinite" }}
                  />
                  Loading organization profile…
                  <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
                </div>
              ) : (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: 14,
                  }}
                >
                  <ReadOnlyLine
                    label="Organization"
                    value={organization?.name || "—"}
                  />
                  <ReadOnlyLine
                    label="Contact Email"
                    value={
                      organization?.contact_email ||
                      organization?.contactEmail ||
                      "—"
                    }
                  />
                  <ReadOnlyLine
                    label="Business Type"
                    value={
                      organization?.business_type ||
                      organization?.biz_type ||
                      organization?.org_type ||
                      "—"
                    }
                  />
                  <ReadOnlyLine
                    label="Primary People Type"
                    value={
                      organization?.primary_people_type ||
                      organization?.primaryPeopleType ||
                      "—"
                    }
                  />
                  <ReadOnlyLine
                    label="Max Staff Capacity"
                    value={
                      maxStaffCapacity != null
                        ? `${maxStaffCapacity} people`
                        : "Unlimited"
                    }
                  />
                  <ReadOnlyLine
                    label="Status"
                    value={organization?.status || "—"}
                  />
                  <ReadOnlyLine
                    label="Attendance Mode"
                    value={(
                      organization?.attendance_mode ||
                      organization?.attendanceMode ||
                      "—"
                    ).toUpperCase()}
                  />
                  <ReadOnlyLine
                    label="Support-created branches"
                    value={String(branches.length)}
                  />
                  {/* <ReadOnlyLine
                    label="Address"
                    value={companyProfile.address || "—"}
                  />
                  <ReadOnlyLine
                    label="City"
                    value={companyProfile.city || "—"}
                  /> */}
                  <ReadOnlyLine
                    label="Public Contact Phone"
                    value={companyProfile.publicContactPhone || "—"}
                  />
                  <ReadOnlyLine
                    label="Timezone"
                    value={companyProfile.timezone || "—"}
                  />
                </div>
              )}
            </ConfigCard>
          )}

          <ChangePasswordCard />
        </div>
      </div>
    </div>
  );
}