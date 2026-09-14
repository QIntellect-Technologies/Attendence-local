import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Calendar,
  Camera,
  CheckCircle2,
  Clock,
  GitBranch,
  GraduationCap,
  KeyRound,
  Loader2,
  Network,
  Plus,
  Save,
  ShieldCheck,
  Trash2,
  UploadCloud,
  Users,
} from "lucide-react";
import { useAuth } from "../../contexts/useAuth";
import { useOrg } from "../../contexts/OrgConfigContext";
import { fetchClientJson, loadClientBootstrap } from "../../services/clintApi";
import { AttendanceSettingsScreens } from "../attendance_temp/settings/AttendanceSettingsScreens";
import ImportPackageButton from "../LiveAttendance/ImportPackageButton";
import {
  validateRtspUrl,
  findInvalidCameraRtspUrl,
} from "../../utils/rtspValidation";
// ─── Access gate helpers ────────────────────────────────────────────────────
// Mirrors AdminLayout.tsx's isStaffUser/getUserAllowedModules exactly (kept
// as small local copies rather than a shared import, since AdminLayout.tsx
// doesn't currently export them). Admins always reach Settings; staff only
// when their session carries the "settings" module grant — the same grant
// toggled in StaffModal's Dashboard Module Access checklist and exempted
// from the purchased-module gate in moduleRegistry.ts.
function isStaffAccount(user: { role?: string } | null | undefined): boolean {
  return String(user?.role ?? "").toLowerCase() === "staff";
}

function toStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.map(String).filter(Boolean);
    } catch {
      return value
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
  }
  return [];
}

function getAccountAllowedModules(
  user:
    | {
      allowedModules?: string[] | string;
      accessModules?: string[] | string;
      moduleAccess?: string[] | string;
      access_modules?: string[] | string;
    }
    | null
    | undefined,
): string[] {
  return Array.from(
    new Set(
      toStringArray(
        user?.allowedModules ??
        user?.accessModules ??
        user?.moduleAccess ??
        user?.access_modules,
      ).map((s) => s.trim()),
    ),
  );
}

export type Branch = {
  id: string;
  name: string;
  location?: string | null;
  max_staff_capacity?: number | null;
  maxStaffCapacity?: number | null;
};

export type BootstrapOrganization = {
  id: string;
  name: string;
  contact_phone?: string | null;
  contact_email?: string | null;
  contactEmail?: string | null;
  org_type?: string | null;
  business_type?: string | null;
  biz_type?: string | null;
  primary_people_type?: string | null;
  primaryPeopleType?: string | null;
  max_capacity?: number | null;
  maxCapacity?: number | null;
  max_users?: number | null;
  maxUsers?: number | null;
  enabled_people_types?: string[];
  enabledPeopleTypes?: string[];
  attendance_people_types?: string[];
  attendancePeopleTypes?: string[];
  vertical_config?: Record<string, unknown> | null;
  verticalConfig?: Record<string, unknown> | null;
  terminology_overrides?: Record<string, unknown> | null;
  terminologyOverrides?: Record<string, unknown> | null;
  attendance_mode?: string;
  attendanceMode?: string;
  status?: string;
};

export type BootstrapResponse = {
  success?: boolean;
  message?: string;
  organization?: BootstrapOrganization;
  branches?: Branch[];
  active_modules?: string[];
  activeModules?: string[];
  config?: Partial<OperationalConfig> & Record<string, unknown>;
  onboarding_config?: Partial<OperationalConfig> & Record<string, unknown>;
  onboardingConfig?: Partial<OperationalConfig> & Record<string, unknown>;
};

type AuthUser = {
  id?: string | number;
  organization_id?: string | null;
  organizationId?: string | null;
  role?: string;
  allowedModules?: string[] | string;
  accessModules?: string[] | string;
  moduleAccess?: string[] | string;
  access_modules?: string[] | string;
};

type AuthContext = {
  user?: AuthUser | null;
  refreshUser?: (userId: string | number) => Promise<unknown> | unknown;
};

type PersonFamily = "student" | "workforce";

export type CompanyProfile = {
  address: string;
  city: string;
  publicContactPhone: string;
  timezone: string;
  logoDataUrl: string;
  logoFileName: string;
};

type GroupItem = {
  id: string;
  name: string;
  className?: string;
  sectionName?: string;
  itemKind: "class_section" | "group";
  personFamily: PersonFamily;
};

type DesignationItem = {
  id: string;
  name: string;
  // Owning GroupItem.id (personFamily "workforce" only). Same field/shape
  // as workforceStructureApi.ts's DesignationItem and OrgConfigContext's
  // OrgRole.departmentId, kept independently here since this file already
  // maintains its own local copies of these shapes — see
  // normalizeDesignationItem and backfillDesignationDepartments below for
  // how this gets populated and repaired on load.
  departmentId?: string;
  level:
  | "admin"
  | "manager"
  | "staff"
  | "teacher"
  | "student"
  | "worker"
  | "custom";
  personFamily: "workforce";
};

type CameraItem = {
  id: string;
  name: string;
  location: string;
  rtspUrl: string;
  channel: string;
  type: "nvr" | "dvr" | "ip_camera" | "webcam";
};

type NetworkConfig = {
  publicIp: string;
  nvrDvrIp: string;
  rtspUsername: string;
  rtspPassword: string;
  rtspPort: string;
};

type OperationalConfig = {
  company_profile: CompanyProfile;
  departments: Record<string, GroupItem[]>;
  roles: Record<string, DesignationItem[]>;
  cameras: Record<string, CameraItem[]>;
  network: NetworkConfig;
  shiftEnabledPeopleTypes?: string[];
  /** Per-branch auto-delete window (days) for archived staff — see
   * ArchivedStaffRetentionEditor and local_db.get_archived_staff_retention_days
   * on the local node backend. Keyed by branch id, same convention as
   * departments/roles/cameras above. */
  archived_staff_retention_days: Record<string, number>;
};

type Terminology = {
  organizationLabel: string;
  branchLabel: string;
  studentGroupLabel: string;
  studentGroupPlural: string;
  studentSubgroupLabel: string;
  workforceGroupLabel: string;
  workforceGroupPlural: string;
  designationLabel: string;
  designationPlural: string;
  cameraLabel: string;
  cameraPlural: string;
  studentPlural: string;
  workforcePlural: string;
};

type OperationalPlan = {
  hasStudentSetup: boolean;
  hasWorkforceSetup: boolean;
  isAcademic: boolean;
  isFactory: boolean;
};

export const C = {
  primary: "#1a699f",
  primaryDark: "#155580",
  bg: "#eef8fc",
  card: "#ffffff",
  border: "#dbe8f0",
  text: "#0f172a",
  textSub: "#475569",
  textMuted: "#94a3b8",
  danger: "#dc2626",
  success: "#16a34a",
  tealPale: "#f0f8fc",
  tealLight: "#e6f3f9",
} as const;

// UX-only guard; support_db_client_users.py's _validate_group_item_name is
// the real boundary that stops an oversized paste from being persisted.
const GROUP_NAME_MAX_LENGTH = 100;

// UX-only guards for the network/camera/company-profile fields below — the
// real boundary is the length caps applied server-side in
// support_db_settings.py so a direct API call can't bypass these.
const IP_MAX_LENGTH = 45; // fits IPv4, IPv6 and hostnames
const PORT_MAX_LENGTH = 6;
const CREDENTIAL_MAX_LENGTH = 128;
const CAMERA_NAME_MAX_LENGTH = 100;
const CAMERA_LOCATION_MAX_LENGTH = 150;
const CAMERA_CHANNEL_MAX_LENGTH = 20;
const RTSP_URL_MAX_LENGTH = 500;

const WORKFORCE_TYPES = new Set([
  "staff",
  "employee",
  "employees",
  "teacher",
  "teachers",
  "faculty",
  "admin",
  "administration",
  "administrator",
  "worker",
  "workers",
  "supervisor",
  "supervisors",
  "doctor",
  "doctors",
  "nurse",
  "nurses",
  "personnel",
]);

function normalizeKey(value: unknown): string {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[\s-]+/g, "_");
}
function titleCase(value: string): string {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}

function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

function cleanText(value: unknown): string {
  return String(value ?? "").trim();
}

// Persists which branch the Attendance Configuration section is showing via
// a URL query param, so navigating away from Settings and back (which fully
// unmounts this component while the bootstrap reloads — see the
// `isLoading` early return below) doesn't silently reset selectedBranchId
// back to branches[0]. Without this, any manual instruction / capture
// setting saved under a non-first branch looked like it "disappeared" on
// return, when it was actually still saved — just filtered out by the
// branch that got re-selected by default.
//
// The URL (not localStorage, not a Supabase-backed preference) is the right
// place for this: it's plain view state, not application data, so it
// doesn't need a round trip, a table, or a migration to remember — and
// unlike localStorage it also makes the current view shareable/bookmarkable
// and survives a hard refresh or opening the page in a new tab.
const ATTENDANCE_BRANCH_PARAM = "branch";

function readBranchIdFromUrl(): string | null {
  try {
    return new URLSearchParams(window.location.search).get(
      ATTENDANCE_BRANCH_PARAM,
    );
  } catch {
    return null;
  }
}

function writeBranchIdToUrl(branchId: string): void {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set(ATTENDANCE_BRANCH_PARAM, branchId);
    window.history.replaceState(window.history.state, "", url.toString());
  } catch {
    // Non-browser environment or URL API unavailable — selection just
    // won't survive a remount.
  }
}

function makeId(prefix: string): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return `${prefix}_${crypto.randomUUID()}`;
  }
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

function readStringList(...values: unknown[]): string[] {
  for (const value of values) {
    if (Array.isArray(value)) {
      const items = value.map(normalizeKey).filter(Boolean);
      if (items.length) return items;
    }
    if (typeof value === "string" && value.trim()) {
      try {
        const parsed = JSON.parse(value);
        if (Array.isArray(parsed)) {
          const items = parsed.map(normalizeKey).filter(Boolean);
          if (items.length) return items;
        }
      } catch {
        const items = value.split(",").map(normalizeKey).filter(Boolean);
        if (items.length) return items;
      }
    }
  }
  return [];
}

function labelFromOverride(
  overrides: Record<string, unknown>,
  keys: string[],
  fallback: string,
): string {
  for (const key of keys) {
    const value = overrides[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return fallback;
}

function resolveOperationalPlan(
  data: BootstrapResponse | null,
): OperationalPlan {
  const org = data?.organization;
  const verticalConfig = asRecord(org?.vertical_config || org?.verticalConfig);
  const businessType = normalizeKey(
    org?.business_type ||
    org?.biz_type ||
    org?.org_type ||
    verticalConfig.business_type ||
    "company",
  );
  const primaryPeopleType = normalizeKey(
    org?.primary_people_type ||
    org?.primaryPeopleType ||
    verticalConfig.primary_people_type ||
    "staff",
  );
  const enabledTypes = readStringList(
    org?.enabled_people_types,
    org?.enabledPeopleTypes,
    verticalConfig.enabled_people_types,
  );
  const attendanceTypes = readStringList(
    org?.attendance_people_types,
    org?.attendancePeopleTypes,
    verticalConfig.attendance_people_types,
  );
  const peopleScope = attendanceTypes.length
    ? attendanceTypes
    : enabledTypes.length
      ? enabledTypes
      : [primaryPeopleType];

  const isAcademic =
    businessType.includes("school") ||
    businessType.includes("college") ||
    businessType.includes("university") ||
    businessType.includes("academy") ||
    peopleScope.some((type) => type.includes("student"));

  const isFactory =
    businessType.includes("factory") ||
    businessType.includes("manufacturing") ||
    businessType.includes("plant") ||
    peopleScope.some((type) => type.includes("worker"));

  const hasStudentSetup = peopleScope.some((type) => type.includes("student"));
  const hasWorkforceSetup = peopleScope.some(
    (type) => WORKFORCE_TYPES.has(type) || !type.includes("student"),
  );

  return {
    hasStudentSetup,
    hasWorkforceSetup:
      hasWorkforceSetup || (!hasStudentSetup && peopleScope.length > 0),
    isAcademic,
    isFactory,
  };
}

function buildTerminology(
  data: BootstrapResponse | null,
  plan: OperationalPlan,
): Terminology {
  const org = data?.organization;
  const verticalConfig = asRecord(org?.vertical_config || org?.verticalConfig);
  const overrides = asRecord(
    org?.terminology_overrides || org?.terminologyOverrides,
  );

  const studentGroupFallback = plan.isAcademic ? "Class" : "Group";
  const studentGroupPluralFallback = plan.isAcademic
    ? "Classes & Sections"
    : "Groups";
  const workforceGroupFallback = plan.isFactory
    ? "Department / Unit"
    : "Department";
  const workforceGroupPluralFallback = plan.isFactory
    ? "Departments / Units"
    : "Departments";

  return {
    organizationLabel: labelFromOverride(
      overrides,
      ["organization", "organizationLabel", "org_label"],
      "Organization",
    ),
    branchLabel: labelFromOverride(
      overrides,
      ["branch", "branchLabel", "branch_label"],
      "Branch",
    ),
    studentGroupLabel: labelFromOverride(
      overrides,
      ["class", "classLabel", "studentGroup", "studentGroupLabel"],
      studentGroupFallback,
    ),
    studentGroupPlural: labelFromOverride(
      overrides,
      ["classes", "classPlural", "studentGroups", "studentGroupPlural"],
      studentGroupPluralFallback,
    ),
    studentSubgroupLabel: labelFromOverride(
      overrides,
      ["section", "sectionLabel", "studentSubgroup", "studentSubgroupLabel"],
      "Section",
    ),
    workforceGroupLabel: labelFromOverride(
      overrides,
      [
        "department",
        "departmentLabel",
        "unit",
        "unitLabel",
        "group",
        "groupLabel",
      ],
      workforceGroupFallback,
    ),
    workforceGroupPlural: labelFromOverride(
      overrides,
      [
        "departments",
        "departmentPlural",
        "units",
        "unitPlural",
        "groups",
        "groupPlural",
      ],
      workforceGroupPluralFallback,
    ),
    designationLabel: labelFromOverride(
      overrides,
      ["designation", "designationLabel", "role", "roleLabel"],
      "Designation",
    ),
    designationPlural: labelFromOverride(
      overrides,
      ["designations", "designationPlural", "roles", "rolePlural"],
      "Designations",
    ),
    cameraLabel: labelFromOverride(
      overrides,
      ["camera", "cameraLabel"],
      "Camera",
    ),
    cameraPlural: labelFromOverride(
      overrides,
      ["cameras", "cameraPlural"],
      "Cameras",
    ),
    studentPlural: labelFromOverride(
      overrides,
      ["students", "studentPlural"],
      "Students",
    ),
    workforcePlural: labelFromOverride(
      overrides,
      [
        "staff",
        "employees",
        "workers",
        "teachers",
        "workforce",
        "workforcePlural",
      ],
      plan.isFactory
        ? "Workers / Staff"
        : plan.isAcademic
          ? "Staff / Teachers / Administration"
          : "People",
    ),
  };
}

const DEFAULT_COMPANY_PROFILE: CompanyProfile = {
  address: "",
  city: "",
  publicContactPhone: "",
  timezone: "Asia/Karachi",
  logoDataUrl: "",
  logoFileName: "",
};

/**
 * Parses the company-profile fields out of a bootstrap response. Shared by
 * configFromBootstrap (this page's editable network/camera/department
 * config) and AccountSettings.tsx's read-only "Organization Profile" card,
 * so both derive these fields the same way instead of maintaining two
 * copies of this fallback chain that could drift apart.
 */
export function companyProfileFromBootstrap(
  data: BootstrapResponse,
): CompanyProfile {
  const saved = asRecord(
    data.onboarding_config || data.onboardingConfig || data.config || {},
  );
  const profile = asRecord(saved.company_profile || saved.companyProfile);

  return {
    address:
      cleanText(profile.address ?? data.config?.address) ||
      DEFAULT_COMPANY_PROFILE.address,
    city:
      cleanText(profile.city ?? data.config?.city) ||
      DEFAULT_COMPANY_PROFILE.city,
    publicContactPhone:
      cleanText(
        profile.publicContactPhone ||
        profile.public_contact_phone ||
        data.config?.publicContactPhone,
      ) ||
      cleanText(data.organization?.contact_phone) ||
      DEFAULT_COMPANY_PROFILE.publicContactPhone,
    timezone:
      cleanText(profile.timezone ?? data.config?.timezone) ||
      DEFAULT_COMPANY_PROFILE.timezone,
    logoDataUrl:
      cleanText(profile.logoDataUrl || profile.logo || data.config?.logo) ||
      DEFAULT_COMPANY_PROFILE.logoDataUrl,
    logoFileName:
      cleanText(profile.logoFileName || profile.logo_file_name) ||
      DEFAULT_COMPANY_PROFILE.logoFileName,
  };
}

/**
 * Archived-staff auto-delete window. 60 days is the default for any branch
 * that hasn't picked one yet — long enough that an accidental archive is
 * still recoverable, short enough to actually keep the local SQLite DB
 * from growing without bound, which is the whole point of this setting.
 * Keep in sync with local_db.py's ALLOWED_ARCHIVED_STAFF_RETENTION_DAYS —
 * that's the value actually enforced server-side; this list is only what
 * the dropdown offers.
 */
const RETENTION_DAY_OPTIONS = [30, 60, 90] as const;
const DEFAULT_RETENTION_DAYS = 60;

function normalizeRetentionDays(value: unknown): number {
  const days = Number(value);
  return (RETENTION_DAY_OPTIONS as readonly number[]).includes(days)
    ? days
    : DEFAULT_RETENTION_DAYS;
}

/** Same key-remapping convention as normalizeBranchRecord, minus the
 * array-of-items shape — this field is one number per branch, not a list. */
function normalizeRetentionDaysRecord(
  value: unknown,
  branches: Branch[],
  keyMap: Record<string, string>,
): Record<string, number> {
  const result: Record<string, number> = {};
  branches.forEach((branch) => {
    result[String(branch.id)] = DEFAULT_RETENTION_DAYS;
  });

  Object.entries(asRecord(value)).forEach(([rawBranchId, rawDays]) => {
    const branchId = keyMap[String(rawBranchId)] || String(rawBranchId);
    if (!Object.prototype.hasOwnProperty.call(result, branchId)) return;
    result[branchId] = normalizeRetentionDays(rawDays);
  });

  return result;
}

function emptyConfig(branches: Branch[]): OperationalConfig {
  const departments: Record<string, GroupItem[]> = {};
  const roles: Record<string, DesignationItem[]> = {};
  const cameras: Record<string, CameraItem[]> = {};
  const archivedStaffRetentionDays: Record<string, number> = {};

  branches.forEach((branch) => {
    departments[String(branch.id)] = [];
    roles[String(branch.id)] = [];
    cameras[String(branch.id)] = [];
    archivedStaffRetentionDays[String(branch.id)] = DEFAULT_RETENTION_DAYS;
  });

  return {
    company_profile: { ...DEFAULT_COMPANY_PROFILE },
    departments,
    roles,
    cameras,
    network: {
      publicIp: "",
      nvrDvrIp: "",
      rtspUsername: "",
      rtspPassword: "",
      rtspPort: "554",
    },
    archived_staff_retention_days: archivedStaffRetentionDays,
  };
}

function branchKeyMap(
  data: BootstrapResponse,
  branches: Branch[],
): Record<string, string> {
  const map: Record<string, string> = {};
  branches.forEach((branch, index) => {
    map[String(branch.id)] = String(branch.id);
    map[String(index + 1)] = String(branch.id);
  });

  asArray<Record<string, unknown>>(data.config?.branches).forEach((branch) => {
    const backendKey = cleanText(
      branch.backend_branch_id || branch.backendBranchId || branch.branch_uuid,
    );
    const uiKey = cleanText(
      branch.id || branch.branchId || branch.branch_ui_id || branch.branchUiId,
    );
    // Settings state is keyed by the numeric UI branch id, while local and
    // cloud persistence may return the stable backend branch id. Normalize
    // both forms to the branch key used by `branches` above so a save/reload
    // does not silently discard departments, designations, or cameras.
    if (backendKey) map[backendKey] = uiKey || backendKey;
    if (uiKey && backendKey) map[uiKey] = uiKey;
  });

  return map;
}

function normalizeGroupItem(
  raw: Record<string, unknown>,
  fallbackFamily: PersonFamily,
): GroupItem | null {
  const className = cleanText(raw.className || raw.class_name);
  const sectionName = cleanText(raw.sectionName || raw.section_name);
  const personFamily =
    cleanText(raw.personFamily || raw.person_family) === "student" ||
      className ||
      sectionName
      ? "student"
      : fallbackFamily;
  const itemKind: GroupItem["itemKind"] =
    personFamily === "student" ? "class_section" : "group";
  const name =
    cleanText(raw.name) || [className, sectionName].filter(Boolean).join(" - ");
  if (!name) return null;

  return {
    id: cleanText(raw.id) || makeId("group"),
    name,
    className: className || undefined,
    sectionName: sectionName || undefined,
    itemKind,
    personFamily,
  };
}

function normalizeDesignationItem(
  raw: Record<string, unknown>,
): DesignationItem | null {
  const name = cleanText(raw.name);
  if (!name) return null;

  const rawLevel = cleanText(
    raw.level,
  ).toLowerCase() as DesignationItem["level"];
  const allowed: DesignationItem["level"][] = [
    "admin",
    "manager",
    "staff",
    "teacher",
    "student",
    "worker",
    "custom",
  ];

  const departmentId = cleanText(raw.departmentId || raw.department_id);

  return {
    id: cleanText(raw.id) || makeId("designation"),
    name,
    departmentId: departmentId || undefined,
    level: allowed.includes(rawLevel) ? rawLevel : "custom",
    personFamily: "workforce",
  };
}

/**
 * One-time repair pass for the pre-department-linking data model: any
 * designation loaded with no departmentId (old flat-list payloads saved
 * before this field existed, or a designation whose department was since
 * deleted) is pinned to that branch's first configured workforce
 * department. Designations that already carry a valid departmentId are
 * left untouched. If a branch has no workforce departments at all yet,
 * the designation is left unassigned — there's nothing to backfill it
 * onto — and will surface once a department exists and the admin
 * reassigns it via WorkforceStructureEditor.
 */
function backfillDesignationDepartments(
  departments: Record<string, GroupItem[]>,
  roles: Record<string, DesignationItem[]>,
): Record<string, DesignationItem[]> {
  const result: Record<string, DesignationItem[]> = {};
  Object.entries(roles).forEach(([branchId, items]) => {
    const workforceDepartmentIds = new Set(
      (departments[branchId] || [])
        .filter((item) => item.personFamily === "workforce")
        .map((item) => item.id),
    );
    const defaultDepartmentId = (departments[branchId] || []).find(
      (item) => item.personFamily === "workforce",
    )?.id;
    result[branchId] = items.map((item) => {
      if (item.departmentId && workforceDepartmentIds.has(item.departmentId)) {
        return item;
      }
      return defaultDepartmentId
        ? { ...item, departmentId: defaultDepartmentId }
        : item;
    });
  });
  return result;
}

function normalizeCameraItem(raw: Record<string, unknown>): CameraItem | null {
  const name = cleanText(raw.name || raw.camera_name || raw.cameraName);
  const rtspUrl = cleanText(raw.rtspUrl || raw.rtsp_url);
  const channel = cleanText(raw.channel) || "1";
  if (!name && !rtspUrl && !channel) return null;

  const rawType = cleanText(raw.type).toLowerCase();
  const type: CameraItem["type"] =
    rawType === "dvr" ||
      rawType === "ip_camera" ||
      rawType === "webcam" ||
      rawType === "nvr"
      ? rawType
      : "nvr";

  return {
    id: cleanText(raw.id) || makeId("camera"),
    name: name || "Camera",
    location: cleanText(raw.location),
    rtspUrl,
    channel,
    type,
  };
}

function normalizeBranchRecord<T extends { id: string }>(
  value: unknown,
  branches: Branch[],
  keyMap: Record<string, string>,
  normalize: (item: Record<string, unknown>) => T | null,
): Record<string, T[]> {
  const result: Record<string, T[]> = {};
  branches.forEach((branch) => {
    result[String(branch.id)] = [];
  });

  Object.entries(asRecord(value)).forEach(([rawBranchId, rawItems]) => {
    const branchId = keyMap[String(rawBranchId)] || String(rawBranchId);
    if (!Object.prototype.hasOwnProperty.call(result, branchId)) return;
    result[branchId] = asArray<Record<string, unknown>>(rawItems)
      .map(normalize)
      .filter((item): item is T => item !== null);
  });

  return result;
}

function configFromBootstrap(
  data: BootstrapResponse,
  branches: Branch[],
): OperationalConfig {
  const saved = asRecord(
    data.onboarding_config || data.onboardingConfig || data.config || {},
  );
  const network = asRecord(
    saved.network || data.config?.network || data.config?.networkConfig,
  );
  const keyMap = branchKeyMap(data, branches);

  const departments = normalizeBranchRecord<GroupItem>(
    saved.departments ||
    data.config?.departments ||
    data.config?.groups ||
    data.config?.classes,
    branches,
    keyMap,
    (item) => normalizeGroupItem(item, "workforce"),
  );
  const roles = backfillDesignationDepartments(
    departments,
    normalizeBranchRecord<DesignationItem>(
      saved.roles || data.config?.roles || data.config?.designations,
      branches,
      keyMap,
      normalizeDesignationItem,
    ),
  );

  return {
    company_profile: companyProfileFromBootstrap(data),
    departments,
    roles,
    cameras: normalizeBranchRecord<CameraItem>(
      saved.cameras || data.config?.cameras,
      branches,
      keyMap,
      normalizeCameraItem,
    ),
    network: {
      publicIp: cleanText(network.publicIp || network.public_ip),
      nvrDvrIp: cleanText(
        network.nvrDvrIp ||
        network.nvr_dvr_ip ||
        network.nvrLocalIp ||
        network.nvr_local_ip,
      ),
      rtspUsername: cleanText(network.rtspUsername || network.rtsp_username),
      rtspPassword: cleanText(network.rtspPassword || network.rtsp_password),
      rtspPort: cleanText(network.rtspPort || network.rtsp_port) || "554",
    },
    shiftEnabledPeopleTypes: readStringList(
      (saved.shiftEnabledPeopleTypes as unknown) ||
      saved.shift_enabled_people_types ||
      data.config?.shiftEnabledPeopleTypes ||
      data.config?.shift_enabled_people_types,
    ),
    archived_staff_retention_days: normalizeRetentionDaysRecord(
      saved.archived_staff_retention_days ||
      saved.archivedStaffRetentionDays ||
      data.config?.archived_staff_retention_days,
      branches,
      keyMap,
    ),
  };
}

async function saveOperationalConfig(payload: {
  user_id: string | number;
  organization_id: string | number;
  config: OperationalConfig;
}): Promise<BootstrapResponse> {
  return fetchClientJson<BootstrapResponse>("/api/client/onboarding/complete", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

function totalItems<T>(
  record: Record<string, T[]>,
  filter?: (item: T) => boolean,
): number {
  return Object.values(record).reduce(
    (sum, items) => sum + (filter ? items.filter(filter).length : items.length),
    0,
  );
}

export function inputStyle(extra?: React.CSSProperties): React.CSSProperties {
  return {
    width: "100%",
    minHeight: 42,
    border: `1.5px solid ${C.border}`,
    borderRadius: 10,
    background: C.card,
    padding: "0 12px",
    fontFamily: "inherit",
    fontSize: 13,
    color: C.text,
    outline: "none",
    ...extra,
  };
}

export function cardStyle(extra?: React.CSSProperties): React.CSSProperties {
  return {
    background: C.card,
    border: `1.5px solid ${C.border}`,
    borderRadius: 16,
    boxShadow: "0 8px 26px rgba(15,45,74,.07)",
    ...extra,
  };
}

export function buttonStyle(
  variant: "primary" | "secondary" | "danger" = "primary",
): React.CSSProperties {
  const primary = variant === "primary";
  const danger = variant === "danger";
  return {
    minHeight: 42,
    border: primary || danger ? "none" : `1.5px solid ${C.border}`,
    borderRadius: 10,
    padding: "0 14px",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 7,
    background: danger ? C.danger : primary ? C.primary : C.card,
    color: primary || danger ? "#fff" : C.textSub,
    fontSize: 13,
    fontWeight: 900,
    cursor: "pointer",
    fontFamily: "inherit",
    whiteSpace: "nowrap",
  };
}

export function sectionTitle(): React.CSSProperties {
  return {
    margin: 0,
    fontSize: 12,
    color: C.primaryDark,
    fontWeight: 950,
    textTransform: "uppercase",
    letterSpacing: ".08em",
  };
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label style={{ display: "grid", gap: 6 }}>
      <span
        style={{
          fontSize: 11,
          color: C.textSub,
          fontWeight: 850,
          textTransform: "uppercase",
          letterSpacing: ".05em",
        }}
      >
        {label}
      </span>
      {children}
    </label>
  );
}

function BranchTabs({
  branches,
  activeBranchId,
  onChange,
}: {
  branches: Branch[];
  activeBranchId: string;
  onChange: (branchId: string) => void;
}) {
  return (
    <div
      style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 18 }}
    >
      {branches.map((branch) => {
        const active = branch.id === activeBranchId;
        return (
          <button
            key={branch.id}
            type="button"
            onClick={() => onChange(branch.id)}
            style={{
              ...buttonStyle("secondary"),
              borderColor: active ? C.primary : C.border,
              background: active ? C.tealLight : C.card,
              color: active ? C.primaryDark : C.textSub,
              minHeight: 34,
            }}
          >
            <GitBranch size={13} /> {branch.name}
          </button>
        );
      })}
    </div>
  );
}

function StudentStructureEditor({
  branches,
  groups,
  setGroups,
  terminology,
}: {
  branches: Branch[];
  groups: Record<string, GroupItem[]>;
  setGroups: React.Dispatch<React.SetStateAction<Record<string, GroupItem[]>>>;
  terminology: Terminology;
}) {
  const [activeBranchId, setActiveBranchId] = useState(branches[0]?.id || "");
  const [className, setClassName] = useState("");
  const [sectionName, setSectionName] = useState("");
  const list = (groups[activeBranchId] || []).filter(
    (item) => item.personFamily === "student",
  );

  const add = () => {
    const cleanClass = className.trim();
    const cleanSection = sectionName.trim();
    if (!activeBranchId || !cleanClass) return;
    const name = cleanSection ? `${cleanClass} - ${cleanSection}` : cleanClass;
    setGroups((prev) => ({
      ...prev,
      [activeBranchId]: [
        ...(prev[activeBranchId] || []),
        {
          id: makeId("class_section"),
          name,
          className: cleanClass,
          sectionName: cleanSection || undefined,
          itemKind: "class_section",
          personFamily: "student",
        },
      ],
    }));
    setClassName("");
    setSectionName("");
  };

  return (
    <ConfigCard
      icon={<GraduationCap size={18} />}
      title={terminology.studentGroupPlural}
    >
      <BranchTabs
        branches={branches}
        activeBranchId={activeBranchId}
        onChange={setActiveBranchId}
      />
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr auto",
          gap: 10,
          marginBottom: 14,
        }}
      >
        <input
          value={className}
          onChange={(event) => setClassName(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && add()}
          style={inputStyle()}
          maxLength={GROUP_NAME_MAX_LENGTH}
          placeholder={`${terminology.studentGroupLabel} name`}
        />
        <input
          value={sectionName}
          onChange={(event) => setSectionName(event.target.value)}
          onKeyDown={(event) => event.key === "Enter" && add()}
          style={inputStyle()}
          maxLength={GROUP_NAME_MAX_LENGTH}
          placeholder={`${terminology.studentSubgroupLabel} name`}
        />
        <button type="button" onClick={add} style={buttonStyle("primary")}>
          <Plus size={15} /> Add
        </button>
      </div>
      <ChipList
        items={list}
        empty={`No ${terminology.studentGroupPlural.toLowerCase()} configured for this branch.`}
        onRemove={(id) =>
          setGroups((prev) => ({
            ...prev,
            [activeBranchId]: (prev[activeBranchId] || []).filter(
              (item) => item.id !== id,
            ),
          }))
        }
      />
    </ConfigCard>
  );
}

/**
 * Case-insensitive duplicate-name check, run client-side purely for instant
 * feedback — mirrors workforceStructureApi.ts's isDuplicateWorkforceName
 * (used by StaffModal's quick-add popovers) rule-for-rule so the two entry
 * points reject the same names, but kept as its own copy here since this
 * file already maintains its own local GroupItem/DesignationItem shapes
 * rather than importing that module's. The backend
 * (local_db.add_department) re-checks this itself on the live-persist
 * path and is the actual guard — never rely on this alone.
 */
function isDuplicateWorkforceName(
  existing: { name: string }[],
  name: string,
): boolean {
  const clean = name.trim().toLowerCase();
  return existing.some((item) => item.name.trim().toLowerCase() === clean);
}

function WorkforceStructureEditor({
  branches,
  groups,
  setGroups,
  designations,
  setDesignations,
  terminology,
}: {
  branches: Branch[];
  groups: Record<string, GroupItem[]>;
  setGroups: React.Dispatch<React.SetStateAction<Record<string, GroupItem[]>>>;
  designations: Record<string, DesignationItem[]>;
  setDesignations: React.Dispatch<
    React.SetStateAction<Record<string, DesignationItem[]>>
  >;
  terminology: Terminology;
}) {
  const [activeBranchId, setActiveBranchId] = useState(branches[0]?.id || "");
  const [groupName, setGroupName] = useState("");
  const [groupNameError, setGroupNameError] = useState<string | null>(null);
  const [designationName, setDesignationName] = useState("");
  // Which department's designations are currently in scope. Derived rather
  // than reset via an effect: whenever the stored id isn't present in the
  // current branch's department list (branch just switched, or the
  // previously active department was just deleted) it naturally falls back
  // to that branch's first department. `id`s are branch-unique (makeId), so
  // a stale id from another branch never accidentally matches.
  const [selectedDepartmentId, setSelectedDepartmentId] = useState("");
  const groupList = (groups[activeBranchId] || []).filter(
    (item) => item.personFamily === "workforce",
  );
  const activeDepartmentId = groupList.some(
    (item) => item.id === selectedDepartmentId,
  )
    ? selectedDepartmentId
    : groupList[0]?.id || "";
  const activeDepartment = groupList.find(
    (item) => item.id === activeDepartmentId,
  );
  // Designations are only ever added with a departmentId (see
  // addDesignation) and any legacy/orphaned ones were already backfilled
  // onto a real department in configFromBootstrap — so this scopes
  // strictly to the currently selected department, with no fallback to
  // "show everything" that would recreate the original flat-list bug.
  const designationList = (designations[activeBranchId] || []).filter(
    (item) => item.departmentId === activeDepartmentId,
  );

  const addGroup = () => {
    const name = groupName.trim();
    if (!activeBranchId || !name) return;
    // Departments are matched by name in the other places this same
    // onboarding_config JSON gets independently re-parsed (StaffModal's
    // department dropdown, ShiftAllocationTab's department filter) — so a
    // case-only duplicate ("Sales" vs "sales") would silently split what
    // the admin thinks is one department into two dropdown entries.
    // Checking case-insensitively here closes that off at the source.
    if (isDuplicateWorkforceName(groupList, name)) {
      setGroupNameError(`"${name}" is already configured for this branch.`);
      return;
    }
    setGroupNameError(null);
    const id = makeId("department");
    setGroups((prev) => ({
      ...prev,
      [activeBranchId]: [
        ...(prev[activeBranchId] || []),
        { id, name, itemKind: "group", personFamily: "workforce" },
      ],
    }));
    setGroupName("");
    // Jump straight to the new department so its (currently empty)
    // designation list is what the admin sees next — that's the whole
    // point of the department -> designation flow being requested here.
    setSelectedDepartmentId(id);
  };

  const removeGroup = (id: string) => {
    const owned = (designations[activeBranchId] || []).filter(
      (item) => item.departmentId === id,
    );
    if (
      owned.length > 0 &&
      !window.confirm(
        `Delete this ${terminology.workforceGroupLabel.toLowerCase()}? Its ${owned.length} ${owned.length === 1
          ? terminology.designationLabel.toLowerCase()
          : terminology.designationPlural.toLowerCase()
        } will be deleted too.`,
      )
    ) {
      return;
    }
    setGroups((prev) => ({
      ...prev,
      [activeBranchId]: (prev[activeBranchId] || []).filter(
        (item) => item.id !== id,
      ),
    }));
    setDesignations((prev) => ({
      ...prev,
      [activeBranchId]: (prev[activeBranchId] || []).filter(
        (item) => item.departmentId !== id,
      ),
    }));
  };

  const addDesignation = () => {
    const name = designationName.trim();
    if (!activeBranchId || !activeDepartmentId || !name) return;
    setDesignations((prev) => ({
      ...prev,
      [activeBranchId]: [
        ...(prev[activeBranchId] || []),
        {
          id: makeId("designation"),
          name,
          departmentId: activeDepartmentId,
          level: "custom",
          personFamily: "workforce",
        },
      ],
    }));
    setDesignationName("");
  };

  return (
    <ConfigCard
      icon={<Users size={18} />}
      title={`${terminology.workforceGroupPlural} & ${terminology.designationPlural}`}
    >
      <BranchTabs
        branches={branches}
        activeBranchId={activeBranchId}
        onChange={(branchId) => {
          setActiveBranchId(branchId);
          setSelectedDepartmentId("");
          setGroupNameError(null);
        }}
      />
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        <div>
          <h3 style={sectionTitle()}>{terminology.workforceGroupPlural}</h3>
          <div style={{ display: "flex", gap: 10, margin: "12px 0 14px" }}>
            <input
              value={groupName}
              onChange={(event) => {
                setGroupName(event.target.value);
                if (groupNameError) setGroupNameError(null);
              }}
              onKeyDown={(event) => event.key === "Enter" && addGroup()}
              style={inputStyle(
                groupNameError ? { borderColor: C.danger } : undefined,
              )}
              maxLength={GROUP_NAME_MAX_LENGTH}
              placeholder={`Add ${terminology.workforceGroupLabel.toLowerCase()}`}
              aria-invalid={Boolean(groupNameError)}
            />
            <button
              type="button"
              onClick={addGroup}
              style={buttonStyle("primary")}
            >
              <Plus size={15} /> Add
            </button>
          </div>
          {groupNameError && (
            <div style={{ marginTop: -8, marginBottom: 12, fontSize: 12, color: C.danger }}>
              {groupNameError}
            </div>
          )}
          <ChipList
            items={groupList}
            empty={`No ${terminology.workforceGroupPlural.toLowerCase()} configured for this branch.`}
            activeId={activeDepartmentId}
            onSelect={setSelectedDepartmentId}
            onRemove={removeGroup}
          />
        </div>
        <div>
          <h3 style={sectionTitle()}>
            {terminology.designationPlural}
            {activeDepartment ? (
              <span style={{ color: C.textMuted, fontWeight: 600 }}>
                {" "}
                — {activeDepartment.name}
              </span>
            ) : null}
          </h3>
          <div style={{ display: "flex", gap: 10, margin: "12px 0 14px" }}>
            <input
              value={designationName}
              onChange={(event) => setDesignationName(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && addDesignation()}
              style={inputStyle()}
              maxLength={GROUP_NAME_MAX_LENGTH}
              disabled={!activeDepartmentId}
              placeholder={
                activeDepartmentId
                  ? `Add ${terminology.designationLabel.toLowerCase()}`
                  : `Add a ${terminology.workforceGroupLabel.toLowerCase()} first`
              }
            />
            <button
              type="button"
              onClick={addDesignation}
              disabled={!activeDepartmentId}
              style={{
                ...buttonStyle("primary"),
                opacity: activeDepartmentId ? 1 : 0.5,
                cursor: activeDepartmentId ? "pointer" : "not-allowed",
              }}
            >
              <Plus size={15} /> Add
            </button>
          </div>
          <ChipList
            items={designationList}
            empty={
              activeDepartmentId
                ? `No ${terminology.designationPlural.toLowerCase()} configured for ${activeDepartment?.name}.`
                : `Add a ${terminology.workforceGroupLabel.toLowerCase()} to configure its ${terminology.designationPlural.toLowerCase()}.`
            }
            onRemove={(id) =>
              setDesignations((prev) => ({
                ...prev,
                [activeBranchId]: (prev[activeBranchId] || []).filter(
                  (item) => item.id !== id,
                ),
              }))
            }
          />
        </div>
      </div>
    </ConfigCard>
  );
}

function ShiftSchedulingEditor({
  activePeopleTypes,
  shiftEnabledPeopleTypes,
  setShiftEnabledPeopleTypes,
  terminology,
}: {
  activePeopleTypes: string[];
  shiftEnabledPeopleTypes: string[] | undefined;
  setShiftEnabledPeopleTypes: (
    updater: React.SetStateAction<string[] | undefined>,
  ) => void;
  terminology: Terminology;
}) {
  const current = shiftEnabledPeopleTypes ?? activePeopleTypes;

  const toggle = (peopleType: string) => {
    const normalized = normalizeKey(peopleType);
    setShiftEnabledPeopleTypes((prev) => {
      const existing = prev || [];
      return existing.includes(normalized)
        ? existing.filter((v) => v !== normalized)
        : [...existing, normalized];
    });
  };

  if (!activePeopleTypes || activePeopleTypes.length === 0) return null;

  return (
    <ConfigCard icon={<ShieldCheck size={18} />} title="Shift Scheduling">
      <p style={{ margin: "6px 0 12px", color: C.textSub }}>
        Enable shift-based attendance for the people types below. These settings
        are saved as part of the onboarding config and are template-aware.
      </p>
      <div style={{ display: "grid", gap: 10 }}>
        {activePeopleTypes.map((pt) => {
          const normalized = normalizeKey(pt);
          return (
            <label
              key={pt}
              style={{ display: "flex", alignItems: "center", gap: 10 }}
            >
              <input
                type="checkbox"
                checked={current.includes(normalized)}
                onChange={() => toggle(pt)}
              />
              <span style={{ fontSize: 14 }}>{titleCase(pt)}</span>
            </label>
          );
        })}
      </div>
    </ConfigCard>
  );
}

function CameraSettingsEditor({
  branches,
  cameras,
  setCameras,
  network,
  setNetwork,
  terminology,
  organization,
}: {
  branches: Branch[];
  cameras: Record<string, CameraItem[]>;
  setCameras: React.Dispatch<
    React.SetStateAction<Record<string, CameraItem[]>>
  >;
  network: NetworkConfig;
  setNetwork: React.Dispatch<React.SetStateAction<NetworkConfig>>;
  terminology: Terminology;
  organization?: BootstrapOrganization;
}) {
  const [activeBranchId, setActiveBranchId] = useState(branches[0]?.id || "");
  const list = cameras[activeBranchId] || [];

  const addCamera = () => {
    if (!activeBranchId) return;
    setCameras((prev) => ({
      ...prev,
      [activeBranchId]: [
        ...(prev[activeBranchId] || []),
        {
          id: makeId("camera"),
          name: "",
          location: "",
          rtspUrl: "",
          channel: "1",
          type: "nvr",
        },
      ],
    }));
  };

  const patchCamera = (cameraId: string, patch: Partial<CameraItem>) => {
    setCameras((prev) => {
      const list = prev[activeBranchId] || [];
      return {
        ...prev,
        [activeBranchId]: list.map((camera) => {
          if (camera.id !== cameraId) return camera;
          const next = { ...camera, ...patch };
          // Switching a camera to webcam: seed a sensible default device
          // index (0, 1, 2...) based on how many webcams already exist on
          // this branch. Stays editable afterward for the multi-webcam
          // case. Mirrors OnboardingWizard.tsx's patchCamera exactly.
          if (patch.type === "webcam" && camera.type !== "webcam") {
            const webcamIndex = list.filter(
              (c) => c.type === "webcam" && c.id !== cameraId,
            ).length;
            next.channel = String(webcamIndex);
          }
          return next;
        }),
      };
    });
  };

  return (
    <ConfigCard
      icon={<Camera size={18} />}
      title={`Network & ${terminology.cameraPlural}`}
    >
      {/* <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: 14,
          marginBottom: 18,
        }}
      >
        <Field label="Public IP / Static IP">
          <input
            value={network.publicIp}
            onChange={(event) =>
              setNetwork((prev) => ({ ...prev, publicIp: event.target.value }))
            }
            style={inputStyle()}
            maxLength={IP_MAX_LENGTH}
          />
        </Field>
        <Field label="NVR / DVR Local IP">
          <input
            value={network.nvrDvrIp}
            onChange={(event) =>
              setNetwork((prev) => ({ ...prev, nvrDvrIp: event.target.value }))
            }
            style={inputStyle()}
            maxLength={IP_MAX_LENGTH}
          />
        </Field>
        <Field label="RTSP Port">
          <input
            value={network.rtspPort}
            onChange={(event) =>
              setNetwork((prev) => ({ ...prev, rtspPort: event.target.value }))
            }
            style={inputStyle()}
            maxLength={PORT_MAX_LENGTH}
          />
        </Field>
        <Field label="RTSP Username">
          <input
            value={network.rtspUsername}
            onChange={(event) =>
              setNetwork((prev) => ({
                ...prev,
                rtspUsername: event.target.value,
              }))
            }
            style={inputStyle()}
            maxLength={CREDENTIAL_MAX_LENGTH}
          />
        </Field>
        <Field label="RTSP Password">
          <input
            type="password"
            value={network.rtspPassword}
            onChange={(event) =>
              setNetwork((prev) => ({
                ...prev,
                rtspPassword: event.target.value,
              }))
            }
            maxLength={CREDENTIAL_MAX_LENGTH}
            style={inputStyle()}
          />
        </Field>
      </div> */}

      <BranchTabs
        branches={branches}
        activeBranchId={activeBranchId}
        onChange={setActiveBranchId}
      />
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <h3 style={sectionTitle()}>
          {terminology.branchLabel} {terminology.cameraPlural}
        </h3>
        <button
          type="button"
          onClick={addCamera}
          style={buttonStyle("primary")}
        >
          <Plus size={15} /> Add {terminology.cameraLabel}
        </button>
      </div>
      {list.length === 0 ? (
        <EmptyText
          text={`No ${terminology.cameraPlural.toLowerCase()} configured for this branch.`}
        />
      ) : null}
      <div style={{ display: "grid", gap: 10 }}>
        {list.map((camera) => (
          <div
            key={camera.id}
            style={cardStyle({ padding: 12, boxShadow: "none" })}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 92px 118px auto",
                gap: 8,
                alignItems: "center",
                marginBottom: 10,
              }}
            >
              <input
                value={camera.name}
                onChange={(event) =>
                  patchCamera(camera.id, { name: event.target.value })
                }
                style={inputStyle()}
                placeholder={`${terminology.cameraLabel} name`}
                maxLength={CAMERA_NAME_MAX_LENGTH}
              />
              <input
                value={camera.location}
                onChange={(event) =>
                  patchCamera(camera.id, { location: event.target.value })
                }
                style={inputStyle()}
                placeholder="Location / zone"
                maxLength={CAMERA_LOCATION_MAX_LENGTH}
              />
              <input
                value={camera.channel}
                onChange={(event) =>
                  patchCamera(camera.id, { channel: event.target.value })
                }
                style={inputStyle()}
                placeholder={camera.type === "webcam" ? "Device index" : "Ch."}
                maxLength={CAMERA_CHANNEL_MAX_LENGTH}
              />
              <select
                value={camera.type}
                onChange={(event) =>
                  patchCamera(camera.id, {
                    type: event.target.value as CameraItem["type"],
                  })
                }
                style={inputStyle()}
              >
                <option value="nvr">NVR</option>
                <option value="dvr">DVR</option>
                <option value="ip_camera">IP Camera</option>
                {organization?.attendance_mode === "local" && (
                  <option value="webcam">Webcam (USB/built-in)</option>
                )}
              </select>
              <button
                type="button"
                aria-label={`Remove ${camera.name || terminology.cameraLabel}`}
                onClick={() =>
                  setCameras((prev) => ({
                    ...prev,
                    [activeBranchId]: (prev[activeBranchId] || []).filter(
                      (item) => item.id !== camera.id,
                    ),
                  }))
                }
                style={buttonStyle("danger")}
              >
                <Trash2 size={15} />
              </button>
            </div>
            {camera.type !== "webcam" && (
              <>
                <input
                  value={camera.rtspUrl}
                  onChange={(event) =>
                    patchCamera(camera.id, { rtspUrl: event.target.value })
                  }
                  style={inputStyle({
                    fontFamily: "monospace",
                    fontSize: 12,
                    ...(validateRtspUrl(camera.rtspUrl)
                      ? { borderColor: C.danger }
                      : null),
                  })}
                  placeholder="Optional full RTSP URL. Leave empty when network + channel is enough."
                  spellCheck={false}
                  maxLength={RTSP_URL_MAX_LENGTH}
                  aria-invalid={Boolean(validateRtspUrl(camera.rtspUrl))}
                />
                {validateRtspUrl(camera.rtspUrl) && (
                  <div
                    style={{
                      marginTop: 6,
                      fontSize: 12,
                      color: C.danger,
                    }}
                  >
                    {validateRtspUrl(camera.rtspUrl)}
                  </div>
                )}
              </>
            )}
          </div>
        ))}
      </div>
    </ConfigCard>
  );
}

function LicenseSettingsEditor() {
  const [status, setStatus] = useState<{
    org_status?: string;
    expires_at?: number | null;
    org_id?: string | null;
    issued_at?: number | null;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [newKey, setNewKey] = useState("");
  const [updating, setUpdating] = useState(false);
  const [message, setMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch("/api/license/status");
      const data = await res.json();
      setStatus(data);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    const token = newKey.trim();
    if (!token) return;

    setUpdating(true);
    setMessage(null);
    try {
      const res = await fetch("/api/license/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ license_key: token }),
      });
      const data = await res.json();
      if (!res.ok || data.success === false) {
        setMessage({
          type: "error",
          text: data.message || data.error || "Failed to update license key.",
        });
      } else {
        setMessage({
          type: "success",
          text: "License successfully updated and extended!",
        });
        setNewKey("");
        await loadStatus();
      }
    } catch (err: any) {
      setMessage({
        type: "error",
        text: err?.message || "Failed to connect to license service.",
      });
    } finally {
      setUpdating(false);
    }
  };

  const formattedExpiry = status?.expires_at
    ? new Date(status.expires_at * 1000).toLocaleDateString(undefined, {
      year: "numeric",
      month: "long",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
    : null;

  const daysRemaining = status?.expires_at
    ? Math.max(
      0,
      Math.ceil(
        (status.expires_at * 1000 - Date.now()) / (1000 * 60 * 60 * 24),
      ),
    )
    : null;

  const isActive = status?.org_status === "active";

  return (
    <ConfigCard icon={<KeyRound size={18} />} title="License & Subscription">
      <div style={{ display: "grid", gap: 16 }}>
        {message && (
          <div
            style={{
              padding: "10px 14px",
              borderRadius: 10,
              fontSize: 13,
              fontWeight: 700,
              background: message.type === "success" ? "#f0fdf4" : "#fef2f2",
              color: message.type === "success" ? "#16a34a" : "#dc2626",
              border: `1px solid ${message.type === "success" ? "#bbf7d0" : "#fecaca"
                }`,
            }}
          >
            {message.text}
          </div>
        )}

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
            gap: 12,
          }}
        >
          <div
            style={{
              padding: "12px 14px",
              borderRadius: 12,
              background: C.tealPale,
              border: `1px solid ${C.border}`,
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 800,
                color: C.textMuted,
                textTransform: "uppercase",
                marginBottom: 4,
              }}
            >
              Status
            </div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                fontWeight: 900,
                color: isActive ? C.success : C.danger,
                fontSize: 14,
              }}
            >
              {isActive ? (
                <CheckCircle2 size={16} />
              ) : (
                <AlertCircle size={16} />
              )}
              {isActive
                ? "Active (Licensed)"
                : (status?.org_status || "Inactive").toUpperCase()}
            </div>
          </div>

          <div
            style={{
              padding: "12px 14px",
              borderRadius: 12,
              background: C.tealPale,
              border: `1px solid ${C.border}`,
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 800,
                color: C.textMuted,
                textTransform: "uppercase",
                marginBottom: 4,
              }}
            >
              Expires On
            </div>
            <div style={{ fontWeight: 850, color: C.text, fontSize: 13.5 }}>
              {loading ? "Checking…" : formattedExpiry || "No expiry set"}
            </div>
          </div>

          <div
            style={{
              padding: "12px 14px",
              borderRadius: 12,
              background: C.tealPale,
              border: `1px solid ${C.border}`,
            }}
          >
            <div
              style={{
                fontSize: 11,
                fontWeight: 800,
                color: C.textMuted,
                textTransform: "uppercase",
                marginBottom: 4,
              }}
            >
              Validity Remaining
            </div>
            <div
              style={{
                fontWeight: 850,
                color:
                  (daysRemaining ?? 0) <= 7 ? "#d97706" : C.primaryDark,
                fontSize: 13.5,
              }}
            >
              {loading
                ? "…"
                : daysRemaining !== null
                  ? `${daysRemaining} day${daysRemaining === 1 ? "" : "s"}`
                  : "N/A"}
            </div>
          </div>
        </div>

        <form
          onSubmit={handleUpdate}
          style={{ marginTop: 6, display: "grid", gap: 10 }}
        >
          <Field label="Renew / Replace License Key">
            <textarea
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="Paste new signed license token issued by QIntellect Support..."
              rows={3}
              style={{
                ...inputStyle(),
                minHeight: 70,
                padding: "10px 12px",
                fontFamily: "monospace",
                fontSize: 12,
                resize: "vertical",
              }}
            />
          </Field>
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              type="submit"
              disabled={updating || !newKey.trim()}
              style={{
                ...buttonStyle("primary"),
                opacity: updating || !newKey.trim() ? 0.6 : 1,
              }}
            >
              {updating ? (
                <Loader2
                  size={15}
                  style={{ animation: "spin .8s linear infinite" }}
                />
              ) : (
                <KeyRound size={15} />
              )}
              {updating ? "Verifying…" : "Apply New License"}
            </button>
          </div>
        </form>
      </div>
    </ConfigCard>
  );
}

export function ConfigCard({
  icon,
  title,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section style={cardStyle({ padding: 18 })}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 16,
        }}
      >
        <span style={{ color: C.primary }}>{icon}</span>
        <h2 style={sectionTitle()}>{title}</h2>
      </div>
      {children}
    </section>
  );
}

export function ReadOnlyLine({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        padding: "10px 12px",
        borderRadius: 10,
        background: C.tealPale,
        border: `1px solid ${C.border}`,
      }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 850,
          color: C.textSub,
          textTransform: "uppercase",
          letterSpacing: ".05em",
        }}
      >
        {label}
      </div>
      <div
        style={{ fontSize: 14, fontWeight: 900, color: C.text, marginTop: 4 }}
      >
        {value}
      </div>
    </div>
  );
}

/**
 * ChipList — plain removable chips by default. Passing `activeId` +
 * `onSelect` turns each chip into a selectable "tab" (used by
 * WorkforceStructureEditor to pick which department's designations are in
 * scope) without duplicating the chip markup/styles in a second component.
 * Call sites that only need remove-able chips (StudentStructureEditor, the
 * plain designation list) simply omit the two select props.
 */
function ChipList<T extends { id: string; name: string }>({
  items,
  empty,
  onRemove,
  activeId,
  onSelect,
}: {
  items: T[];
  empty: string;
  onRemove: (id: string) => void;
  activeId?: string;
  onSelect?: (id: string) => void;
}) {
  if (!items.length) return <EmptyText text={empty} />;
  const selectable = Boolean(onSelect);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 9 }}>
      {items.map((item) => {
        const active = selectable && item.id === activeId;
        return (
          <span
            key={item.id}
            role={selectable ? "button" : undefined}
            tabIndex={selectable ? 0 : undefined}
            onClick={selectable ? () => onSelect!(item.id) : undefined}
            onKeyDown={
              selectable
                ? (event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect!(item.id);
                  }
                }
                : undefined
            }
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 11px",
              borderRadius: 999,
              background: active ? C.tealLight : C.tealPale,
              border: `1px solid ${active ? C.primary : C.border}`,
              color: C.primaryDark,
              fontSize: 13,
              fontWeight: 800,
              cursor: selectable ? "pointer" : "default",
            }}
          >
            {item.name}
            <button
              type="button"
              aria-label={`Remove ${item.name}`}
              onClick={(event) => {
                if (selectable) event.stopPropagation();
                onRemove(item.id);
              }}
              style={{
                border: "none",
                background: "transparent",
                color: C.textMuted,
                cursor: "pointer",
                display: "grid",
                placeItems: "center",
              }}
            >
              <Trash2 size={13} />
            </button>
          </span>
        );
      })}
    </div>
  );
}

function EmptyText({ text }: { text: string }) {
  return (
    <div
      style={{
        fontSize: 13,
        color: C.textMuted,
        fontStyle: "italic",
        padding: "10px 0",
      }}
    >
      {text}
    </div>
  );
}

/**
 * ImportDataSection
 * ─────────────────────────────────────────────────────────────────────────────
 * Home for the embeddings-package import action, moved here from the Live
 * Attendance Monitoring header so it lives with the rest of the org's
 * configuration instead of a live/operational screen.
 *
 * This deployment target is a single local node writing to its own SQLite
 * database, not a multi-tenant Supabase-backed org — so `branchId` here is
 * just the plain local branch id, the same `branches` list (and the same
 * BranchTabs pattern) CameraSettingsEditor already uses below. There is no
 * Supabase branch UUID to resolve to.
 */
/**
 * Shared by every Settings section that needs its own branch tab-strip
 * (ImportDataSection, ArchivedStaffRetentionEditor) — one place owning
 * "pick a branch, and keep the selection valid as the branch list loads
 * or changes" instead of each section re-implementing the same effect.
 */
function useBranchSelection(branches: Branch[]): [string, (id: string) => void] {
  const [activeBranchId, setActiveBranchId] = useState(branches[0]?.id || "");

  useEffect(() => {
    if (!branches.length) return;
    if (branches.some((branch) => branch.id === activeBranchId)) return;
    setActiveBranchId(branches[0].id);
  }, [branches, activeBranchId]);

  return [activeBranchId, setActiveBranchId];
}

function ImportDataSection({ branches }: { branches: Branch[] }) {
  const [activeBranchId, setActiveBranchId] = useBranchSelection(branches);

  return (
    <ConfigCard icon={<UploadCloud size={18} />} title="Import Data">
      <p
        style={{
          margin: "0 0 16px",
          fontSize: 13,
          color: C.textSub,
          fontWeight: 600,
          lineHeight: 1.5,
        }}
      >
        Import a trainer-generated embeddings package (.zip) to sync face
        data for the branch selected below.
      </p>

      <BranchTabs
        branches={branches}
        activeBranchId={activeBranchId}
        onChange={setActiveBranchId}
      />

      <ImportPackageButton branchId={activeBranchId} />
    </ConfigCard>
  );
}

/**
 * ArchivedStaffRetentionEditor
 * ─────────────────────────────────────────────────────────────────────────────
 * Sets how long an archived staff record survives before it's permanently
 * deleted. Purely a policy knob from this page's point of view — the
 * actual deletion happens on the local node backend (retention_worker.py,
 * on a timer, calling local_db.purge_expired_archived_staff), driven by
 * this same archived_staff_retention_days value round-tripped through the
 * ordinary bootstrap/onboarding-complete config payload.
 *
 * Archiving a staff member already deletes their face embeddings
 * immediately (local_db.archive_staff) — this setting only controls when
 * the now-faceless archived record itself is finally removed, which is
 * what actually keeps the database from growing without bound. Restoring
 * an archived staff member before that point requires re-enrolling their
 * face, since the embeddings are already gone by the time archive
 * completes, not just at the end of this window.
 */
function ArchivedStaffRetentionEditor({
  branches,
  config,
  setConfig,
  terminology,
}: {
  branches: Branch[];
  config: OperationalConfig;
  setConfig: React.Dispatch<React.SetStateAction<OperationalConfig>>;
  terminology: Terminology;
}) {
  const [activeBranchId, setActiveBranchId] = useBranchSelection(branches);
  const days =
    config.archived_staff_retention_days[activeBranchId] ??
    DEFAULT_RETENTION_DAYS;

  const handleChange = (value: number) => {
    setConfig((prev) => ({
      ...prev,
      archived_staff_retention_days: {
        ...prev.archived_staff_retention_days,
        [activeBranchId]: value,
      },
    }));
  };

  return (
    <ConfigCard icon={<Clock size={18} />} title="Archived Staff Retention">
      <p
        style={{
          margin: "0 0 16px",
          fontSize: 13,
          color: C.textSub,
          fontWeight: 600,
          lineHeight: 1.5,
        }}
      >
        Archived {terminology.workforcePlural || "staff"} are permanently
        deleted — including their enrolled face data — this many days after
        being archived, so the database doesn't grow indefinitely.
      </p>

      <BranchTabs
        branches={branches}
        activeBranchId={activeBranchId}
        onChange={setActiveBranchId}
      />

      <Field label="Auto-delete after">
        <select
          value={days}
          onChange={(event) => handleChange(Number(event.target.value))}
          style={inputStyle()}
        >
          {RETENTION_DAY_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option} days
            </option>
          ))}
        </select>
      </Field>
    </ConfigCard>
  );
}

export default function Settings() {
  const auth = useAuth() as unknown as AuthContext;
  const { user, refreshUser } = auth;
  const { refreshOrgConfig } = useOrg();
  const organizationId = user?.organization_id || user?.organizationId;

  // Access gate: staff without the "settings" module grant never reach this
  // screen, even by typing /admin/settings directly — the gear icon in
  // AdminLayout.tsx already hides for them, this is the defense-in-depth
  // backstop. Admins/branch-admins (any non-"staff" role) always pass.
  const isRestrictedStaff =
    isStaffAccount(user) &&
    !getAccountAllowedModules(user).includes("settings");

  if (isRestrictedStaff) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: 320,
          gap: 10,
          textAlign: "center",
          padding: 24,
        }}
      >
        <AlertCircle size={28} color="#94a3b8" />
        <p
          style={{ margin: 0, fontSize: 15, fontWeight: 700, color: "#334155" }}
        >
          You don't have access to Settings
        </p>
        <p style={{ margin: 0, fontSize: 13, color: "#64748b", maxWidth: 360 }}>
          Ask your admin to grant the Settings module from Staff Management if
          you need to configure departments, capture settings, or timing
          overrides.
        </p>
      </div>
    );
  }

  const [bootstrap, setBootstrap] = useState<BootstrapResponse | null>(null);
  const [config, setConfig] = useState<OperationalConfig>(() =>
    emptyConfig([]),
  );
  const [branches, setBranches] = useState<Branch[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);
  const [selectedBranchId, setSelectedBranchId] = useState<string>("");

  const hasInvalidCameraRtspUrl = useMemo(
    () => findInvalidCameraRtspUrl(config.cameras) !== null,
    [config.cameras],
  );

  // Keep the selected branch valid as soon as branches load or change,
  // preferring whichever branch is named in the URL over always falling
  // back to branches[0]. This component remounts from scratch every time
  // the user navigates back to Settings (see the `isLoading` early return
  // further down), so without restoring the prior selection, the Attendance
  // Configuration section — and the manual instructions / capture settings
  // scoped to it — would silently flip back to the first branch and look
  // empty, even though the data for the previously-selected branch was
  // never touched.
  useEffect(() => {
    if (!branches.length) return;
    if (branches.some((b) => b.id === selectedBranchId)) return;
    const fromUrl = readBranchIdFromUrl();
    const restored =
      fromUrl && branches.some((b) => b.id === fromUrl)
        ? fromUrl
        : branches[0].id;
    setSelectedBranchId(restored);
  }, [branches, selectedBranchId]);

  // Keep the URL in sync so the restore effect above (and a page refresh,
  // or a shared link) can find the current selection again.
  useEffect(() => {
    if (selectedBranchId) {
      writeBranchIdToUrl(selectedBranchId);
    }
  }, [selectedBranchId]);

  const load = useCallback(async () => {
    if (!organizationId) {
      setError(
        "Organization is missing from the logged-in user. Please log in again.",
      );
      setIsLoading(false);
      return;
    }

    try {
      setIsLoading(true);
      setError(null);
      const data = await loadClientBootstrap<BootstrapResponse>(organizationId);
      const nextBranches = data.branches || [];
      setBootstrap(data);
      setBranches(nextBranches);
      setConfig(configFromBootstrap(data, nextBranches));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load dashboard setup.",
      );
    } finally {
      setIsLoading(false);
    }
  }, [organizationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const plan = useMemo(() => resolveOperationalPlan(bootstrap), [bootstrap]);
  const terminology = useMemo(
    () => buildTerminology(bootstrap, plan),
    [bootstrap, plan],
  );

  const activePeopleTypes = useMemo(() => {
    const org = bootstrap?.organization;
    const vertical = asRecord(org?.vertical_config || org?.verticalConfig);
    const enabled = readStringList(
      org?.enabled_people_types,
      org?.enabledPeopleTypes,
      vertical.enabled_people_types,
      vertical.enabledPeopleTypes,
    );
    const attendance = readStringList(
      org?.attendance_people_types,
      org?.attendancePeopleTypes,
      vertical.attendance_people_types,
      vertical.attendancePeopleTypes,
    );
    const primary = normalizeKey(
      org?.primary_people_type ||
      org?.primaryPeopleType ||
      vertical.primary_people_type ||
      "staff",
    );
    const scope = attendance.length
      ? attendance
      : enabled.length
        ? enabled
        : [primary];
    return scope;
  }, [bootstrap]);

  const save = async () => {
    if (!user?.id || !organizationId) {
      setError("User or organization is missing. Please log in again.");
      return;
    }

    const rtspError = findInvalidCameraRtspUrl(config.cameras);
    if (rtspError) {
      setError(rtspError);
      return;
    }

    try {
      setIsSaving(true);
      setError(null);
      setSavedMessage(null);
      const data = await saveOperationalConfig({
        user_id: user.id,
        organization_id: organizationId,
        config,
      });
      const nextBranches = data.branches || branches;
      setBootstrap(data);
      setBranches(nextBranches);
      setConfig(configFromBootstrap(data, nextBranches));
      if (refreshUser && user.id) await refreshUser(user.id);
      await refreshOrgConfig();
      setSavedMessage("Dashboard setup saved successfully.");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to save dashboard setup.",
      );
    } finally {
      setIsSaving(false);
    }
  };

  if (isLoading) {
    return (
      <div
        style={{
          minHeight: 360,
          display: "grid",
          placeItems: "center",
          color: C.primaryDark,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            fontWeight: 900,
          }}
        >
          <Loader2
            size={20}
            style={{ animation: "spin .8s linear infinite" }}
          />{" "}
          Loading settings…
          <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: "100%",
        background: C.bg,
        padding: 28,
        fontFamily: "'DM Sans','Inter','Segoe UI',sans-serif",
      }}
    >
      <div style={{ maxWidth: 1180, margin: "0 auto" }}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 18,
            marginBottom: 22,
          }}
        >
          <div>
            <h1
              style={{
                margin: "0 0 8px",
                fontSize: 28,
                color: C.primary,
                fontWeight: 950,
                letterSpacing: "-.03em",
              }}
            >
              Dashboard Setup
            </h1>
          </div>
          <button
            type="button"
            onClick={save}
            disabled={isSaving || hasInvalidCameraRtspUrl}
            style={{
              ...buttonStyle("primary"),
              minWidth: 150,
              opacity: isSaving || hasInvalidCameraRtspUrl ? 0.7 : 1,
            }}
          >
            {isSaving ? (
              <Loader2
                size={17}
                style={{ animation: "spin .8s linear infinite" }}
              />
            ) : (
              <Save size={17} />
            )}
            {isSaving ? "Saving…" : "Save Setup"}
          </button>
        </div>

        {error ? (
          <div
            style={cardStyle({
              borderColor: "#fecaca",
              background: "#fef2f2",
              padding: 14,
              marginBottom: 18,
              color: C.danger,
              fontSize: 13,
              fontWeight: 800,
            })}
          >
            <AlertCircle
              size={16}
              style={{ verticalAlign: "middle", marginRight: 6 }}
            />{" "}
            {error}
          </div>
        ) : null}

        {savedMessage ? (
          <div
            style={cardStyle({
              borderColor: "#bbf7d0",
              background: "#f0fdf4",
              padding: 14,
              marginBottom: 18,
              color: C.success,
              fontSize: 13,
              fontWeight: 800,
            })}
          >
            <CheckCircle2
              size={16}
              style={{ verticalAlign: "middle", marginRight: 6 }}
            />{" "}
            {savedMessage}
          </div>
        ) : null}

        <div style={{ display: "grid", gap: 18 }}>
          <LicenseSettingsEditor />

          {plan.hasStudentSetup ? (
            <StudentStructureEditor
              branches={branches}
              groups={config.departments}
              setGroups={(updater) =>
                setConfig((prev) => ({
                  ...prev,
                  departments:
                    typeof updater === "function"
                      ? updater(prev.departments)
                      : updater,
                }))
              }
              terminology={terminology}
            />
          ) : null}

          {plan.hasWorkforceSetup ? (
            <WorkforceStructureEditor
              branches={branches}
              groups={config.departments}
              setGroups={(updater) =>
                setConfig((prev) => ({
                  ...prev,
                  departments:
                    typeof updater === "function"
                      ? updater(prev.departments)
                      : updater,
                }))
              }
              designations={config.roles}
              setDesignations={(updater) =>
                setConfig((prev) => ({
                  ...prev,
                  roles:
                    typeof updater === "function"
                      ? updater(prev.roles)
                      : updater,
                }))
              }
              terminology={terminology}
            />
          ) : null}

          <ShiftSchedulingEditor
            activePeopleTypes={activePeopleTypes}
            shiftEnabledPeopleTypes={config.shiftEnabledPeopleTypes}
            setShiftEnabledPeopleTypes={(updater) =>
              setConfig((prev) => ({
                ...prev,
                shiftEnabledPeopleTypes:
                  typeof updater === "function"
                    ? (updater(prev.shiftEnabledPeopleTypes) as string[])
                    : (updater as string[]),
              }))
            }
            terminology={terminology}
          />

          <CameraSettingsEditor
            branches={branches}
            organization={bootstrap?.organization}
            cameras={config.cameras}
            setCameras={(updater) =>
              setConfig((prev) => ({
                ...prev,
                cameras:
                  typeof updater === "function"
                    ? updater(prev.cameras)
                    : updater,
              }))
            }
            network={config.network}
            setNetwork={(updater) =>
              setConfig((prev) => ({
                ...prev,
                network:
                  typeof updater === "function"
                    ? updater(prev.network)
                    : updater,
              }))
            }
            terminology={terminology}
          />

          <ImportDataSection branches={branches} />

          <ArchivedStaffRetentionEditor
            branches={branches}
            config={config}
            setConfig={setConfig}
            terminology={terminology}
          />
        </div>
      </div>
    </div>
  );
}