/**
 * modules/client-dashboard/api/workforceStructureApi.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Client for the workforce "departments" and "designations" collections that
 * live inside an org's onboarding_config JSON blob (NOT the Phase-1 UUID
 * `departments` table — see attendance-settings/api/departmentsApi.ts's
 * header comment for that distinction; this module and that one are
 * deliberately separate concepts).
 *
 * addWorkforceDepartment/addWorkforceDesignation are additive, single-item
 * endpoints (POST .../departments, POST .../designations) that append one
 * item to the existing blob under a server-side lock, rather than resending
 * the whole OperationalConfig the way Settings.tsx's saveOperationalConfig
 * does. Settings.tsx's full-object save remains the source of truth for
 * bulk editing (rename/delete/reorder); this module exists so a smaller
 * surface — like the Add/Edit Staff modal — can add one department or
 * designation on the fly without risking clobbering unrelated config
 * sections (cameras, network, company profile) with a stale local copy.
 *
 * Canonical GroupItem/DesignationItem shapes live here; Settings.tsx should
 * import them rather than declaring its own copies, so the two never drift.
 * Mirrors the new department/designation routes in app.py 1:1.
 */

import { fetchClientJson } from "../services/clintApi";

export type PersonFamily = "student" | "workforce";

export interface GroupItem {
  id: string;
  name: string;
  className?: string;
  sectionName?: string;
  itemKind: "class_section" | "group";
  personFamily: PersonFamily;
}

export type DesignationLevel =
  | "admin"
  | "manager"
  | "staff"
  | "teacher"
  | "student"
  | "worker"
  | "custom";

export interface DesignationItem {
  id: string;
  name: string;
  // Owning department's GroupItem.id (personFamily "workforce" only).
  departmentId?: string;
  level: DesignationLevel;
  personFamily: "workforce";
}

/** Shared with Settings.tsx's structure editors and StaffModal's quick-add
 * popovers so every caller enforces the same limit the backend does. */
export const WORKFORCE_ITEM_NAME_MAX_LENGTH = 100;

/**
 * Case-insensitive duplicate-name check, run client-side purely for instant
 * feedback. The backend (local_tenant_db.add_department) re-checks this
 * itself and is the actual guard — never rely on this alone.
 */
export function isDuplicateWorkforceName(
  existing: { name: string }[],
  name: string,
): boolean {
  const clean = name.trim().toLowerCase();
  return existing.some((item) => item.name.trim().toLowerCase() === clean);
}

interface AddDepartmentResponse {
  success: boolean;
  department: GroupItem;
  departments: GroupItem[];
}

interface AddDesignationResponse {
  success: boolean;
  designation: DesignationItem;
  designations: DesignationItem[];
}

/** Adds one workforce department to a branch. Throws with the backend's
 * message (e.g. a duplicate-name rejection) on failure. */
export async function addWorkforceDepartment(
  organizationId: number | string,
  branchId: number | string,
  name: string,
): Promise<GroupItem> {
  const body = await fetchClientJson<AddDepartmentResponse>(
    "/api/client/onboarding/departments",
    {
      method: "POST",
      body: JSON.stringify({
        organization_id: organizationId,
        branch_id: branchId,
        name,
      }),
    },
  );
  return body.department;
}

/** Adds one designation under an existing department. */
export async function addWorkforceDesignation(
  organizationId: number | string,
  branchId: number | string,
  departmentId: string,
  name: string,
): Promise<DesignationItem> {
  const body = await fetchClientJson<AddDesignationResponse>(
    "/api/client/onboarding/designations",
    {
      method: "POST",
      body: JSON.stringify({
        organization_id: organizationId,
        branch_id: branchId,
        department_id: departmentId,
        name,
      }),
    },
  );
  return body.designation;
}
