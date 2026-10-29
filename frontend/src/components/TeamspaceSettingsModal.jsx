import { useEffect, useState } from "react";
import {
  createTeamspaceInvite,
  deleteTeamspace,
  fetchTeamspace,
  leaveTeamspace,
  removeTeamspaceMember,
  updateTeamspace,
  updateTeamspaceMemberDiscipline,
  updateTeamspaceMemberRole,
} from "../api";
import { useMe } from "../MeContext";
import { useDialog } from "../dialog/DialogContext";
import { useTeamspace } from "../TeamspaceContext";
import { assignableRoles, canManageMember, describeSpaceError, isSpaceAdmin, SPACE_ROLE_LABEL } from "../spaceRoles";
import { removeRecentProjectsByTeamspace } from "../recentProjects";
import Avatar from "./Avatar";
import Modal from "./Modal";
import TeamspaceInviteList from "./TeamspaceInviteList";

const VISIBILITY_LABEL = { open: "공개", closed: "참가 제한", private: "비공개" };
const DISCIPLINE_LABEL = { pm: "PM", frontend: "프론트엔드", backend: "백엔드" };

export default function TeamspaceSettingsModal({ teamspaceId, onClose }) {
  const { reload: reloadTeamspaces } = useTeamspace();
  const { me } = useMe();
  const dialog = useDialog();

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
      .catch((err) => setError(describeSpaceError(err)));
  };

  useEffect(load, [teamspaceId]);

  const myRole = teamspace?.my_role;
  const isAdmin = isSpaceAdmin(myRole);
  const isOwner = myRole === "owner";

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
      setError(null);
      await updateTeamspaceMemberRole(teamspaceId, memberId, role);
      load();
    } catch (err) {
      setError(describeSpaceError(err));
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

  const doRemoveMember = async (m) => {
    if (!(await dialog.confirm({ title: "멤버 제거", message: `${m.nickname}님을 팀스페이스에서 제거할까요?`, confirmLabel: "제거", danger: true }))) return;
    try {
      setError(null);
      await removeTeamspaceMember(teamspaceId, m.member_id);
      load();
    } catch (err) {
      setError(describeSpaceError(err));
    }
  };

  const doLeave = async () => {
    if (!(await dialog.confirm({ title: "팀스페이스 나가기", message: `'${teamspace.name}' 팀스페이스에서 나갈까요?`, confirmLabel: "나가기", danger: true }))) return;
    try {
      await leaveTeamspace(teamspaceId);
      await reloadTeamspaces();
      onClose();
    } catch (err) {
      setError(describeSpaceError(err));
    }
  };

  const doDeleteTeamspace = async () => {
    try {
      await deleteTeamspace(teamspaceId);
      removeRecentProjectsByTeamspace(teamspaceId);
      await reloadTeamspaces();
      onClose();
    } catch (err) {
      setError(describeSpaceError(err));
    }
  };

  if (!teamspace) {
    return (
      <Modal title="팀스페이스 관리" onClose={onClose} wide>
        {error ? <div className="alert-error">{error}</div> : "불러오는 중..."}
      </Modal>
    );
  }

  const isLastOwner = isOwner && teamspace.members.filter((m) => m.role === "owner").length <= 1;

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
              {teamspace.members.map((m) => {
                const isMe = m.member_id === me?.id;
                const manageable = !isMe && canManageMember(myRole, m.role);
                return (
                <tr key={m.member_id}>
                  <td>
                    <span className="member-cell">
                      <Avatar profile={{ id: m.member_id, nickname: m.nickname, name: m.name, avatar_url: m.avatar_url }}
                              size={26} role={m.role} />
                      <span className="member-cell-names">
                        <span>{m.nickname} {isMe && <span className="muted">(나)</span>} {m.is_guest && <span className="guest-badge" title="회사 도메인 밖 계정 — 초대받은 공간만 이용">게스트</span>}</span>
                        {m.name && m.name !== m.nickname && <span className="muted">{m.name}</span>}
                      </span>
                    </span>
                  </td>
                  <td>
                    {manageable ? (
                      <select value={m.role} onChange={(e) => changeMemberRole(m.member_id, e.target.value)}>
                        {assignableRoles(myRole).map((r) => (
                          <option key={r} value={r}>{SPACE_ROLE_LABEL[r]}</option>
                        ))}
                      </select>
                    ) : (
                      SPACE_ROLE_LABEL[m.role] || m.role
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
                  <td>
                    {isMe ? (
                      <button
                        onClick={doLeave}
                        disabled={isLastOwner}
                        title={isLastOwner ? "마지막 소유자는 나갈 수 없습니다 — 다른 멤버를 소유자로 지정하거나 팀스페이스를 삭제하세요" : undefined}
                      >
                        나가기
                      </button>
                    ) : (
                      manageable && <button onClick={() => doRemoveMember(m)}>제거</button>
                    )}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {isAdmin && (
        <section className="dashboard-section">
          <h2>멤버 초대</h2>
          <form className="inline-form" onSubmit={submitInvite}>
            <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
              <option value="admin">{SPACE_ROLE_LABEL.admin}</option>
              <option value="member">{SPACE_ROLE_LABEL.member}</option>
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

      {isOwner && (
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
