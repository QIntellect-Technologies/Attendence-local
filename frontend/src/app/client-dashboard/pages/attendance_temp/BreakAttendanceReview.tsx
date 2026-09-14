import React, { useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle,
  Clock3,
  Pencil,
  X,
} from "lucide-react";
import {
  updateBreakAttendance,
  type BreakAttendanceRecord,
} from "./api/attendanceApi";
import {
  fromDatetimeLocalValue,
  T,
  toDatetimeLocalValue,
} from "./utils/attendanceDisplay";

interface Props {
  date: string;
  records: BreakAttendanceRecord[];
  loading: boolean;
  error: string | null;
  timezone: string;
  onRefresh: () => Promise<void> | void;
}

function formatTime(
  value: string | null | undefined,
  timezone: string,
): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: timezone,
  });
}

function statusStyle(status: string): {
  color: string;
  background: string;
  label: string;
} {
  if (status === "returned") {
    return { color: T.green600, background: T.green100, label: "Returned" };
  }
  if (status === "missing_return") {
    return { color: T.red600, background: T.red100, label: "Missing return" };
  }
  return { color: T.amber600, background: T.amber100, label: "Outside" };
}

function isValidCorrection(outside: string, returned: string): boolean {
  if (!outside) return false;
  if (!returned) return true;
  return new Date(outside).getTime() <= new Date(returned).getTime();
}

export default function BreakAttendanceReview({
  date,
  records,
  loading,
  error,
  timezone,
  onRefresh,
}: Props) {
  const returned = records.filter(
    (record) => record.status === "returned",
  ).length;
  const missing = records.filter(
    (record) => record.status === "missing_return",
  ).length;
  const [editingId, setEditingId] = useState<number | null>(null);
  const [outsideDraft, setOutsideDraft] = useState("");
  const [returnedDraft, setReturnedDraft] = useState("");
  const [reasonDraft, setReasonDraft] = useState("");
  const [savingId, setSavingId] = useState<number | null>(null);
  const [editError, setEditError] = useState<string | null>(null);

  const beginEdit = (record: BreakAttendanceRecord) => {
    setEditingId(record.id);
    setOutsideDraft(toDatetimeLocalValue(record.outside_at, timezone));
    setReturnedDraft(toDatetimeLocalValue(record.returned_at, timezone));
    setReasonDraft(record.correction_reason ?? "");
    setEditError(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditError(null);
  };

  const saveEdit = async () => {
    if (editingId === null) return;
    if (!isValidCorrection(outsideDraft, returnedDraft)) {
      setEditError("Return time must be after the outside time.");
      return;
    }
    const outsideAt = fromDatetimeLocalValue(outsideDraft, timezone);
    const returnedAt = fromDatetimeLocalValue(returnedDraft, timezone);
    if (!outsideAt) {
      setEditError("Enter a valid outside time.");
      return;
    }
    setSavingId(editingId);
    setEditError(null);
    try {
      await updateBreakAttendance(editingId, {
        outside_at: outsideAt,
        returned_at: returnedAt,
        correction_reason: reasonDraft.trim() || null,
      });
      setEditingId(null);
      await onRefresh();
    } catch (error) {
      setEditError(
        error instanceof Error ? error.message : "Failed to save correction.",
      );
    } finally {
      setSavingId(null);
    }
  };

  return (
    <section
      className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden mt-6"
      aria-labelledby="break-attendance-review-heading"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-4 border-b border-gray-100">
        <div>
          <h3
            id="break-attendance-review-heading"
            className="text-sm font-semibold"
            style={{ color: "#1a699f" }}
          >
            Break / Namaz Review
          </h3>
          <p className="text-xs text-gray-400 mt-1">
            {date} · {records.length} periods · {returned} returned · {missing}{" "}
            missing return
          </p>
        </div>
      </div>

      {error && (
        <div
          className="px-6 py-4 text-sm text-red-700 bg-red-50 border-b border-red-100"
          role="alert"
        >
          {error}
        </div>
      )}

      {loading && records.length === 0 ? (
        <div className="px-6 py-8 text-sm text-gray-400">
          Loading break attendance…
        </div>
      ) : records.length === 0 ? (
        <div className="px-6 py-8 text-sm text-gray-400 flex items-center gap-2">
          <Clock3 size={16} /> No break periods recorded for this date.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px]">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-100">
                {[
                  "Employee",
                  "Break / Namaz",
                  "Period",
                  "Outside",
                  "Returned",
                  "Duration",
                  "Status",
                  "Correction Reason",
                  "Action",
                ].map((label) => (
                  <th
                    key={label}
                    className="px-5 py-3 text-left text-[11px] font-semibold text-gray-400 uppercase tracking-wider"
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {records.map((record) => {
                const badge = statusStyle(record.status);
                return (
                  <tr
                    key={record.id}
                    className="border-b border-gray-50 last:border-0"
                  >
                    <td className="px-5 py-3">
                      <div className="text-sm font-semibold text-gray-800">
                        {record.staff_name || record.person_code}
                      </div>
                      <div className="text-xs text-gray-400">
                        {record.person_code}
                      </div>
                    </td>
                    <td className="px-5 py-3 text-sm text-gray-700">
                      {record.shift_name}
                    </td>
                    <td className="px-5 py-3 text-xs text-gray-500">
                      {formatTime(record.period_start_at, timezone)}–
                      {formatTime(record.period_end_at, timezone)}
                    </td>
                    <td className="px-5 py-3 text-sm text-gray-700">
                      {editingId === record.id ? (
                        <input
                          type="datetime-local"
                          value={outsideDraft}
                          onChange={(event) =>
                            setOutsideDraft(event.target.value)
                          }
                          className="rounded border px-2 py-1 text-xs"
                        />
                      ) : (
                        formatTime(record.outside_at, timezone)
                      )}
                    </td>
                    <td className="px-5 py-3 text-sm text-gray-700">
                      {editingId === record.id ? (
                        <input
                          type="datetime-local"
                          value={returnedDraft}
                          onChange={(event) =>
                            setReturnedDraft(event.target.value)
                          }
                          className="rounded border px-2 py-1 text-xs"
                        />
                      ) : (
                        formatTime(record.returned_at, timezone)
                      )}
                    </td>
                    <td className="px-5 py-3 text-sm font-semibold text-gray-700">
                      {record.duration_label || "-"}
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className="inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold"
                        style={{
                          color: badge.color,
                          background: badge.background,
                        }}
                      >
                        {record.status === "returned" ? (
                          <CheckCircle size={13} />
                        ) : (
                          <AlertCircle size={13} />
                        )}
                        {badge.label}
                      </span>
                      {record.correction_source === "manual" && (
                        <span className="ml-2 text-[10px] font-semibold text-gray-400">
                          Manual
                        </span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-xs text-gray-600">
                      {editingId === record.id ? (
                        <input
                          type="text"
                          value={reasonDraft}
                          onChange={(event) =>
                            setReasonDraft(event.target.value)
                          }
                          placeholder="Correction reason"
                          className="w-44 rounded border px-2 py-1 text-xs"
                          aria-label="Correction reason"
                        />
                      ) : (
                        record.correction_reason || "-"
                      )}
                    </td>
                    <td className="px-5 py-3">
                      {editingId === record.id ? (
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void saveEdit()}
                            disabled={savingId === record.id}
                            title="Save correction"
                            aria-label="Save correction"
                            className="rounded p-1 text-green-700 hover:bg-green-50 disabled:opacity-50"
                          >
                            <Check size={15} />
                          </button>
                          <button
                            type="button"
                            onClick={cancelEdit}
                            title="Cancel correction"
                            aria-label="Cancel correction"
                            className="rounded p-1 text-gray-500 hover:bg-gray-100"
                          >
                            <X size={15} />
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => beginEdit(record)}
                          title="Correct break times"
                          aria-label={`Correct break times for ${record.staff_name || record.person_code}`}
                          className="rounded p-1 text-blue-700 hover:bg-blue-50"
                        >
                          <Pencil size={15} />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {editError && (
            <div
              className="border-t border-red-100 bg-red-50 px-5 py-3 text-sm text-red-700"
              role="alert"
            >
              {editError}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
