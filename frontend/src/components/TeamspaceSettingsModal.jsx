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
import { ChoiceCards, Segmented } from "./Segmented";
import { DISCIPLINE_LABEL, DISCIPLINE_OPTIONS, roleOptions, VISIBILITY_OPTIONS } from "./spaceOptions";
import TeamspaceInviteList from "./TeamspaceInviteList";
import { CopyIcon } from "../icons/SpaceIcons";

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
  const [copied, setCopied] = useState(false);

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
    const ok = await dialog.confirm({
      title: "팀스페이스 삭제",
      message: `'${teamspace.name}' 팀스페이스를 삭제할까요?\n소속 프로젝트도 더 이상 보이지 않게 되고, 되돌릴 수 없습니다(감사 로그는 보존).`,
      confirmLabel: "삭제",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteTeamspace(teamspaceId);
      removeRecentProjectsByTeamspace(teamspaceId);
      await reloadTeamspaces();
      onClose();
    } catch (err) {
      setError(describeSpaceError(err));
    }
  };

  const copyInvite = async () => {
    try {
      await navigator.clipboard.writeText(lastInvite.invite_url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 클립보드 권한 없음 — 링크는 화면에 그대로 보인다
    }
  };

  if (!teamspace) {
    return (
      <Modal title="팀스페이스 관리" onClose={onClose} wide>
        {error ? <div className="alert-error">{error}</div> : <p className="ws-loading">불러오는 중...</p>}
      </Modal>
    );
  }

  const isLastOwner = isOwner && teamspace.members.filter((m) => m.role === "owner").length <= 1;
  const infoDirty = nameDraft.trim() !== teamspace.name
    || descriptionDraft.trim() !== (teamspace.description || "")
    || visibilityDraft !== teamspace.visibility;
  const visibility = VISIBILITY_OPTIONS.find((o) => o.value === teamspace.visibility);
  const VisibilityIcon = visibility?.Icon;

  return (
    <Modal title="팀스페이스 관리" onClose={onClose} wide>
      {error && <div className="alert-error ws-error">{error}</div>}

      <div className="ws-row">
        <div className="ws-row-label">
          <h3>기본 정보</h3>
          <p>이름과 설명은 사이드바와 초대 화면에 보입니다.</p>
        </div>
        <div className="ws-row-control">
          {isAdmin ? (
            <>
              <input className="ws-input" value={nameDraft} onChange={(e) => setNameDraft(e.target.value)}
                     placeholder="팀스페이스 이름" aria-label="팀스페이스 이름" />
              <textarea className="ws-input ts-desc" value={descriptionDraft} rows={2}
                        onChange={(e) => setDescriptionDraft(e.target.value)}
                        placeholder="무엇을 하는 팀인지 한 줄로 (선택)" aria-label="설명" />
            </>
          ) : (
            <div className="ts-static">
              <span className="ws-static">{teamspace.name}</span>
              {teamspace.description && <span className="ts-static-desc">{teamspace.description}</span>}
            </div>
          )}
        </div>
      </div>

      <div className="ws-row">
        <div className="ws-row-label">
          <h3>공개 범위</h3>
          <p>워크스페이스 안에서 이 팀스페이스가 누구에게 보이는지 정합니다.</p>
        </div>
        <div className="ws-row-control">
          {isAdmin ? (
            <>
              <ChoiceCards ariaLabel="공개 범위" options={VISIBILITY_OPTIONS}
                           value={visibilityDraft} onChange={setVisibilityDraft} />
              <div className="ts-save-bar">
                {infoDirty && <span className="ts-dirty">저장하지 않은 변경이 있습니다</span>}
                <button type="button" className="mp-btn mp-btn-primary" disabled={!infoDirty || !nameDraft.trim()}
                        onClick={saveInfo}>
                  저장
                </button>
              </div>
            </>
          ) : (
            visibility && (
              <div className="ts-visibility-static">
                <span className="ts-visibility-chip"><VisibilityIcon size={14} /> {visibility.title}</span>
                <span className="ts-static-desc">{visibility.desc}</span>
              </div>
            )
          )}
        </div>
      </div>

      <div className="ws-section">
        <div className="ws-section-head">
          <h3>멤버 <span className="ws-count">{teamspace.members.length}</span></h3>
          {isAdmin && <p>직무는 선택된 것을 한 번 더 누르면 해제됩니다.</p>}
        </div>
        <ul className="ws-members">
          {teamspace.members.map((m) => {
            const isMe = m.member_id === me?.id;
            const manageable = !isMe && canManageMember(myRole, m.role);
            return (
              <li key={m.member_id} className="ts-member">
                <div className="ts-member-who">
                  <Avatar profile={{ id: m.member_id, nickname: m.nickname, name: m.name, avatar_url: m.avatar_url }}
                          size={28} role={m.role} />
                  <div className="ws-member-names">
                    <span className="ws-member-nick">
                      {m.nickname}
                      {isMe && <span className="ws-chip">나</span>}
                      {m.is_guest && <span className="ws-chip ws-chip-guest">게스트</span>}
                    </span>
                    {m.name && m.name !== m.nickname && <span className="ws-member-name">{m.name}</span>}
                  </div>
                </div>
                <div className="ts-member-controls">
                  {manageable ? (
                    <Segmented size="sm" ariaLabel={`${m.nickname} 역할`} value={m.role}
                               options={roleOptions(assignableRoles(myRole))}
                               onChange={(r) => changeMemberRole(m.member_id, r)} />
                  ) : (
                    <span className="ts-role-chip">{SPACE_ROLE_LABEL[m.role] || m.role}</span>
                  )}
                  {isAdmin ? (
                    <Segmented size="sm" allowDeselect ariaLabel={`${m.nickname} 직무`} value={m.discipline || ""}
                               options={DISCIPLINE_OPTIONS}
                               onChange={(d) => changeMemberDiscipline(m.member_id, d)} />
                  ) : (
                    <span className="ts-role-chip is-faint">
                      {m.discipline ? DISCIPLINE_LABEL[m.discipline] : "직무 미지정"}
                    </span>
                  )}
                </div>
                <div className="ws-member-action">
                  {isMe ? (
                    isLastOwner ? (
                      <span className="ws-note">마지막 소유자</span>
                    ) : (
                      <button type="button" className="ws-text-btn" onClick={doLeave}>나가기</button>
                    )
                  ) : (
                    manageable && (
                      <button type="button" className="ws-text-btn ws-text-btn-danger" onClick={() => doRemoveMember(m)}>
                        제거
                      </button>
                    )
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        {isLastOwner && (
          <p className="ts-footnote">
            마지막 소유자는 나갈 수 없습니다. 다른 멤버를 소유자로 지정하거나 팀스페이스를 삭제하세요.
          </p>
        )}
      </div>

      {isAdmin && (
        <div className="ws-row">
          <div className="ws-row-label">
            <h3>초대</h3>
            <p>이메일을 비워 두면 링크를 가진 누구나 들어올 수 있습니다.</p>
          </div>
          <div className="ws-row-control">
            <form className="ts-invite-form" onSubmit={submitInvite}>
              <div className="ts-invite-pickers">
                <div className="ts-field">
                  <span className="ts-field-label">역할</span>
                  <Segmented ariaLabel="초대 역할" value={inviteRole} onChange={setInviteRole}
                             options={roleOptions(["member", "admin"])} />
                </div>
                <div className="ts-field">
                  <span className="ts-field-label">직무 <em>선택</em></span>
                  <Segmented allowDeselect ariaLabel="초대 직무" value={inviteDiscipline}
                             onChange={setInviteDiscipline} options={DISCIPLINE_OPTIONS} />
                </div>
              </div>
              <div className="ws-inline">
                <input className="ws-input" type="email" placeholder="name@company.com"
                       value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} aria-label="초대 이메일" />
                <button type="submit" className="mp-btn mp-btn-primary">
                  {inviteEmail.trim() ? "초대 보내기" : "링크 만들기"}
                </button>
              </div>
            </form>
            {lastInvite && (
              <div className="ws-invite-result">
                <span className="ws-invite-result-label">
                  {lastInvite.target_email
                    ? lastInvite.email_sent
                      ? `${lastInvite.target_email} 에게 메일을 보냈습니다.`
                      : "메일을 보내지 못했습니다. 아래 링크를 직접 전달하세요."
                    : "초대 링크가 만들어졌습니다."}
                </span>
                <div className="ws-invite-url">
                  <code>{lastInvite.invite_url}</code>
                  <button type="button" className="ws-text-btn" onClick={copyInvite}>
                    <CopyIcon size={13} /> {copied ? "복사됨" : "복사"}
                  </button>
                </div>
              </div>
            )}
            <TeamspaceInviteList teamspaceId={teamspaceId} version={inviteListVersion} />
          </div>
        </div>
      )}

      {isOwner && (
        <div className="ws-row ws-row-danger">
          <div className="ws-row-label">
            <h3>팀스페이스 삭제</h3>
            <p>소속 프로젝트도 함께 보이지 않게 됩니다. 감사 로그는 남고, 되돌릴 수 없습니다.</p>
          </div>
          <div className="ws-row-control ws-row-control-end">
            <button type="button" className="mp-btn mp-btn-ghost mp-btn-danger-text" onClick={doDeleteTeamspace}>
              삭제
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
