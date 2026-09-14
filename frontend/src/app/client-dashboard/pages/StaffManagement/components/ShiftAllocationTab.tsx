/**
 * modules/staff/components/ShiftAllocationTab.tsx
 * ─────────────────────────────────────────────────────────────────────────────
 * Bulk assignment of staff to the branch's configured shifts.
 * ─────────────────────────────────────────────────────────────────────────────
 */

import React, {
  type FC,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import { toastError, toastSuccess } from "../../../utils/notifications";
import { CalendarClock, TimerReset } from "lucide-react";
import {
  type OrgBranch,
  type OrgDepartment,
} from "../../../contexts/OrgConfigContext";
import { ActionButton } from "../../engine/ModuleShell";
import { T } from "../../../components/ui/theme";
import JellyButton from "../../../components/ui/JellyButton";
import ModernSelect from "../../../components/ui/ModernSelect";
import { resolveApiBranchId } from "../../../utils/tenantScope";
import { type PeopleRenderingModel } from "../../../utils/templateRendering";
import {
  assignStaffBreakShifts,
  listStaffBreakShifts,
  listBranchShifts,
  type ShiftRecord,
} from "../api/attendanceSettingsApi";
import { type StaffMember } from "../types/staffTypes";
import { shiftText } from "../utils/staffShifts";
import { formatShiftWindow } from "../utils/shiftOverlap";
import { ShiftTimingsModal } from "./ShiftTimingsModal";

export const ShiftAllocationTab: FC<{
  staffRows: StaffMember[];
  // Full branch objects (this always receives cfg.branches at runtime) —
  // typed loosely here only for the fields this component itself reads;
  // backendBranchId/backend_branch_id ride along so branch-scoped shift
  // endpoints can resolve the real UUID instead of the UI ordinal id.
  visibleBranches: OrgBranch[];
  departmentsByBranch: Record<number, OrgDepartment[]>;
  organizationId: number | string | null;
  branchName: (id: number) => string;
  isGlobalDashboard: boolean;
  effectiveBranchId?: number;
  peopleModel: PeopleRenderingModel;
  onApplyShift: (target: {
    scope: "branch" | "department" | "individual";
    branchId: number;
    department?: string;
    staffId?: string;
    shiftId: string;
  }) => Promise<void>;
}> = ({
  staffRows,
  visibleBranches,
  departmentsByBranch,
  organizationId,
  branchName,
  isGlobalDashboard,
  effectiveBranchId,
  peopleModel,
  onApplyShift,
}) => {
    const defaultBranchId =
      effectiveBranchId ?? visibleBranches[0]?.id ?? staffRows[0]?.branchId ?? 0;

    const [scope, setScope] = useState<"branch" | "department" | "individual">(
      isGlobalDashboard ? "branch" : "department",
    );
    const [selectedBranchId, setSelectedBranchId] = useState(defaultBranchId);
    const [selectedDepartment, setSelectedDepartment] = useState<string>("all");
    const [selectedStaffId, setSelectedStaffId] = useState<string>("");
    const [selectedShiftId, setSelectedShiftId] = useState<string>("");
    const [isApplying, setIsApplying] = useState(false);
    const [applyError, setApplyError] = useState<string | null>(null);

    const scopedBranchId = isGlobalDashboard
      ? selectedBranchId
      : (effectiveBranchId ?? selectedBranchId);

    // The real Supabase branch UUID for scopedBranchId — every shift endpoint
    // (list/create/update/delete) requires this, not the UI ordinal id. See
    // resolveApiBranchId's own comment for why this translation is mandatory.
    const scopedApiBranchId = useMemo(
      () => resolveApiBranchId(organizationId, scopedBranchId, visibleBranches),
      [organizationId, scopedBranchId, visibleBranches],
    );

    // Real, per-branch shifts (support_db_shifts.py's `shifts` table) — the
    // sole owner of check-in/check-out time in this codebase. Replaces the
    // old org-wide, 4-row-max `ShiftDefinition` list, which could never
    // represent more than one "Custom" shift for the whole organization.
    const [liveShifts, setLiveShifts] = useState<ShiftRecord[]>([]);
    const [breakShifts, setBreakShifts] = useState<ShiftRecord[]>([]);
    const [assignedBreakShiftIds, setAssignedBreakShiftIds] = useState<string[]>(
      [],
    );
    const [isSavingBreakShifts, setIsSavingBreakShifts] = useState(false);
    const [isLoadingShifts, setIsLoadingShifts] = useState(false);
    const [shiftsError, setShiftsError] = useState<string | null>(null);
    const [isShiftTimingsOpen, setIsShiftTimingsOpen] = useState(false);

    const scopedBranchTimezone = useMemo(() => {
      try {
        const branch = visibleBranches.find(
          (b) => String(b.id) === String(scopedBranchId),
        );
        return (
          branch?.timezone ||
          Intl.DateTimeFormat().resolvedOptions().timeZone ||
          "UTC"
        );
      } catch {
        return "UTC";
      }
    }, [visibleBranches, scopedBranchId]);

    const reloadShifts = useCallback(() => {
      if (!organizationId || !scopedApiBranchId) {
        setLiveShifts([]);
        setShiftsError(
          scopedBranchId && organizationId
            ? "This branch isn't fully synced yet — its backend id couldn't be resolved."
            : null,
        );
        return;
      }
      setIsLoadingShifts(true);
      setShiftsError(null);
      Promise.all([
        listBranchShifts(
          scopedApiBranchId,
          organizationId,
          peopleModel.peopleType,
          "main",
        ),
        listBranchShifts(
          scopedApiBranchId,
          organizationId,
          peopleModel.peopleType,
          "break",
        ),
      ])
        .then(([mainRows, breakRows]) => {
          setLiveShifts(mainRows);
          setBreakShifts(breakRows);
        })
        .catch((error) => {
          setLiveShifts([]);
          setBreakShifts([]);
          setShiftsError(
            error instanceof Error ? error.message : "Failed to load shifts.",
          );
        })
        .finally(() => setIsLoadingShifts(false));
    }, [
      organizationId,
      scopedApiBranchId,
      scopedBranchId,
      peopleModel.peopleType,
    ]);

    useEffect(() => {
      if (!organizationId || !selectedStaffId) {
        setAssignedBreakShiftIds([]);
        return;
      }
      listStaffBreakShifts(selectedStaffId, organizationId)
        .then((rows) => setAssignedBreakShiftIds(rows.map((row) => row.id)))
        .catch(() => setAssignedBreakShiftIds([]));
    }, [organizationId, selectedStaffId]);

    useEffect(() => {
      reloadShifts();
    }, [reloadShifts]);

    // Only real, DB-persisted shifts. Presets are NOT injected here — the
    // admin must save shifts in ShiftTimingsModal before they appear anywhere.
    const shiftOptions = useMemo(
      () =>
        liveShifts.map((shift) => ({
          id: shift.id,
          value: shift.id,
          name: shift.name ?? "",
          check_in_time: shift.check_in_time,
          check_out_time: shift.check_out_time ?? null,
          label: `${shift.name ?? ""} · ${shift.check_in_time}${
            shift.check_out_time ? `–${shift.check_out_time}` : ""
          }`,
        })),
      [liveShifts],
    );

    // Keep the selection valid as the branch (and therefore the available
    // shift list) changes — default to the first real shift, or clear if none.
    useEffect(() => {
      setSelectedShiftId((current) => {
        if (shiftOptions.some((option) => option.id === current)) return current;
        return shiftOptions[0]?.id ?? "";
      });
    }, [shiftOptions]);

    const branchStaff = useMemo(
      () => staffRows.filter((member) => member.branchId === scopedBranchId),
      [scopedBranchId, staffRows],
    );

    const departmentOptions = useMemo(() => {
      // Departments are organization master-data, so the shift-allocation dropdown
      // must read from cfg.departments instead of deriving options from staff rows.
      // This keeps configured departments visible even before employees are assigned.
      const configuredDepartments = (departmentsByBranch[scopedBranchId] ?? [])
        .map((department) => department.name)
        .filter((name): name is string => Boolean(name?.trim()));

      if (configuredDepartments.length > 0) {
        return [...new Set(configuredDepartments)].sort((a, b) =>
          a.localeCompare(b),
        );
      }

      // Legacy fallback for older localStorage data that may not have department config.
      return Array.from(
        new Set(
          branchStaff
            .map((member) => String(member.department ?? ""))
            .filter((department) => Boolean(department.trim())),
        ),
      ).sort((a, b) => a.localeCompare(b));
    }, [branchStaff, departmentsByBranch, scopedBranchId]);

    const individualOptions = useMemo(() => {
      const list =
        selectedDepartment === "all"
          ? branchStaff
          : branchStaff.filter(
            (member) => member.department === selectedDepartment,
          );
      return [...list].sort((a, b) => a.name.localeCompare(b.name));
    }, [branchStaff, selectedDepartment]);

    function timeToMinutes(value: string | null | undefined): number | null {
      const match = String(value ?? "").match(/^(\d{1,2}):(\d{2})/);
      if (!match) return null;
      const hours = Number(match[1]);
      const minutes = Number(match[2]);
      if (hours > 23 || minutes > 59) return null;
      return hours * 60 + minutes;
    }
    function shiftDuration(
      start: string | null | undefined,
      end: string | null | undefined,
    ): number | null {
      const startMinutes = timeToMinutes(start);
      const endMinutes = timeToMinutes(end);
      if (
        startMinutes === null ||
        endMinutes === null ||
        startMinutes === endMinutes
      ) {
        return null;
      }
      return (endMinutes - startMinutes + 1440) % 1440 || 1440;
    }
    function isBreakWithinMainShift(
      mainStart: string | null | undefined,
      mainEnd: string | null | undefined,
      breakStart: string | null | undefined,
      breakEnd: string | null | undefined,
    ): boolean {
      const mainStartMinutes = timeToMinutes(mainStart);
      const breakStartMinutes = timeToMinutes(breakStart);
      const mainDuration = shiftDuration(mainStart, mainEnd);
      const breakDuration = shiftDuration(breakStart, breakEnd);
      if (
        mainStartMinutes === null ||
        breakStartMinutes === null ||
        mainDuration === null ||
        breakDuration === null ||
        breakDuration > mainDuration
      ) {
        return false;
      }
      const breakOffset = (breakStartMinutes - mainStartMinutes + 1440) % 1440;
      return breakOffset + breakDuration <= mainDuration;
    }
    const selectedShift = liveShifts.find(
      (shift) => shift.id === selectedShiftId,
    );
    const selectedStaff = branchStaff.find(
      (member) => String(member.id) === String(selectedStaffId),
    );
    const eligibleBreakShifts = useMemo(() => {
      if (
        !selectedStaff?.shiftIdRef ||
        !selectedStaff.shiftStart ||
        !selectedStaff.shiftEnd
      ) {
        return [];
      }
      return breakShifts.filter((shift) =>
        isBreakWithinMainShift(
          selectedStaff.shiftStart,
          selectedStaff.shiftEnd,
          shift.check_in_time,
          shift.check_out_time,
        ),
      );
    }, [breakShifts, selectedStaff]);

    useEffect(() => {
      setAssignedBreakShiftIds((current) =>
        current.filter((id) =>
          eligibleBreakShifts.some((shift) => shift.id === id),
        ),
      );
    }, [eligibleBreakShifts]);

    const targetCount = useMemo(() => {
      if (scope === "branch") return branchStaff.length;
      if (scope === "department") {
        if (selectedDepartment === "all") return 0;
        return branchStaff.filter(
          (member) => member.department === selectedDepartment,
        ).length;
      }
      return selectedStaffId ? 1 : 0;
    }, [branchStaff, scope, selectedDepartment, selectedStaffId]);

    const inputStyle: React.CSSProperties = {
      width: "100%",
      height: 38,
      border: `1px solid ${T.border}`,
      borderRadius: 10,
      background: T.card,
      color: T.head,
      padding: "0 12px",
      fontSize: 12,
      fontWeight: 700,
      fontFamily: "inherit",
      outline: "none",
    };

    const labelStyle: React.CSSProperties = {
      display: "block",
      marginBottom: 6,
      color: T.muted,
      fontSize: 10,
      fontWeight: 800,
      letterSpacing: ".07em",
      textTransform: "uppercase",
    };

    const canApply =
      scopedBranchId > 0 &&
      !!selectedShiftId &&
      !isApplying &&
      (scope === "branch" ||
        (scope === "department" && selectedDepartment !== "all") ||
        (scope === "individual" && selectedStaffId));

    // Who among the targets already holds a DIFFERENT shift. A person holds
    // exactly one shift (client_staff.shift_id_ref), so applying a second one
    // replaces the first — it does not add to it. That replacement used to be
    // silent: both applies reported success and nothing said which shift the
    // person ended up on, which is how one employee appeared to hold both a
    // Morning and an Evening shift at once (Ticket #20).
    const replacements = useMemo(() => {
      const targets =
        scope === "individual"
          ? branchStaff.filter((member) => member.id === selectedStaffId)
          : scope === "department"
            ? branchStaff.filter(
              (member) =>
                selectedDepartment !== "all" &&
                member.department === selectedDepartment,
            )
            : branchStaff;

      return targets.filter((member) => {
        const current = String((member as any).shiftIdRef ?? "");
        return current && current !== selectedShiftId;
      });
    }, [
      branchStaff,
      scope,
      selectedDepartment,
      selectedStaffId,
      selectedShiftId,
    ]);

    const handleApply = async () => {
      if (!canApply) return;
      if (!organizationId) {
        setApplyError("Organization context is not available yet. Please try again in a moment.");
        return;
      }

      // One confirm for the whole apply, not one per person — a branch-wide
      // apply can target hundreds.
      if (replacements.length > 0 && selectedShift) {
        const incoming = `${selectedShift.name} (${formatShiftWindow(selectedShift)})`;
        const summary =
          replacements.length === 1
            ? `${replacements[0].name} is currently on ${shiftText(replacements[0])}.`
            : `${replacements.length} people are currently on a different shift.`;

        if (
          !window.confirm(
            `${summary}\n\nApplying ${incoming} REPLACES their current shift — nobody holds two shifts at once. Continue?`,
          )
        ) {
          return;
        }
      }

      setIsApplying(true);
      setApplyError(null);
      try {
        // selectedShiftId is always a real backend shift id (only DB-saved
        // shifts appear in the dropdown) so no preset resolution is needed.
        await onApplyShift({
          scope,
          branchId: scopedBranchId,
          department:
            selectedDepartment === "all" ? undefined : selectedDepartment,
          staffId: selectedStaffId || undefined,
          shiftId: selectedShiftId,
        });
        toastSuccess(
          selectedShift
            ? `${selectedShift.name ?? ""} (${selectedShift.check_in_time}${selectedShift.check_out_time ? `–${selectedShift.check_out_time}` : ""}) applied to ${targetCount} ${
                targetCount === 1
                  ? peopleModel.personSingular.toLowerCase()
                  : peopleModel.personPlural.toLowerCase()
              }.`
            : "Shift applied successfully.",
        );
        setApplyError(null);
      } catch (error) {
        setApplyError(
          error instanceof Error ? error.message : "Failed to apply shift.",
        );
        toastError(
          error instanceof Error ? error.message : "Failed to apply shift.",
        );
      } finally {
        setIsApplying(false);
      }
    };

    const handleSaveBreakShifts = async () => {
      if (!organizationId || !selectedStaffId) return;
      setIsSavingBreakShifts(true);
      try {
        const rows = await assignStaffBreakShifts(
          selectedStaffId,
          assignedBreakShiftIds,
          organizationId,
        );
        setAssignedBreakShiftIds(rows.map((row) => row.id));
        toastSuccess("Break / Namaz shifts saved.");
      } catch (error) {
        toastError(
          error instanceof Error ? error.message : "Failed to save break shifts.",
        );
      } finally {
        setIsSavingBreakShifts(false);
      }
    };

    return (
      <div style={{ display: "grid", gap: 16 }}>
        <div
          style={{
            background: T.card,
            border: `1px solid ${T.border}`,
            borderRadius: 16,
            padding: 18,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 16,
            boxShadow:
              "0 1px 3px rgba(15,45,74,0.06),0 1px 2px rgba(15,45,74,0.04)",
          }}
        >
          <div>
            <div style={{ fontSize: 15, fontWeight: 900, color: T.head }}>
              Shift Allocation
            </div>
            <div style={{ fontSize: 12, color: T.muted, marginTop: 3 }}>
              {isGlobalDashboard
                ? "Admin can allocate shifts branch-wise, department-wise, or individually."
                : `Branch dashboard is locked to ${branchName(scopedBranchId)} and can allocate by department or individual.`}
            </div>
          </div>
          <ActionButton
            label="Shift Timings"
            Icon={TimerReset}
            onClick={() => setIsShiftTimingsOpen(true)}
            variant="ghost"
            disabled={!scopedApiBranchId}
          />
        </div>
        {!scopedApiBranchId && scopedBranchId > 0 && (
          <div
            style={{
              fontSize: 12,
              color: "#e11d48",
              marginTop: -8,
            }}
          >
            {branchName(scopedBranchId)}'s backend id couldn't be resolved, so
            shifts can't be loaded or edited for it right now. Try reselecting the
            branch, or refresh the dashboard.
          </div>
        )}

        <div
          style={{
            background: T.card,
            border: `1px solid ${T.border}`,
            borderRadius: 16,
            padding: 16,
            boxShadow:
              "0 1px 3px rgba(15,45,74,0.06),0 1px 2px rgba(15,45,74,0.04)",
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(5, minmax(150px, 1fr)) auto",
              gap: 12,
              alignItems: "end",
            }}
          >
            <div>
              <label style={labelStyle}>Allocation Level</label>
              <ModernSelect
                value={scope}
                onChange={(value) => {
                  setScope(value as "branch" | "department" | "individual");
                  setSelectedDepartment("all");
                  setSelectedStaffId("");
                }}
                options={[
                  ...(isGlobalDashboard
                    ? [{ value: "branch", label: "Branch Wise" }]
                    : []),
                  {
                    value: "department",
                    label: `${peopleModel.groupLabel} Wise`,
                  },
                  { value: "individual", label: "Individual" },
                ]}
                ariaLabel="Select allocation level"
                width="100%"
              />
            </div>

            <div>
              <label style={labelStyle}>Branch</label>
              {isGlobalDashboard ? (
                <ModernSelect
                  value={String(selectedBranchId)}
                  onChange={(value) => {
                    setSelectedBranchId(Number(value));
                    setSelectedDepartment("all");
                    setSelectedStaffId("");
                  }}
                  options={visibleBranches.map((branch) => ({
                    value: String(branch.id),
                    label: branch.name,
                  }))}
                  ariaLabel="Select branch"
                  width="100%"
                />
              ) : (
                <div
                  style={{
                    ...inputStyle,
                    display: "flex",
                    alignItems: "center",
                    background: T.teal50,
                    color: T.teal600,
                  }}
                >
                  {branchName(scopedBranchId)}
                </div>
              )}
            </div>

            <div>
              <label style={labelStyle}>{peopleModel.groupLabel}</label>
              <ModernSelect
                value={selectedDepartment}
                disabled={scope === "branch"}
                onChange={(value) => {
                  setSelectedDepartment(value);
                  setSelectedStaffId("");
                }}
                options={[
                  {
                    value: "all",
                    label:
                      scope === "department"
                        ? `Select ${peopleModel.groupLabel}`
                        : peopleModel.groupFilterAllLabel,
                  },
                  ...departmentOptions.map((department) => ({
                    value: department,
                    label: department,
                  })),
                ]}
                ariaLabel={`Select ${peopleModel.groupLabel.toLowerCase()}`}
                width="100%"
              />
            </div>

            <div>
              <label style={labelStyle}>Employee</label>
              <ModernSelect
                value={selectedStaffId}
                disabled={scope !== "individual"}
                onChange={(value) => setSelectedStaffId(value)}
                options={[
                  { value: "", label: `Select ${peopleModel.personSingular}` },
                  ...individualOptions.map((member) => ({
                    value: member.id,
                    label: `${member.name} · ${member.department}`,
                  })),
                ]}
                ariaLabel={`Select ${peopleModel.personSingular.toLowerCase()}`}
                width="100%"
              />
            </div>

            <div>
              <label style={labelStyle}>Shift</label>
              <ModernSelect
                value={selectedShiftId}
                disabled={isLoadingShifts || shiftOptions.length === 0}
                onChange={(value) => setSelectedShiftId(value)}
                options={
                  shiftOptions.length > 0
                    ? shiftOptions.map((option) => ({
                        value: option.value,
                        label: option.label,
                      }))
                    : [
                        {
                          value: "",
                          label: isLoadingShifts
                            ? "Loading shifts…"
                            : "No shifts configured — use Shift Timings",
                        },
                      ]
                }
                ariaLabel="Select shift"
                width="100%"
              />
            </div>

            <JellyButton
              type="button"
              variant="primary"
              disabled={!canApply}
              onClick={() => void handleApply()}
            >
              {isApplying ? "Applying…" : "Apply Shift"}
            </JellyButton>
          </div>


          {shiftsError && (
            <div style={{ marginTop: 10, fontSize: 12, color: "#e11d48" }}>
              {shiftsError}
            </div>
          )}

          {applyError && (
            <div style={{ marginTop: 10, fontSize: 12, color: "#e11d48" }}>
              {applyError}
            </div>
          )}

          <div
            style={{
              marginTop: 12,
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 12,
              color: T.muted,
            }}
          >
            <CalendarClock size={14} color={T.teal600} />
            {selectedShift ? (
              <>
                Selected:{" "}
                <strong style={{ color: T.head }}>{selectedShift.name}</strong>
                <span>·</span>
                <span>
                  {selectedShift.check_in_time}
                  {selectedShift.check_out_time
                    ? `–${selectedShift.check_out_time}`
                    : ""}
                </span>
                <span>·</span>
              </>
            ) : (
              <span>No shift selected ·</span>
            )}
            <span>{targetCount} staff will be affected</span>
          </div>
        </div>

        <div
          style={{
            background: T.card,
            border: `1px solid ${T.border}`,
            borderRadius: 16,
            padding: 16,
            boxShadow: "0 1px 3px rgba(15,45,74,0.06)",
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 800, color: T.head }}>
            Break / Namaz Shifts
          </div>
          <div style={{ fontSize: 12, color: T.muted, marginTop: 4 }}>
            Select one employee to assign multiple break periods. This does not
            replace the main attendance shift.
          </div>
          {!selectedStaffId ? (
            <div style={{ marginTop: 12, fontSize: 12, color: T.muted }}>
              Choose <strong>Individual</strong> allocation and an employee above.
            </div>
          ) : !selectedStaff?.shiftIdRef ||
            !selectedStaff?.shiftStart ||
            !selectedStaff.shiftEnd ? (
            <div style={{ marginTop: 12, fontSize: 12, color: T.muted }}>
              Assign a <strong>main attendance shift</strong> to this employee
              before assigning Break / Namaz shifts.
            </div>
          ) : eligibleBreakShifts.length === 0 ? (
            <div style={{ marginTop: 12, fontSize: 12, color: T.muted }}>
              No Break / Namaz shifts fall inside this employee&apos;s main shift
              ({selectedStaff.shiftStart}–{selectedStaff.shiftEnd}).
            </div>
          ) : (
            <div style={{ marginTop: 12, display: "grid", gap: 8 }}>
              {eligibleBreakShifts.map((shift) => {
                const checked = assignedBreakShiftIds.includes(shift.id);
                return (
                  <label
                    key={shift.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 9,
                      padding: "9px 10px",
                      border: `1px solid ${checked ? T.teal600 : T.border}`,
                      borderRadius: 9,
                      background: checked ? T.teal50 : T.card,
                      cursor: "pointer",
                      fontSize: 12,
                      color: T.head,
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() =>
                        setAssignedBreakShiftIds((current) =>
                          checked
                            ? current.filter((id) => id !== shift.id)
                            : [...current, shift.id],
                        )
                      }
                    />
                    <strong>{shift.name}</strong>
                    <span style={{ color: T.muted }}>
                      {shift.check_in_time}
                      {shift.check_out_time ? `–${shift.check_out_time}` : ""}
                    </span>
                  </label>
                );
              })}
              <JellyButton
                type="button"
                variant="primary"
                disabled={isSavingBreakShifts}
                onClick={() => void handleSaveBreakShifts()}
              >
                {isSavingBreakShifts ? "Saving…" : "Save Break Assignments"}
              </JellyButton>
            </div>
          )}
        </div>

        <div
          style={{
            background: T.card,
            border: `1px solid ${T.border}`,
            borderRadius: 16,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              padding: "14px 18px",
              borderBottom: `1px solid ${T.border}`,
              fontSize: 13,
              fontWeight: 800,
              color: T.head,
            }}
          >
            Current Shift Summary
          </div>
          {staffRows.slice(0, 12).map((member) => (
            <div
              key={member.id}
              style={{
                display: "grid",
                gridTemplateColumns: "1.5fr 1fr 1fr 1fr",
                gap: 12,
                padding: "11px 18px",
                borderBottom: `1px solid ${T.teal50}`,
                alignItems: "center",
                fontSize: 12,
              }}
            >
              <strong style={{ color: T.head }}>{member.name}</strong>
              <span style={{ color: T.muted }}>
                {branchName(member.branchId)}
              </span>
              <span style={{ color: T.muted }}>{member.department}</span>
              <span style={{ color: T.teal600, fontWeight: 800 }}>
                {shiftText(member)}
              </span>
            </div>
          ))}
        </div>

        {isShiftTimingsOpen && organizationId && scopedApiBranchId && (
          <ShiftTimingsModal
            branchId={scopedApiBranchId}
            organizationId={organizationId}
            peopleType={peopleModel.peopleType}
            branchTimezone={scopedBranchTimezone}
            onClose={() => setIsShiftTimingsOpen(false)}
            onSaved={reloadShifts}
          />
        )}
      </div>
    );
  };