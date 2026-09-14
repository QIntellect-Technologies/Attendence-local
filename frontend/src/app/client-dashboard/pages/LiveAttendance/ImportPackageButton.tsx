import React, { useEffect, useRef, useState } from "react";
import { UploadCloud, Info, CheckCircle2, XCircle } from "lucide-react";
import { T } from "../../components/ui/theme";
import JellyButton from "../../components/ui/JellyButton";
import { useOrg } from "../../contexts/OrgConfigContext";
import {
  importEmbeddingPackage,
  IS_LOCAL_NODE_MODE,
  LiveCctvError,
} from "./api/liveStreamApi";

/**
 * ImportPackageButton.tsx
 * ─────────────────────────────────────────────────────────────────────────────
 * Header-level trigger for importing a trainer_desktop `import_package.zip`
 * straight into this branch's cloud embeddings.
 *
 * Wired to POST /api/dashboard/embeddings/import (support_db_internal.
 * import_embeddings_cloud_mode), the existing cloud-mode counterpart to
 * Local Node's own import — NOT a new endpoint, and NOT the local-node
 * path: the browser never talks to a branch's local node directly (that
 * would bypass the stream-token auth this whole page already uses for
 * camera feeds). This is an org-admin action (@require_client_dashboard_
 * admin on the backend), scoped to whichever single branch is active on
 * this page — there is no "import for all branches" here on purpose: a
 * package imported with no branch filter would match ANY branch in the
 * org sharing the same person_code, which is never what clicking this
 * button on one branch's live-attendance page should do.
 */

const NOTICE_AUTO_DISMISS_MS = 8000;

type NoticeState =
  | { kind: "no-branch" }
  | { kind: "importing" }
  | { kind: "success"; syncedCount: number; total: number }
  | { kind: "error"; message: string };

interface ImportPackageButtonProps {
  /** Supabase branch id for cloud imports. Local nodes are single-branch and
   *  import directly into their SQLite database without a branch selection. */
  branchId?: string;
}

export default function ImportPackageButton({
  branchId,
}: ImportPackageButtonProps) {
  const { cfg } = useOrg();
  const isLocalImport =
    IS_LOCAL_NODE_MODE ||
    String(cfg.attendanceMode ?? cfg.attendance_mode ?? "").toLowerCase() ===
      "local";
  const inputRef = useRef<HTMLInputElement>(null);
  const [notice, setNotice] = useState<NoticeState | null>(null);

  useEffect(() => {
    if (!notice || notice.kind === "importing") return;
    const id = window.setTimeout(() => setNotice(null), NOTICE_AUTO_DISMISS_MS);
    return () => window.clearTimeout(id);
  }, [notice]);

  const handleClick = () => {
    if (!isLocalImport && !branchId) {
      setNotice({ kind: "no-branch" });
      return;
    }
    inputRef.current?.click();
  };

  const handleFileChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    // Reset so selecting the same file again still fires onChange.
    event.target.value = "";
    if (!file || (!isLocalImport && !branchId)) return;

    setNotice({ kind: "importing" });
    try {
      const result = await importEmbeddingPackage(
        file,
        branchId,
        undefined,
        isLocalImport,
      );
      setNotice({
        kind: "success",
        syncedCount: result.syncedCount,
        total: result.results.length,
      });
    } catch (err) {
      const message =
        err instanceof LiveCctvError
          ? err.friendly
          : err instanceof Error
            ? err.message
            : "Import failed. Check the package and try again.";
      setNotice({ kind: "error", message });
    }
  };

  return (
    <div style={{ position: "relative", display: "inline-flex" }}>
      <input
        ref={inputRef}
        type="file"
        accept=".zip"
        style={{ display: "none" }}
        onChange={(event) => void handleFileChange(event)}
      />
      <JellyButton
        type="button"
        variant="ghost"
        size="md"
        leftIcon={<UploadCloud size={14} />}
        loading={notice?.kind === "importing"}
        onClick={handleClick}
      >
        {notice?.kind === "importing" ? "Importing…" : "Import package"}
      </JellyButton>

      {notice && notice.kind !== "importing" && (
        <div
          role="status"
          style={{
            position: "absolute",
            top: "calc(100% + 8px)",
            right: 0,
            zIndex: 20,
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            width: 280,
            padding: "10px 12px",
            borderRadius: 10,
            background:
              notice.kind === "success"
                ? "#f0fdf4"
                : notice.kind === "error"
                  ? "#fef2f2"
                  : T.amberBg,
            border: `1px solid ${
              notice.kind === "success"
                ? "#bbf7d0"
                : notice.kind === "error"
                  ? "#fecaca"
                  : T.amberBd
            }`,
            color:
              notice.kind === "success"
                ? "#15803d"
                : notice.kind === "error"
                  ? "#dc2626"
                  : T.amber,
            fontSize: 12,
            fontWeight: 600,
            lineHeight: 1.5,
            boxShadow: "0 8px 20px rgba(15,45,74,0.12)",
          }}
        >
          {notice.kind === "no-branch" && (
            <>
              <Info size={14} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>
                Select a single branch above before importing a package.
              </span>
            </>
          )}
          {notice.kind === "success" && (
            <>
              <CheckCircle2 size={14} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>
                Imported {notice.syncedCount} of {notice.total} record
                {notice.total === 1 ? "" : "s"}.
                {notice.syncedCount < notice.total
                  ? " Some records were skipped or rejected — check the import history for details."
                  : ""}
              </span>
            </>
          )}
          {notice.kind === "error" && (
            <>
              <XCircle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>{notice.message}</span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
