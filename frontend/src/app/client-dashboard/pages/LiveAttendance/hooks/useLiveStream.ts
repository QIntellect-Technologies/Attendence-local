/**
 * useLiveStream.ts
 * ─────────────────────────────────────────────────────────────────────────────
 * Production backend-first live attendance logic.
 *
 * Source of truth:
 *   /api/cameras
 *   /api/stats
 *   /api/live-detections
 *
 * Responsibilities:
 * - Apply organization + branch scope.
 * - Load backend cameras.
 * - Start/stop monitoring.
 * - Poll live detections and scoped attendance stats.
 * - Keep UI components pure and fetch-free.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOrg, resolveBackendBranchId } from "../../../contexts/OrgConfigContext";
import {
  fetchLiveCameras,
  fetchLiveDetections,
  fetchLiveStats,
  startCameraStream,
  stopCameraStream,
  type LiveCamera,
  type LiveDetection,
  type LiveStats,
} from "../api/liveStreamApi";
import {
  resolveActivePeopleTypes,
  peopleLabelForType,
} from "../../../utils/templateRendering";
import { IS_LOCAL_NODE_MODE } from "../api/liveStreamApi";

export interface UseLiveStreamReturn {
  isGlobalScope: boolean;
  /**
   * The Supabase branch id for whatever branch is currently selected, or
   * undefined when isGlobalScope is true (no single branch is active).
   * Same value CameraGrid/DetectionSidebar's own API calls are already
   * scoped by internally — exposed here so anything needing a
   * branch-specific write (e.g. ImportPackageButton) doesn't re-derive
   * cfg.branches.find(...) a second time and risk drifting from it.
   */
  backendBranchId: string | undefined;
  allCameras: LiveCamera[];
  streamableCameras: LiveCamera[];
  noCameras: boolean;
  noStreamable: boolean;

  streaming: boolean;
  startMonitoring: () => void;
  stopMonitoring: () => void;

  stats: LiveStats;
  detections: LiveDetection[];
  refreshCameras: () => Promise<void>;
  refreshDetections: () => Promise<void>;
  refreshStats: () => Promise<void>;

  liveCount: number;
  faceCount: number;
  matchCount: number;
  todayCount: number;

  loading: boolean;
  error: string | null;

  // Template awareness additions
  activePeopleTypes: string[];
  personLabel: { singular: string; plural: string };
  scopeLabel: string;
  isNodeOffline: boolean;
  hasOfflineDetections: boolean;

  // People-type filter (dropdown). null/"all" means every active type.
  // Cameras are hardware and are never scoped by this — only stats and
  // detections are.
  peopleTypeFilter: string | null;
  setPeopleTypeFilter: (type: string | null) => void;
}

const EMPTY_STATS: LiveStats = {
  enrolledCount: 0,
  presentCount: 0,
  totalLogs: 0,
};

const MAX_STREAM_DURATION_MS = 3 * 60 * 1_000;

function isStreamable(camera: LiveCamera): boolean {
  return Boolean(camera.streamUrl || camera.streamPath || camera.id);
}

export function useLiveStream(): UseLiveStreamReturn {
  const { activeBranchId, organizationId, isOrgReady, cfg } = useOrg();

  // Template awareness: resolve enabled people types
  const activePeopleTypes = useMemo(() => resolveActivePeopleTypes(cfg), [cfg]);
  const primaryPeopleType = activePeopleTypes[0] || "staff";

  const [peopleTypeFilter, setPeopleTypeFilterState] = useState<string | null>(
    null,
  );

  // personLabel drives the page title, the sidebar heading, and the "Total
  // X" stat label — it must track whatever the person-type dropdown is
  // currently set to, not just the org's first configured type. Previously
  // this was computed once from primaryPeopleType alone, so selecting
  // "Staff" in the dropdown left every one of those labels reading
  // "Students" (or whichever type happened to be first) no matter what was
  // selected. When the filter is "All" and the org has more than one active
  // type, combine both labels (e.g. "Students & Staff") rather than
  // silently picking one — showing only one type's name while the data
  // underneath covers both would be its own version of the same bug.
  const personLabel = useMemo(() => {
    if (peopleTypeFilter) {
      return peopleLabelForType(peopleTypeFilter, cfg);
    }
    if (activePeopleTypes.length > 1) {
      const labels = activePeopleTypes.map((type) =>
        peopleLabelForType(type, cfg),
      );
      return {
        singular: labels.map((l) => l.singular).join(" / "),
        plural: labels.map((l) => l.plural).join(" & "),
      };
    }
    return peopleLabelForType(primaryPeopleType, cfg);
  }, [peopleTypeFilter, activePeopleTypes, primaryPeopleType, cfg]);

  const [streaming, setStreaming] = useState(false);
  const [stats, setStats] = useState<LiveStats>(EMPTY_STATS);
  const [allCameras, setAllCameras] = useState<LiveCamera[]>([]);
  const [detections, setDetections] = useState<LiveDetection[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isNodeOffline, setIsNodeOffline] = useState(false);

  // If a support-side toggle removes the currently-selected type from scope
  // (e.g. org switched from "staff" to "student" while "staff" was selected
  // here), fall back to "all" rather than silently filtering everything out.
  useEffect(() => {
    if (peopleTypeFilter && !activePeopleTypes.includes(peopleTypeFilter)) {
      setPeopleTypeFilterState(null);
    }
  }, [activePeopleTypes, peopleTypeFilter]);

  const setPeopleTypeFilter = useCallback((type: string | null) => {
    setPeopleTypeFilterState(type && type !== "all" ? type : null);
  }, []);

  const camerasAbortRef = useRef<AbortController | null>(null);
  const detectionsAbortRef = useRef<AbortController | null>(null);
  const statsAbortRef = useRef<AbortController | null>(null);
  const suppressedDetectionUsersRef = useRef<Set<string>>(new Set());

  const isGlobalScope = activeBranchId === null;

  // Scope label for branch-aware messages
  const scopeLabel = useMemo(() => {
    if (isGlobalScope) return "All Branches";

    const branch = (cfg?.branches || []).find((item: any) => {
      return String(item.id) === String(activeBranchId);
    });

    return branch?.name || `Branch ${activeBranchId}`;
  }, [isGlobalScope, activeBranchId, cfg?.branches]);

  const backendBranchId = useMemo(() => {
    if (activeBranchId === null) return undefined;

    const branch = cfg.branches.find(
      (item) => String(item.id) === String(activeBranchId),
    );

    return resolveBackendBranchId(branch);
  }, [activeBranchId, cfg.branches]);

  // Cameras are physical hardware, not people — this scope intentionally
  // excludes peopleType so /api/cameras is never asked to filter by it.
  const scope = useMemo(
    () => ({
      organizationId,
      branchId: backendBranchId,
    }),
    [backendBranchId, organizationId],
  );

  // Stats and detections are people-scoped; this is the one that carries
  // the dropdown's current selection through to the backend.
  const filterScope = useMemo(
    () => ({
      ...scope,
      peopleType: peopleTypeFilter ?? undefined,
    }),
    [scope, peopleTypeFilter],
  );

  const streamableCameras = useMemo(
    () => allCameras.filter(isStreamable),
    [allCameras],
  );

  const noCameras = allCameras.length === 0;
  const noStreamable = allCameras.length > 0 && streamableCameras.length === 0;

  // Check if any detections are from fallback (offline node)
  const hasOfflineDetections = useMemo(
    () => detections.some((det) => det.source?.includes("fallback")),
    [detections],
  );

  const refreshCameras = useCallback(async () => {
    camerasAbortRef.current?.abort();

    if (!IS_LOCAL_NODE_MODE && (!isOrgReady || !organizationId)) {
      setAllCameras([]);
      return;
    }

    const controller = new AbortController();
    camerasAbortRef.current = controller;

    try {
      setLoading(true);
      const cameras = await fetchLiveCameras(scope, controller.signal);
      setAllCameras(cameras);
      setError(null);
    } catch (err) {
      if ((err as DOMException).name === "AbortError") return;

      // A failed fetch for the current scope must never leave the previous
      // scope's cameras on screen — e.g. switching to a branch with zero
      // cameras must not keep showing another branch's feed just because
      // this request errored instead of returning an empty list.
      setAllCameras([]);
      setError(
        err instanceof Error ? err.message : "Failed to load backend cameras.",
      );
    } finally {
      setLoading(false);
    }
  }, [isOrgReady, organizationId, scope]);

  const refreshStats = useCallback(async () => {
    statsAbortRef.current?.abort();

    if (!IS_LOCAL_NODE_MODE && (!isOrgReady || !organizationId)) {
      setStats(EMPTY_STATS);
      return;
    }

    const controller = new AbortController();
    statsAbortRef.current = controller;

    try {
      setLoading(true);
      const nextStats = await fetchLiveStats(filterScope, controller.signal);
      setStats(nextStats);
      setError(null);
    } catch (err) {
      if ((err as DOMException).name === "AbortError") return;

      setError(
        err instanceof Error ? err.message : "Failed to load attendance stats.",
      );
    } finally {
      setLoading(false);
    }
  }, [isOrgReady, organizationId, filterScope]);

  const refreshDetections = useCallback(async () => {
    detectionsAbortRef.current?.abort();

    if (!IS_LOCAL_NODE_MODE && (!isOrgReady || !organizationId)) {
      setDetections([]);
      return;
    }

    const controller = new AbortController();
    detectionsAbortRef.current = controller;

    try {
      const nextDetections = await fetchLiveDetections(
        {
          ...filterScope,
          cameraIds: streamableCameras.map((camera) => camera.id),
        },
        controller.signal,
      );

      setDetections(
        nextDetections.filter((detection) => {
          const identifiers = [detection.userId, detection.name].filter(
            (value) => value !== null && value !== undefined,
          );
          return !identifiers.some((value) =>
            suppressedDetectionUsersRef.current.has(String(value)),
          );
        }),
      );
      setError(null);
    } catch (err) {
      if ((err as DOMException).name === "AbortError") return;

      setError(
        err instanceof Error ? err.message : "Failed to load live detections.",
      );
    }
  }, [isOrgReady, organizationId, filterScope, streamableCameras]);

  const startMonitoring = useCallback(() => {
    if (noCameras || noStreamable) return;

    // "Press start" — tells the backend to actually open RTSP capture +
    // detection for these cameras (local_node's manual start/stop model),
    // rather than relying only on the old lazy-start-on-first-<img>
    // fallback. Fired before flipping `streaming` (which is what makes
    // CameraGrid render the <img> tiles) so capture has a head start, but
    // a failure here doesn't block the UI — video_stream still lazy-starts
    // server-side if this call didn't land.
    void startCameraStream(backendBranchId).catch((err) => {
      console.error("Failed to start camera stream on backend:", err);
    });

    setStreaming(true);
    void refreshStats();
    void refreshDetections();
  }, [
    noCameras,
    noStreamable,
    backendBranchId,
    refreshDetections,
    refreshStats,
  ]);

  const stopMonitoring = useCallback(() => {
    setStreaming(false);
    detectionsAbortRef.current?.abort();

    void stopCameraStream({ branchId: backendBranchId }).catch((err) => {
      console.error("Failed to stop camera stream on backend:", err);
    });
  }, [backendBranchId]);

  useEffect(() => {
    if (!streaming) return;

    const timeoutId = window.setTimeout(() => {
      stopMonitoring();
    }, MAX_STREAM_DURATION_MS);

    return () => window.clearTimeout(timeoutId);
  }, [stopMonitoring, streaming]);

  useEffect(() => {
    void refreshCameras();
    void refreshStats();

    const id = window.setInterval(() => {
      void refreshCameras();
      void refreshStats();
    }, 30_000);

    return () => window.clearInterval(id);
  }, [refreshCameras, refreshStats]);

  useEffect(() => {
    void refreshDetections();
    const id = window.setInterval(() => {
      void refreshDetections();
    }, 1_500);

    return () => window.clearInterval(id);
  }, [refreshDetections]);

  useEffect(() => {
    const handleAttendanceChanged = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          userId?: string | number;
          personCode?: string | number;
          staffName?: string;
        }>
      ).detail;
      const identifiers = [
        detail?.userId,
        detail?.personCode,
        detail?.staffName,
      ]
        .filter((value) => value !== undefined && value !== null)
        .map((value) => String(value));
      if (identifiers.length > 0) {
        identifiers.forEach((identifier) =>
          suppressedDetectionUsersRef.current.add(identifier),
        );
        setDetections((current) =>
          current.filter(
            (detection) =>
              ![detection.userId, detection.name]
                .filter((value) => value !== null && value !== undefined)
                .some((value) => identifiers.includes(String(value))),
          ),
        );
      }
      void refreshStats();
      void refreshDetections();
    };

    window.addEventListener("attendance:changed", handleAttendanceChanged);
    return () =>
      window.removeEventListener("attendance:changed", handleAttendanceChanged);
  }, [refreshDetections, refreshStats]);

  useEffect(() => {
    if (streamableCameras.length === 0) setStreaming(false);
  }, [streamableCameras.length]);

  useEffect(() => {
    return () => {
      camerasAbortRef.current?.abort();
      detectionsAbortRef.current?.abort();
      statsAbortRef.current?.abort();
    };
  }, []);

  const liveCount = detections.length;
  const matchCount = detections.filter(
    (item) => item.name !== "Unknown",
  ).length;
  const todayCount = stats.presentCount ?? 0;
  const faceCount = liveCount;

  return {
    isGlobalScope,
    backendBranchId,
    allCameras,
    streamableCameras,
    noCameras,
    noStreamable,

    streaming,
    startMonitoring,
    stopMonitoring,

    stats,
    detections,
    refreshCameras,
    refreshDetections,
    refreshStats,

    liveCount,
    faceCount,
    matchCount,
    todayCount,

    loading,
    error,

    // Template awareness and offline detection additions
    activePeopleTypes,
    personLabel,
    scopeLabel,
    isNodeOffline,
    hasOfflineDetections,

    peopleTypeFilter,
    setPeopleTypeFilter,
  };
}