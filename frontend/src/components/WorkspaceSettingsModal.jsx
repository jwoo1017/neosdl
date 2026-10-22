import { useEffect, useState } from "react";
import {
  createWorkspaceInvite,
  deleteWorkspace,
  fetchWorkspace,
  removeWorkspaceMember,
  updateWorkspace,
  updateWorkspaceMemberRole,
} from "../api";
import { useWorkspace } from "../WorkspaceContext";
import Modal from "./Modal";
import WorkspaceInviteList from "./WorkspaceInviteList";

export default function WorkspaceSettingsModal({ onClose }) {
  const { activeWorkspaceId, isWorkspaceAdmin, reload: reloadWorkspaces } = useWorkspace();

  const [workspace, setWorkspace] = useState(null);
  const [error, setError] = useState(null);
  const [nameDraft, setNameDraft] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteEmail, setInviteEmail] = useState("");
  const [lastInvite, setLastInvite] = useState(null);
  const [inviteListVersion, setInviteListVersion] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = () => {
    if (!activeWorkspaceId) return;
    fetchWorkspace(activeWorkspaceId)
      .then((w) => {
        setWorkspace(w);
        setNameDraft(w.name);
      })
      .catch((err) => setError(err.message));
  };

  useEffect(load, [activeWorkspaceId]);

  const saveName = async (e) => {
    e.preventDefault();
    try {
      await updateWorkspace(activeWorkspaceId, nameDraft);
      await reloadWorkspaces();
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const submitInvite = async (e) => {
    e.preventDefault();
    try {
      const invite = await createWorkspaceInvite(activeWorkspaceId, inviteRole, inviteEmail.trim() || undefined);
      setLastInvite(invite);
      setInviteEmail("");
      setInviteListVersion((v) => v + 1);
    } catch (err) {
      setError(err.message);
    }
  };

  const changeMemberRole = async (memberId, role) => {
    try {
      await updateWorkspaceMemberRole(activeWorkspaceId, memberId, role);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const doRemoveMember = async (memberId) => {
    try {
      await removeWorkspaceMember(activeWorkspaceId, memberId);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const doDeleteWorkspace = async () => {
    try {
      await deleteWorkspace(activeWorkspaceId);
      await reloadWorkspaces();
      onClose();
    } catch (err) {
      setError(err.message);
    }
  };

  if (!workspace) {
    return (
      <Modal title="워크스페이스 관리" onClose={onClose} wide>
        {error ? <div className="alert-error">{error}</div> : "불러오는 중..."}
      </Modal>
    );
  }

  return (
    <Modal title={`워크스페이스 관리 · ${workspace.name}`} onClose={onClose} wide>
      {error && <div className="alert-error">{error}</div>}

      <section className="dashboard-section">
        <h2>워크스페이스 정보</h2>
        {isWorkspaceAdmin ? (
          <form className="inline-form" onSubmit={saveName}>
            <input value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} />
            <button type="submit">저장</button>
          </form>
        ) : (
          <p className="muted">{workspace.name}</p>
        )}
      </section>

      <section className="dashboard-section">
        <h2>멤버</h2>
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>계정</th>
                <th>역할</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {workspace.members.map((m) => (
                <tr key={m.member_id}>
                  <td>{m.nickname} {m.is_guest && <span className="guest-badge" title="회사 도메인 밖 계정 — 초대받은 공간만 이용">게스트</span>}</td>
                  <td>
                    {isWorkspaceAdmin ? (
                      <select value={m.role} onChange={(e) => changeMemberRole(m.member_id, e.target.value)}>
                        <option value="admin">admin</option>
                        <option value="member">member</option>
                      </select>
                    ) : (
                      m.role
                    )}
                  </td>
                  <td>{isWorkspaceAdmin && <button onClick={() => doRemoveMember(m.member_id)}>제거</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {isWorkspaceAdmin && (
        <section className="dashboard-section">
          <h2>멤버 초대</h2>
          <form className="inline-form" onSubmit={submitInvite}>
            <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
              <option value="admin">admin</option>
              <option value="member">member</option>
            </select>
            <input
              placeholder="이메일 (선택, 비우면 링크 소지자 누구나)"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
            />
            <button type="submit">초대 링크 생성</button>
          </form>
          {lastInvite && (
            <p className="muted invite-url">
              {lastInvite.target_email
                ? lastInvite.email_sent
                  ? "메일을 발송했습니다: "
                  : "메일 발송 실패 — 아래 링크를 직접 전달하세요: "
                : "생성됨: "}
              <code>{lastInvite.invite_url}</code>
            </p>
          )}
          <WorkspaceInviteList workspaceId={activeWorkspaceId} version={inviteListVersion} />
        </section>
      )}

      {isWorkspaceAdmin && (
        <section className="dashboard-section danger-zone">
          <h2>워크스페이스 삭제</h2>
          <p className="muted">
            워크스페이스를 삭제하면 소속 프로젝트도 함께 더 이상 보이지 않게 됩니다(감사 로그는 보존).
            되돌릴 수 없습니다.
          </p>
          {confirmDelete ? (
            <div className="inline-form">
              <button className="btn-danger" onClick={doDeleteWorkspace}>정말 삭제합니다</button>
              <button onClick={() => setConfirmDelete(false)}>취소</button>
            </div>
          ) : (
            <button className="btn-danger" onClick={() => setConfirmDelete(true)}>워크스페이스 삭제</button>
          )}
        </section>
      )}
    </Modal>
  );
}
