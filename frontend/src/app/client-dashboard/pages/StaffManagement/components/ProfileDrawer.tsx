/**
 * modules/staff/components/ProfileDrawer.tsx
 * ─────────────────────────────────────────────────────────────────────────────
 * Right-side detail panel for a single staff member.
 * ─────────────────────────────────────────────────────────────────────────────
 */

import { type FC } from "react";
import {
  BriefcaseBusiness,
  CalendarDays,
  Edit2,
  Gift,
  Phone,
  Shield,
  Trash2,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { useOrg } from "../../../contexts/OrgConfigContext";
import { ActionButton } from "../../engine/ModuleShell";
import { T } from "../../../components/ui/theme";
import { useAuthenticatedImageUrl } from "../../../hooks/useAuthenticatedImageUrl";
import { type PeopleRenderingModel } from "../../../utils/templateRendering";
import { peopleCodeModel } from "../types/types";
import { type StaffMember } from "../types/staffTypes";
import { staffAvatarUrl, staffInitial } from "../utils/staffMember";
import { STATUS_META, statusIcon } from "../utils/staffStatus";

export const ProfileDrawer: FC<{
  member: StaffMember;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
  canDelete: boolean;
  branchName: (id: number) => string;
  peopleModel: PeopleRenderingModel;
}> = ({
  member,
  onClose,
  onEdit,
  onDelete,
  canDelete,
  branchName,
  peopleModel,
}) => {
  const sm = STATUS_META[member.status];
  const { organizationId } = useOrg();
  const authedAvatarUrl = useAuthenticatedImageUrl(staffAvatarUrl(member));

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 900,
        display: "flex",
        justifyContent: "flex-end",
      }}
    >
      <div
        onClick={onClose}
        style={{
          position: "absolute",
          inset: 0,
          background: "rgba(0,0,0,0.3)",
        }}
      />

      <div
        style={{
          position: "relative",
          width: 380,
          height: "100%",
          background: T.card,
          boxShadow: "-8px 0 32px rgba(0,0,0,0.12)",
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "18px 20px",
            borderBottom: `1px solid ${T.border}`,
            background: T.teal50,
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 700, color: T.head }}>
            {peopleModel.personSingular} Profile
          </div>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              color: T.muted,
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Avatar + identity */}
        <div
          style={{
            padding: "24px 20px",
            borderBottom: `1px solid ${T.border}`,
            textAlign: "center",
          }}
        >
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: "50%",
              background: T.teal600,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 12px",
              fontSize: 26,
              fontWeight: 800,
              color: "#fff",
            }}
          >
            {authedAvatarUrl ? (
              <img
                src={authedAvatarUrl}
                alt={member.name}
                style={{
                  width: "100%",
                  height: "100%",
                  borderRadius: "50%",
                  objectFit: "cover",
                }}
              />
            ) : (
              staffInitial(member)
            )}
          </div>
          <div
            style={{
              fontSize: 16,
              fontWeight: 800,
              color: T.head,
              marginBottom: 4,
            }}
          >
            {member.name}
          </div>
          <div style={{ fontSize: 12, color: T.muted, marginBottom: 8 }}>
            {peopleCodeModel(peopleModel.peopleType).label}:{" "}
            {member.personCode || member.employeeId || "—"}
          </div>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              padding: "3px 10px",
              borderRadius: 20,
              background: sm.bg,
            }}
          >
            {statusIcon(member.status)}
            <span style={{ fontSize: 11, fontWeight: 700, color: sm.color }}>
              {sm.label}
            </span>
          </div>
        </div>

        {/* Details — all fields accessed directly, no lookup functions */}
        <div style={{ padding: "16px 20px", flex: 1 }}>
          {[
            {
              Icon: UserRound,
              label: peopleCodeModel(peopleModel.peopleType).label,
              val: member.personCode || member.employeeId,
            },
            { Icon: Phone, label: "Phone", val: member.phone },
            ...(!peopleModel.isStudent
              ? [{ Icon: Shield, label: "CNIC", val: member.cnic || "—" }]
              : []),
            {
              Icon: UsersRound,
              label: "Department",
              val: member.department || "—",
            },
            {
              Icon: BriefcaseBusiness,
              label: "Designation",
              val: member.role || member.position || "—",
            },
            {
              Icon: Gift,
              label: "Benefits",
              val: member.benefits.length ? member.benefits.join(", ") : "—",
            },
            {
              Icon: CalendarDays,
              label: "Joined",
              val: member.joinDate || "—",
            },
            ...(peopleModel.isStudent
              ? [
                  {
                    Icon: UserRound,
                    label: "Father Name",
                    val: member.fatherName || "—",
                  },
                  {
                    Icon: Phone,
                    label: "Father Number",
                    val: member.fatherPhone || "—",
                  },
                  {
                    Icon: Shield,
                    label: "Father CNIC",
                    val: member.fatherCnic || "—",
                  },
                ]
              : []),
          ].map(({ Icon, label, val }) => (
            <div
              key={label}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 12,
                padding: "10px 0",
                borderBottom: `1px solid ${T.teal50}`,
              }}
            >
              <div
                style={{
                  width: 28,
                  height: 28,
                  borderRadius: 7,
                  background: T.teal50,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                }}
              >
                <Icon size={13} color={T.teal600} />
              </div>
              <div>
                <div
                  style={{
                    fontSize: 10,
                    color: T.muted,
                    fontWeight: 700,
                    textTransform: "uppercase",
                    letterSpacing: ".06em",
                    marginBottom: 2,
                  }}
                >
                  {label}
                </div>
                <div style={{ fontSize: 13, color: T.head, fontWeight: 500 }}>
                  {val}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Actions */}
        <div
          style={{
            padding: "14px 20px",
            borderTop: `1px solid ${T.border}`,
            display: "flex",
            gap: 8,
          }}
        >
          <ActionButton label="Edit" Icon={Edit2} onClick={onEdit} />
          {canDelete && (
            <ActionButton
              label="Archive"
              Icon={Trash2}
              onClick={onDelete}
              variant="ghost"
            />
          )}
        </div>
      </div>
    </div>
  );
};
