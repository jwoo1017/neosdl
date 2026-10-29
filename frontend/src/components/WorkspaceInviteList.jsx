import { useEffect, useState } from "react";
import { listWorkspaceInvites, resendWorkspaceInvite, revokeWorkspaceInvite } from "../api";
import { SPACE_ROLE_LABEL } from "../spaceRoles";

function statusOf(inv) {
  if (inv.accepted_at) return "수락됨";
  if (inv.revoked_at) return "취소됨";
  return "대기 중";
}

export default function WorkspaceInviteList({ workspaceId, version = 0, filter = "all" }) {
  const [invites, setInvites] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const load = () => {
    listWorkspaceInvites(workspaceId).then(setInvites).catch((err) => setError(err.message));
  };

  useEffect(load, [workspaceId, version]);

  const doResend = async (inviteId) => {
    setBusyId(inviteId);
    try {
      await resendWorkspaceInvite(workspaceId, inviteId);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  const doRevoke = async (inviteId) => {
    setBusyId(inviteId);
    try {
      await revokeWorkspaceInvite(workspaceId, inviteId);
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  if (invites === null) return <p className="ws-invites-empty">불러오는 중...</p>;

  const rows = filter === "pending" ? invites.filter((i) => statusOf(i) === "대기 중") : invites;
  if (rows.length === 0) return filter === "pending" ? null : <p className="ws-invites-empty">보낸 초대가 없습니다.</p>;

  return (
    <div className="ws-invites">
      <p className="ws-invites-label">{filter === "pending" ? "대기 중인 초대" : "보낸 초대"}</p>
      {error && <div className="alert-error">{error}</div>}
      <ul>
        {rows.map((inv) => {
          const status = statusOf(inv);
          const pending = status === "대기 중";
          const busy = busyId === inv.id;
          return (
            <li key={inv.id} className={`ws-invite${pending ? "" : " is-closed"}`}>
              <span className="ws-invite-target">{inv.target_email || "링크 소지자 누구나"}</span>
              <span className="ws-invite-meta">
                {SPACE_ROLE_LABEL[inv.role] || inv.role}
                {inv.target_email && <> · {inv.email_sent ? "메일 발송됨" : "메일 미발송"}</>}
              </span>
              <span className={`ws-invite-status${pending ? " is-pending" : ""}`}>{status}</span>
              <span className="ws-invite-actions">
                {pending && inv.target_email && (
                  <button type="button" className="ws-text-btn" disabled={busy} onClick={() => doResend(inv.id)}>
                    {busy ? "처리 중..." : "재발송"}
                  </button>
                )}
                {pending && (
                  <button type="button" className="ws-text-btn ws-text-btn-danger" disabled={busy}
                          onClick={() => doRevoke(inv.id)}>
                    취소
                  </button>
                )}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
