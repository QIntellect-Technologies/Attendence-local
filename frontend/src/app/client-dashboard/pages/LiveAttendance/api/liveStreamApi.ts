/**
 * liveStreamApi.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Backend-only API layer for live attendance, live CCTV, cameras, detections,
 * profile photos, and live-tracking view models.
 *
 * Rules:
 * - No React import.
 * - No JSX.
 * - No component code.
 * - No mock data.
 * - No direct UI logic.
 */

export type CameraStatus = "Online" | "Normal" | "Alert" | "Offline";
export type LiveSourceStatus = "ready" | "loading" | "error";

/**
 * Thrown by every liveStream API call on a non-2xx response.
 *
 * `status` lets callers decide whether a failure is worth retrying (503 is
 * transient, 401/404 are not). `friendly` is the only field safe to render;
 * `message` may carry a server detail string meant for the console.
 */
export class LiveCctvError extends Error {
  readonly status: number;
  readonly friendly: string;

  constructor(status: number, friendly: string, detail?: string) {
    super(detail || friendly);
    this.name = "LiveCctvError";
    this.status = status;
    this.friendly = friendly;
  }
}

/**
 * Maps an HTTP status to text an admin can act on. Says nothing about status
 * codes or internals — the raw "Live CCTV request failed: 400 Bad Request"
 * string this replaces was being rendered straight into the dashboard.
 */
function friendlyFor(status: number): string {
  if (status === 401 || status === 403) {
    return "Your session expired. Sign in again to view camera feeds.";
  }
  if (status === 404) return "No camera feed is configured for this branch.";
  if (status === 400) return "Camera feed unavailable for this branch.";
  if (status === 429)
    return "Too many requests. Live updates will resume shortly.";
  if (status >= 500) return "Camera service is temporarily unavailable.";
  return "Camera feed unavailable.";
}

export interface LiveCamera {
  id: string;
  branchId: number | string;
  backendBranchId?: string | null;
  branchName: string;
  name: string;
  cameraName: string;
  location: string;
  status: CameraStatus;
  lastSeen: string | null;

  /**
   * Backend MJPEG URL used directly in <img src>.
   * Example: /api/stream/cam_1_main
   */
  streamUrl: string;
  streamPath: string;

  /**
   * Optional only for legacy compatibility.
   * Production UI should not depend on RTSP URLs.
   */
  rtspUrl?: string | null;
}

export interface LiveDetection {
  key: string;
  name: string;
  confidence: number;
  timestamp: string | null;
  source: string | null;
  userId: string | number | null;
  department: string | null;
  designation: string | null;
  cameraId: string | null;
  cameraName?: string | null;
  branchId: number | string | null;
  branchName: string | null;
  faceCrop?: string | null;
  /**
   * "checked_in" | "checked_out" — drives the card's badge in
   * LiveAttendanceMarker.tsx. Defaults to "checked_in" display when
   * absent (older backend responses that predate this field).
   */
  status?: "checked_in" | "checked_out" | null;
  /**
   * "late" when a check-in was confirmed after the shift's check-in grace
   * window had already closed (local_db.record_attendance_local's
   * check_in_hold_reason). null/undefined means on-time (or nothing to
   * compare against). Independent of `status`, which stays "checked_in"
   * either way — this only adds the lateness signal that used to be
   * dropped before reaching the frontend.
   */
  checkInHoldReason?: "late" | null;
}

export interface LiveStats {
  enrolledCount: number | null;
  presentCount: number | null;
  totalLogs: number | null;
}

export interface LiveTrackingPerson {
  id: string;
  name: string;
  personType?: string | null;
  personCode?: string | null;
  employeeId?: string | null;
  location: string;
  cameraName: string;
  building: string;
  pose: string;
  lastSeen: string;
  timestamp?: string | null;
  detectedAt?: string | null;
  confidence?: number | null;
  status: "Active" | "Idle";
  duty: string;
  groupName?: string | null;
  subGroupName?: string | null;
  className?: string | null;
  sectionName?: string | null;
  department?: string | null;
  position?: string | null;
  designation?: string | null;
  branchId: number | string;
  backendBranchId?: string | null;
  branchName: string;
  cameraId: string;
  fallbackMarker?: boolean;
}

export type LiveTrackingEmployee = LiveTrackingPerson;

export interface LiveTrackingCamera {
  id: string;
  cameraName: string;
  location: string;
  branchId: number | string;
  branchName: string;
  /** "Unknown" = the camera has not reported recently enough to trust any
   *  of the fields below. Never collapse it into "Online": an unreported
   *  camera and an empty room are indistinguishable at the UI otherwise. */
  status: "Online" | "Alert" | "Offline" | "Unknown";
  /** null when there is no live detection feed for this camera, so the
   *  count is not a measurement and must not be rendered as one. */
  activeDetections: number | null;
  localNodeOffline?: boolean;
  lastHeartbeat?: string | null;
}

export interface LiveCCTVViewModel {
  employees: LiveTrackingPerson[];
  persons?: LiveTrackingPerson[];
  cameras: LiveTrackingCamera[];
  registeredCount: number;
  activeFeedCount: number;
  activeNowCount: number;
  sourceStatus: LiveSourceStatus;
  sourceLabel: string;
  localNodeStatus?: {
    online: boolean;
    lastHeartbeat?: string | null;
    thresholdSeconds?: number;
  };
}

interface RawStatsResponse {
  total_users?: number;
  total_staff?: number;
  enrolled_users?: number;
  today_attendance?: number;
  unique_users_today?: number;
  total_logs?: number;
}

interface RawCamera {
  organization_id?: string | number;
  organizationId?: string | number;
  org_id?: string | number;
  backend_branch_id?: string | null;
  backendBranchId?: string | null;
  id?: string;
  camera_id?: string;
  branch_id?: number | string;
  branchId?: number | string;
  branch_name?: string;
  branchName?: string;
  name?: string;
  camera_name?: string;
  cameraName?: string;
  location?: string;
  status?: string;
  last_seen?: string | null;
  lastSeen?: string | null;
  stream_url?: string;
  streamUrl?: string;
  stream_path?: string;
  streamPath?: string;
  rtsp_url?: string | null;
  rtspUrl?: string | null;
}

interface RawDetection {
  backend_branch_id?: string | null;
  backendBranchId?: string | null;
  name?: string;
  confidence?: number;
  timestamp?: string;
  source?: string;
  user_id?: number | string;
  userId?: number | string;
  department?: string;
  designation?: string;
  camera_id?: string;
  cameraId?: string;
  camera_name?: string | null;
  cameraName?: string | null;
  branch_id?: number | string;
  branchId?: number | string;
  branch_name?: string;
  branchName?: string;
  face_crop?: string;
  faceCrop?: string;
  status?: string;
  check_in_hold_reason?: string | null;
}

interface RawDetectionsResponse {
  detections?: RawDetection[];

}

interface LocalLiveEvent {
  id?: string;
  name?: string;
  status?: string;
  confidence?: number;
  marked_at?: string;
  camera_id?: string | null;
  camera_name?: string | null;
  department?: string | null;
  designation?: string | null;
  staff_id?: string | null;
  snapshot?: string | null;
  check_in_hold_reason?: string | null;
}

interface LocalLiveEventsResponse {
  events?: LocalLiveEvent[];
  enrolled_count?: number;

}

export interface ScopeParams {
  organizationId?: number | string | null;
  branchId?: number | string | null;
  /**
   * Optional vertical people-type filter (e.g. "student", "staff"). Cameras
   * stay type-agnostic (hardware, not people) and never filter on this —
   * only detections, stats, and CCTV tracking data do.
   */
  peopleType?: string | null;
}

export interface DetectionParams extends ScopeParams {
  cameraIds?: string[];
}

const configuredApiBase = (
  (import.meta.env.VITE_API_BASE_URL as string | undefined) || "/api"
).replace(/\/$/, "");
const API_BASE = configuredApiBase.endsWith("/api")
  ? configuredApiBase
  : `${configuredApiBase}/api`;

export const IS_LOCAL_NODE_MODE =
  (import.meta.env.VITE_DEPLOYMENT_MODE as string | undefined) === "local" ||
  import.meta.env.MODE === "localnode";

// Same storage key as apiClient.ts's AUTH_TOKEN_STORAGE_KEY — deliberately
// duplicated as a plain string constant rather than imported, matching
// that file's own stated convention for keeping modules dependency-free of
// each other. If the key ever changes, it must change in both places —
// grep "dashboardAuthToken" before renaming either one.
const AUTH_TOKEN_STORAGE_KEY = "dashboardAuthToken";

function authHeaders(): HeadersInit {
  try {
    const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

function hasCameraIds(
  params?: ScopeParams | DetectionParams,
): params is DetectionParams & { cameraIds: string[] } {
  return (
    Array.isArray((params as DetectionParams | undefined)?.cameraIds) &&
    (params as DetectionParams).cameraIds!.length > 0
  );
}

function buildQuery(params?: ScopeParams | DetectionParams): string {
  const query = new URLSearchParams();

  if (params?.organizationId !== undefined && params.organizationId !== null) {
    query.set("organization_id", String(params.organizationId));
  }

  if (params?.branchId !== undefined && params.branchId !== null) {
    query.set("branch_id", String(params.branchId));
  }

  if (params?.peopleType) {
    query.set("people_type", params.peopleType);
  }

  if (hasCameraIds(params)) {
    query.set("camera_ids", params.cameraIds.join(","));
  }

  return query.toString();
}

async function requestJson<T>(
  path: string,
  signal?: AbortSignal,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  Object.entries(authHeaders()).forEach(([key, value]) => {
    headers.set(key, value as string);
  });

  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    signal,
    credentials: "same-origin",
    cache: "no-store",
    headers,
  });

  if (!response.ok) {
    let detail: string | undefined;
    try {
      // The backend returns { error, message } on failure; `message` is for
      // the console only, never for the UI.
      const body = await response.json();
      detail = typeof body?.message === "string" ? body.message : undefined;
    } catch {
      // Non-JSON body (HTML error page, empty 502) — friendlyFor covers it.
    }

    throw new LiveCctvError(
      response.status,
      friendlyFor(response.status),
      detail,
    );
  }

  return response.json() as Promise<T>;
}

function toNumberOrString(value: unknown): number | string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number" && Number.isFinite(value)) return value;

  const text = String(value).trim();
  if (!text) return null;

  const numeric = Number(text);
  if (Number.isFinite(numeric) && String(numeric) === text) return numeric;

  return text;
}

function toUiBranchId(value: unknown): number | string {
  return toNumberOrString(value) ?? 0;
}

function isSupabaseLikeId(value: unknown): boolean {
  return typeof value === "string" && /[a-f0-9-]{16,}/i.test(value);
}

function normalizeStatus(value: string | undefined): CameraStatus {
  const raw = String(value ?? "Online").toLowerCase();

  if (raw.includes("offline")) return "Offline";
  if (raw.includes("alert")) return "Alert";
  if (raw.includes("normal")) return "Normal";

  return "Online";
}

function normalizeCamera(raw: RawCamera): LiveCamera {
  const id = String(raw.id ?? raw.camera_id ?? "");
  const branchId = toUiBranchId(raw.branchId ?? raw.branch_id ?? 0);
  const backendBranchId =
    raw.backendBranchId ??
    raw.backend_branch_id ??
    (isSupabaseLikeId(raw.branch_id)
      ? String(raw.branch_id)
      : typeof raw.branchId === "string" && isSupabaseLikeId(raw.branchId)
        ? String(raw.branchId)
        : null);

  const organizationId =
    raw.organizationId ?? raw.organization_id ?? raw.org_id;

  const generatedStreamPath = `/stream/${encodeURIComponent(id)}`;
  const streamPath = raw.streamPath ?? raw.stream_path ?? generatedStreamPath;
  const streamUrl = new URL(`${API_BASE}${streamPath}`, window.location.origin);

  if (organizationId !== undefined && organizationId !== null) {
    streamUrl.searchParams.set("organization_id", String(organizationId));
  }

  const backendStreamUrl =
    raw.streamUrl ??
    raw.stream_url ??
    `${streamUrl.pathname}${streamUrl.search}`;

  return {
    id,
    branchId,
    backendBranchId,
    branchName:
      raw.branchName ??
      raw.branch_name ??
      (branchId ? `Branch ${branchId}` : ""),
    name: raw.name ?? raw.cameraName ?? raw.camera_name ?? "Camera",
    cameraName: raw.cameraName ?? raw.camera_name ?? raw.name ?? "Camera",
    location: raw.location ?? "Unassigned",
    status: normalizeStatus(raw.status),
    lastSeen: raw.lastSeen ?? raw.last_seen ?? null,
    streamPath,
    streamUrl: backendStreamUrl,
    rtspUrl: raw.rtspUrl ?? raw.rtsp_url ?? null,
  };
}

function mapStats(raw: RawStatsResponse): LiveStats {
  return {
    enrolledCount: raw.enrolled_users ?? 0,
    presentCount: raw.unique_users_today ?? raw.today_attendance ?? 0,
    totalLogs: raw.total_logs ?? 0,
  };
}

function mapDetection(raw: RawDetection, index: number): LiveDetection {
  const userId = toNumberOrString(raw.userId ?? raw.user_id);
  const timestamp = raw.timestamp ?? null;
  const source = raw.source ?? null;
  const cameraId = raw.cameraId ?? raw.camera_id ?? null;
  const cameraName = raw.cameraName ?? raw.camera_name ?? null;
  const branchId = toNumberOrString(raw.branchId ?? raw.branch_id);

  const key =
    userId !== null && timestamp
      ? `${userId}_${timestamp}_${cameraId ?? source ?? index}`
      : `${raw.name ?? "unknown"}_${cameraId ?? source ?? "camera"}_${index}`;

  const normalizedStatus = String(raw.status ?? "")
    .trim()
    .toLowerCase()
    .replace(/[-\s]+/g, "_");
  const status =
    normalizedStatus === "checked_out" || normalizedStatus === "checkout"
      ? "checked_out"
      : "checked_in";

  return {
    key,
    name: raw.name ?? "Unknown",
    confidence: Number(raw.confidence ?? 0),
    timestamp,
    source,
    userId,
    department: raw.department ?? null,
    designation: raw.designation ?? null,
    cameraId,
    cameraName,
    branchId,
    branchName: raw.branchName ?? raw.branch_name ?? null,
    faceCrop: raw.faceCrop ?? raw.face_crop ?? null,
    status,
    checkInHoldReason:
      String(raw.check_in_hold_reason ?? "").toLowerCase() === "late"
        ? "late"
        : null,
  };
}

export async function fetchLiveCameras(
  params: ScopeParams,
  signal?: AbortSignal,
): Promise<LiveCamera[]> {
  const query = buildQuery(params);

  const payload = await requestJson<RawCamera[] | { cameras?: RawCamera[] }>(
    `/cameras${query ? `?${query}` : ""}`,
    signal,
  );

  const raw = Array.isArray(payload) ? payload : (payload.cameras ?? []);
  return raw.map(normalizeCamera).filter((camera) => camera.id);
}

export async function fetchLiveStats(
  params: ScopeParams = {},
  signal?: AbortSignal,
): Promise<LiveStats> {
  if (IS_LOCAL_NODE_MODE) {
    const payload = await requestJson<LocalLiveEventsResponse>(
      "/live-events",
      signal,
    );
    const events = payload.events ?? [];
    const today = new Date().toDateString();
    const present = new Set(
      events
        .filter(
          (event) =>
            event.marked_at &&
            new Date(event.marked_at).toDateString() === today,
        )
        .map((event) => event.staff_id || event.name),
    );
    return {
      enrolledCount: payload.enrolled_count ?? 0,
      presentCount: present.size,
      totalLogs: events.length,
    };
  }

  const query = buildQuery(params);

  const raw = await requestJson<RawStatsResponse>(
    `/stats${query ? `?${query}` : ""}`,
    signal,
  );

  return mapStats(raw);
}

export async function fetchLiveDetections(
  params: DetectionParams = {},
  signal?: AbortSignal,
): Promise<LiveDetection[]> {
  if (IS_LOCAL_NODE_MODE) {
    const payload = await requestJson<LocalLiveEventsResponse>(
      "/live-events",
      signal,
    );
    return (payload.events ?? []).map((event, index) =>
      mapDetection(
        {
          name: event.name,
          confidence: event.confidence,
          timestamp: event.marked_at,
          source: "local_node",
          user_id: event.staff_id ?? undefined,
          camera_id: event.camera_id ?? undefined,
          camera_name: event.camera_name ?? undefined,
          department: event.department ?? undefined,
          designation: event.designation ?? undefined,
          status: event.status,
          face_crop: event.snapshot ?? undefined,
          check_in_hold_reason: event.check_in_hold_reason ?? undefined,
        },
        index,
      ),
    );
  }

  const query = buildQuery(params);

  const raw = await requestJson<RawDetectionsResponse>(
    `/live-detections${query ? `?${query}` : ""}`,
    signal,
  );

  return (raw.detections ?? []).map(mapDetection);
}

export async function fetchLiveCCTVTracking(
  params: ScopeParams,
  signal?: AbortSignal,
): Promise<LiveCCTVViewModel> {
  const query = buildQuery(params);

  return requestJson<LiveCCTVViewModel>(
    `/cctv/live-tracking${query ? `?${query}` : ""}`,
    signal,
  );
}

export async function initAiEngine(signal?: AbortSignal): Promise<void> {
  await requestJson<{ status: string }>("/init", signal, {
    method: "POST",
  });
}

/**
 * Explicit "press start" call — starts RTSP capture + detection on the
 * backend for every camera in scope, mirroring local_node's manual
 * start/stop model instead of the old lazy-start-on-first-<img> behavior.
 * Call this BEFORE opening any camera <img> tiles; the backend can still
 * lazy-start as a safety net if this is skipped, but the dashboard should
 * always call it so "Start" actually means something server-side.
 */
export async function startCameraStream(
  branchId?: string,
  signal?: AbortSignal,
): Promise<{ started: string[] }> {
  if (IS_LOCAL_NODE_MODE) return { started: [] };

  return requestJson<{ success: boolean; started: string[] }>(
    "/stream/start",
    signal,
    {
      method: "POST",
      body: JSON.stringify(branchId ? { branch_id: branchId } : {}),
    },
  );
}

/**
 * Explicit "press stop" call. Omit cameraId to stop every camera in
 * branchId's scope (or the whole org if branchId is also omitted) — used
 * by the page-level Stop button. Pass cameraId to stop just one tile.
 */
export async function stopCameraStream(
  options: { branchId?: string; cameraId?: string } = {},
  signal?: AbortSignal,
): Promise<{ stopped: string[] }> {
  if (IS_LOCAL_NODE_MODE) return { stopped: [] };

  const body: Record<string, string> = {};
  if (options.cameraId) body.camera_id = options.cameraId;
  if (options.branchId) body.branch_id = options.branchId;

  return requestJson<{ success: boolean; stopped: string[] }>(
    "/stream/stop",
    signal,
    {
      method: "POST",
      body: JSON.stringify(body),
    },
  );
}

export interface ImportEmbeddingResult {
  syncedCount: number;
  packageId: string;
  branchLabel: string;
  results: Array<{
    peopleType: string;
    personCode: string | null;
    status: string;
    reason?: string;
    embeddingCount?: number;
  }>;
}

interface RawImportEmbeddingResult {
  people_type?: string;
  person_code?: string | null;
  status?: string;
  reason?: string;
  embedding_count?: number;
}

interface RawImportEmbeddingResponse {
  success?: boolean;
  synced_count?: number;
  package_id?: string;
  branch_label?: string;
  results?: RawImportEmbeddingResult[];
  message?: string;
  error?: string;
}

/**
 * Maps an import failure's HTTP status to a message for the notice shown
 * under the button. Distinct from friendlyFor (which is written for
 * camera-feed failures and would read oddly here, e.g. "No camera feed is
 * configured for this branch" for a rejected zip) — the backend already
 * returns a specific `message` for almost every failure mode (bad zip,
 * checksum mismatch, no matching person), so this is only the fallback
 * for the rare case there isn't one.
 */
function importFriendlyFor(status: number): string {
  if (status === 401 || status === 403) {
    return "Your session expired, or this account isn't an admin. Sign in again as an admin to import a package.";
  }
  if (status === 413) return "That package is too large to upload.";
  if (status >= 500) return "Import failed on the server. Try again shortly.";
  return "Import failed. Check the package and try again.";
}

/**
 * Uploads a trainer_desktop import_package.zip to the cloud-mode "Import
 * embeddings" endpoint (/api/dashboard/embeddings/import), scoped to one
 * branch. Deliberately does NOT go through requestJson: the body here is
 * FormData, and requestJson stamps `Content-Type: application/json` onto
 * any request body that doesn't already carry a Content-Type header —
 * that would overwrite the multipart boundary the browser needs to set
 * itself, and the backend would receive an unparseable body.
 *
 * branchId must be a real Supabase branch id (UseLiveStreamReturn's
 * backendBranchId, not the UI's numeric activeBranchId) — the backend
 * endpoint accepts branch_id as an optional narrowing filter, but for
 * this UI it's required: importing with no branch would match ANY
 * branch in the org sharing the same person_code, which is never what
 * "import package" from one specific branch's live-attendance page
 * should do. Callers must reject a missing/empty branchId before
 * calling this rather than relying on it to guard that itself.
 */
export async function importEmbeddingPackage(
  file: File,
  branchId?: string,
  signal?: AbortSignal,
  localImport = false,
): Promise<ImportEmbeddingResult> {
  const body = new FormData();
  body.append("package", file);

  if (IS_LOCAL_NODE_MODE || localImport) {
    const response = await fetch(`${API_BASE}/import-embeddings`, {
      method: "POST",
      signal,
      credentials: "same-origin",
      cache: "no-store",
      headers: authHeaders(),
      body,
    });

    const payload = (await response.json().catch(() => null)) as {
      success?: boolean;
      imported?: number;
      skipped?: number;
      errors?: string[];
      message?: string;
    } | null;

    if (!response.ok || payload?.success === false) {
      const detail = payload?.message || "Local embedding import failed.";
      throw new LiveCctvError(response.status, detail, detail);
    }

    const imported = payload?.imported ?? 0;
    const skipped = payload?.skipped ?? 0;
    return {
      syncedCount: imported,
      packageId: "local-node",
      branchLabel: "Local node",
      results: Array.from({ length: imported + skipped }, (_, index) => ({
        peopleType: "staff",
        personCode: String(index + 1),
        status: index < imported ? "synced" : "skipped",
      })),
    };
  }

  if (!branchId) {
    throw new LiveCctvError(
      400,
      "Select a branch before importing a cloud package.",
    );
  }
  body.append("branch_id", branchId);

  const headers = new Headers();
  headers.set("Accept", "application/json");
  Object.entries(authHeaders()).forEach(([key, value]) => {
    headers.set(key, value as string);
  });

  const response = await fetch(`${API_BASE}/dashboard/embeddings/import`, {
    method: "POST",
    signal,
    credentials: "same-origin",
    cache: "no-store",
    headers,
    body,
  });

  let payload: RawImportEmbeddingResponse | undefined;
  try {
    payload = await response.json();
  } catch {
    // Non-JSON body (HTML error page, empty 502) — importFriendlyFor covers it.
  }

  if (!response.ok || payload?.success === false) {
    const detail =
      typeof payload?.message === "string"
        ? payload.message
        : typeof payload?.error === "string"
          ? payload.error
          : undefined;
    throw new LiveCctvError(
      response.status,
      detail || importFriendlyFor(response.status),
      detail,
    );
  }

  return {
    syncedCount: payload?.synced_count ?? 0,
    packageId: payload?.package_id ?? "",
    branchLabel: payload?.branch_label ?? "",
    results: (payload?.results ?? []).map((r) => ({
      peopleType: r.people_type ?? "staff",
      personCode: r.person_code ?? null,
      status: r.status ?? "unknown",
      reason: r.reason,
      embeddingCount: r.embedding_count,
    })),
  };
}

export function getCameraStreamUrl(camera: LiveCamera): string {
  if (IS_LOCAL_NODE_MODE) {
    return `/api/camera-stream/${encodeURIComponent(camera.id)}`;
  }

  return (
    camera.streamUrl ||
    `${API_BASE}/api/stream/${encodeURIComponent(camera.id)}`
  );
}

/**
 * Mints a short-lived (~60s) stream_token for one camera via an
 * authenticated fetch — the Bearer header this call sends is the only
 * proof-of-org-membership the backend accepts for that camera, since the
 * <img> tag that actually opens the MJPEG connection can't send one
 * itself. Call this immediately before first setting img.src, and again
 * periodically (~45s) to keep a fresh token on hand in case the browser
 * needs to reopen the connection (e.g. after a network blip).
 *
 * Throws on failure (404 = camera not in this org, 401 = session expired)
 * — callers should treat that the same as any other stream error rather
 * than silently leaving the tile on a stale/missing token.
 */
export async function fetchStreamToken(
  cameraId: string,
  signal?: AbortSignal,
): Promise<string> {
  if (IS_LOCAL_NODE_MODE) return "";

  const raw = await requestJson<{ success: boolean; stream_token: string }>(
    "/stream/token",
    signal,
    {
      method: "POST",
      body: JSON.stringify({ camera_id: cameraId }),
    },
  );
  return raw.stream_token;
}

/**
 * Builds the <img>-ready URL for a camera tile, given a freshly minted
 * stream_token. Distinct from getCameraStreamUrl (which returns the bare
 * path/URL with no token) so callers can't accidentally point an <img> at
 * the stream without one — GET /api/stream/<camera_id> 401s without a
 * valid stream_token query param.
 */
export function getAuthenticatedStreamUrl(
  camera: LiveCamera,
  streamToken: string,
): string {
  if (IS_LOCAL_NODE_MODE) return getCameraStreamUrl(camera);

  const base = getCameraStreamUrl(camera);
  const joiner = base.includes("?") ? "&" : "?";
  return `${base}${joiner}stream_token=${encodeURIComponent(streamToken)}`;
}

export function getUserPhotoUrl(userId: number | string): string;
export function getUserPhotoUrl(userId: null | undefined): undefined;
export function getUserPhotoUrl(
  userId: number | string | null | undefined,
): string | undefined {
  if (userId == null) return undefined;
  // /api/users/<int:user_id>/photo only resolves legacy integer user IDs —
  // a Supabase org's detections carry a UUID staff_id (from
  // cloud_recognition_worker.best_match / SUPABASE_EMBEDDING_CACHE), which
  // that route 404s on, silently falling back to the live face-crop
  // snapshot every time. /api/staff/<staff_id>/photo (same endpoint
  // StaffManagement already uses) accepts EITHER an int or a UUID, so route
  // through that one instead — see app.py's api_staff_photo.
  return `${API_BASE}/staff/${encodeURIComponent(String(userId))}/photo`;
}

export const profilePhotoUrl = getUserPhotoUrl;

/**
 * Backward-compatible aliases for older imports.
 */
function isAbortSignal(value: unknown): value is AbortSignal {
  return (
    typeof value === "object" &&
    value !== null &&
    "aborted" in value &&
    "addEventListener" in value
  );
}

/**
 * Backward-compatible typed wrappers.
 *
 * Supported:
 *   getLiveStats(signal)
 *   getLiveStats(params, signal)
 */
export function getLiveStats(signal?: AbortSignal): Promise<LiveStats>;
export function getLiveStats(
  params?: ScopeParams,
  signal?: AbortSignal,
): Promise<LiveStats>;
export function getLiveStats(
  first?: ScopeParams | AbortSignal,
  second?: AbortSignal,
): Promise<LiveStats> {
  if (isAbortSignal(first)) {
    return fetchLiveStats({}, first);
  }

  return fetchLiveStats(first ?? {}, second);
}

/**
 * Supported:
 *   getLiveDetections(signal)
 *   getLiveDetections(params, signal)
 */
export function getLiveDetections(
  signal?: AbortSignal,
): Promise<LiveDetection[]>;
export function getLiveDetections(
  params?: DetectionParams,
  signal?: AbortSignal,
): Promise<LiveDetection[]>;
export function getLiveDetections(
  first?: DetectionParams | AbortSignal,
  second?: AbortSignal,
): Promise<LiveDetection[]> {
  if (isAbortSignal(first)) {
    return fetchLiveDetections({}, first);
  }

  return fetchLiveDetections(first ?? {}, second);
}
