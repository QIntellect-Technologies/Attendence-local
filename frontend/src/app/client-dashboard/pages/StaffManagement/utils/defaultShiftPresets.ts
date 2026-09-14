/**
 * modules/staff/utils/defaultShiftPresets.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * The single canonical "starter roster" offered for a branch with zero
 * configured shifts. ONLY ShiftTimingsModal shows these presets — as plain
 * local draft rows (see buildDefaultShiftDraftRows there), nothing written
 * to the backend until the admin explicitly presses "Save Shift Timings".
 *
 * StaffModal's shift-assignment dropdown and ShiftAllocationTab's Apply
 * Shift dropdown intentionally do NOT fall back to this preset list: they
 * only ever render real, DB-persisted shifts (their `liveShifts` state), and
 * show a "No shifts configured" hint pointing the admin at Shift Timings
 * when that list is empty. That's deliberate — until a shift is actually
 * saved it isn't a valid assignment target, so it must not be selectable
 * from either staff modal. Once the admin saves in ShiftTimingsModal, both
 * dropdowns pick the new row up on their next fetch — StaffModal fetches
 * `liveShifts` fresh each time it's opened, and ShiftAllocationTab reloads
 * via its `onSaved` → `reloadShifts` callback when Shift Timings closes.
 * ─────────────────────────────────────────────────────────────────────────────
 */

export const DEFAULT_SHIFT_PRESETS: ReadonlyArray<{
  name: string;
  check_in_time: string;
  check_out_time: string;
}> = [
    { name: "Morning", check_in_time: "06:00", check_out_time: "14:00" },
    { name: "Evening", check_in_time: "14:00", check_out_time: "22:00" },
    { name: "Night", check_in_time: "22:00", check_out_time: "06:00" },
  ];

export interface ShiftPickerOption {
  id: string;
  name: string;
  check_in_time: string;
  check_out_time: string | null;
  isUnsaved: boolean;
}

/** Label helper shared by every dropdown that renders real shift options. */
export function describeShiftOption(option: ShiftPickerOption): string {
  const window = option.check_out_time
    ? `${option.check_in_time}–${option.check_out_time}`
    : option.check_in_time;
  return option.name ? `${option.name} · ${window}` : window;
}
