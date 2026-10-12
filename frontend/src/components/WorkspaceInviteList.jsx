import { useEffect, useState } from "react";
import { listWorkspaceInvites, resendWorkspaceInvite, revokeWorkspaceInvite } from "../api";

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

  if (invites === null) return <p className="muted">불러오는 중...</p>;

  const rows = filter === "pending" ? invites.filter((i) => statusOf(i) === "대기 중") : invites;
  if (rows.length === 0) return filter === "pending" ? null : <p className="muted">보낸 초대가 없습니다.</p>;

  return (
    <div className="invite-list">
      {filter === "pending" && <p className="muted invite-list-label">대기 중인 초대</p>}
      {error && <div className="alert-error">{error}</div>}
      <table className="repo-table">
        <thead>
          <tr>
            <th>대상</th>
            <th>역할</th>
            <th>메일 발송</th>
            <th>상태</th>
            <th></th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((inv) => {
            const status = statusOf(inv);
            const busy = busyId === inv.id;
            return (
              <tr key={inv.id}>
                <td className="muted">{inv.target_email || "누구나(링크 소지자)"}</td>
                <td>{inv.role}</td>
                <td>
                  {inv.target_email ? (
                    <span className={`badge ${inv.email_sent ? "badge-webhook" : ""}`}>
                      {inv.email_sent ? "발송됨" : "미발송"}
                    </span>
                  ) : (
                    <span className="muted">해당 없음</span>
                  )}
                </td>
                <td>
                  <span className={`badge ${status === "대기 중" ? "badge-webhook" : ""}`}>{status}</span>
                </td>
                <td>
                  {status === "대기 중" && inv.target_email && (
                    <button disabled={busy} onClick={() => doResend(inv.id)}>
                      {busy ? "처리 중..." : "재발송"}
                    </button>
                  )}
                </td>
                <td>
                  {status === "대기 중" && (
                    <button disabled={busy} onClick={() => doRevoke(inv.id)}>
                      취소
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
