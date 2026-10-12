import { useEffect, useState } from "react";
import {
  createTeamspaceInvite,
  deleteTeamspace,
  fetchTeamspace,
  removeTeamspaceMember,
  updateTeamspace,
  updateTeamspaceMemberDiscipline,
  updateTeamspaceMemberRole,
} from "../api";
import { useTeamspace } from "../TeamspaceContext";
import { removeRecentProjectsByTeamspace } from "../recentProjects";
import Modal from "./Modal";
import TeamspaceInviteList from "./TeamspaceInviteList";

const VISIBILITY_LABEL = { open: "공개", closed: "참가 제한", private: "비공개" };
const DISCIPLINE_LABEL = { pm: "PM", frontend: "프론트엔드", backend: "백엔드" };

export default function TeamspaceSettingsModal({ teamspaceId, onClose }) {
  const { reload: reloadTeamspaces } = useTeamspace();

  const [teamspace, setTeamspace] = useState(null);
  const [error, setError] = useState(null);
  const [nameDraft, setNameDraft] = useState("");
  const [descriptionDraft, setDescriptionDraft] = useState("");
  const [visibilityDraft, setVisibilityDraft] = useState("open");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteDiscipline, setInviteDiscipline] = useState("");
  const [inviteEmail, setInviteEmail] = useState("");
  const [lastInvite, setLastInvite] = useState(null);
  const [inviteListVersion, setInviteListVersion] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = () => {
    fetchTeamspace(teamspaceId)
      .then((t) => {
        setTeamspace(t);
        setNameDraft(t.name);
        setDescriptionDraft(t.description || "");
        setVisibilityDraft(t.visibility);
      })
      .catch((err) => setError(err.message));
  };

  useEffect(load, [teamspaceId]);

  const isAdmin = teamspace?.my_role === "admin";

  const saveInfo = async (e) => {
    e.preventDefault();
    try {
      await updateTeamspace(teamspaceId, nameDraft, {
        description: descriptionDraft.trim() || undefined,
        visibility: visibilityDraft,
      });
      await reloadTeamspaces();
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const submitInvite = async (e) => {
    e.preventDefault();
    try {
      const invite = await createTeamspaceInvite(
        teamspaceId,
        inviteRole,
        inviteEmail.trim() || undefined,
        inviteDiscipline || undefined
      );
      setLastInvite(invite);
      setInviteEmail("");
      setInviteListVersion((v) => v + 1);
    } catch (err) {
      setError(err.message);
    }
  };

  const changeMemberRole = async (memberId, role) => {
    try {
      await updateTeamspaceMemberRole(teamspaceId, memberId, role);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const changeMemberDiscipline = async (memberId, discipline) => {
    try {
      await updateTeamspaceMemberDiscipline(teamspaceId, memberId, discipline || null);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const doRemoveMember = async (memberId) => {
    try {
      await removeTeamspaceMember(teamspaceId, memberId);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const doDeleteTeamspace = async () => {
    try {
      await deleteTeamspace(teamspaceId);
      removeRecentProjectsByTeamspace(teamspaceId);
      await reloadTeamspaces();
      onClose();
    } catch (err) {
      setError(err.message);
    }
  };

  if (!teamspace) {
    return (
      <Modal title="팀스페이스 관리" onClose={onClose} wide>
        {error ? <div className="alert-error">{error}</div> : "불러오는 중..."}
      </Modal>
    );
  }

  return (
    <Modal title={`팀스페이스 관리 · ${teamspace.name}`} onClose={onClose} wide>
      {error && <div className="alert-error">{error}</div>}

      <section className="dashboard-section">
        <h2>팀스페이스 정보</h2>
        {isAdmin ? (
          <form className="auth-form" onSubmit={saveInfo}>
            <input value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} placeholder="이름" />
            <textarea
              value={descriptionDraft}
              onChange={(e) => setDescriptionDraft(e.target.value)}
              placeholder="설명"
              rows={2}
            />
            <select value={visibilityDraft} onChange={(e) => setVisibilityDraft(e.target.value)}>
              <option value="open">공개 — 워크스페이스 멤버 누구나 보고 가입 가능</option>
              <option value="closed">참가 제한 — 누구나 보이지만 가입은 초대로만</option>
              <option value="private">비공개 — 멤버에게만 표시</option>
            </select>
            <button className="btn-primary auth-submit" type="submit">
              저장
            </button>
          </form>
        ) : (
          <>
            <p className="muted">{teamspace.name}</p>
            {teamspace.description && <p className="muted">{teamspace.description}</p>}
            <span className="badge">{VISIBILITY_LABEL[teamspace.visibility]}</span>
          </>
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
                <th>직무</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {teamspace.members.map((m) => (
                <tr key={m.member_id}>
                  <td>{m.nickname}</td>
                  <td>
                    {isAdmin ? (
                      <select value={m.role} onChange={(e) => changeMemberRole(m.member_id, e.target.value)}>
                        <option value="admin">admin</option>
                        <option value="member">member</option>
                      </select>
                    ) : (
                      m.role
                    )}
                  </td>
                  <td>
                    {isAdmin ? (
                      <select
                        value={m.discipline || ""}
                        onChange={(e) => changeMemberDiscipline(m.member_id, e.target.value)}
                      >
                        <option value="">미지정</option>
                        <option value="pm">PM</option>
                        <option value="frontend">프론트엔드</option>
                        <option value="backend">백엔드</option>
                      </select>
                    ) : (
                      m.discipline ? DISCIPLINE_LABEL[m.discipline] : "미지정"
                    )}
                  </td>
                  <td>{isAdmin && <button onClick={() => doRemoveMember(m.member_id)}>제거</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {isAdmin && (
        <section className="dashboard-section">
          <h2>멤버 초대</h2>
          <form className="inline-form" onSubmit={submitInvite}>
            <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
              <option value="admin">admin</option>
              <option value="member">member</option>
            </select>
            <select value={inviteDiscipline} onChange={(e) => setInviteDiscipline(e.target.value)}>
              <option value="">직무 미지정</option>
              <option value="pm">PM</option>
              <option value="frontend">프론트엔드</option>
              <option value="backend">백엔드</option>
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
          <TeamspaceInviteList teamspaceId={teamspaceId} version={inviteListVersion} />
        </section>
      )}

      {isAdmin && (
        <section className="dashboard-section danger-zone">
          <h2>팀스페이스 삭제</h2>
          <p className="muted">
            팀스페이스를 삭제하면 소속 프로젝트도 함께 더 이상 보이지 않게 됩니다(감사 로그는 보존). 되돌릴 수 없습니다.
          </p>
          {confirmDelete ? (
            <div className="inline-form">
              <button className="btn-danger" onClick={doDeleteTeamspace}>정말 삭제합니다</button>
              <button onClick={() => setConfirmDelete(false)}>취소</button>
            </div>
          ) : (
            <button className="btn-danger" onClick={() => setConfirmDelete(true)}>팀스페이스 삭제</button>
          )}
        </section>
      )}
    </Modal>
  );
}
