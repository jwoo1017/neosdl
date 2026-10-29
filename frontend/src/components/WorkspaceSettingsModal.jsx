import { useEffect, useState } from "react";
import {
  createWorkspaceInvite,
  deleteWorkspace,
  fetchWorkspace,
  leaveWorkspace,
  removeWorkspaceMember,
  updateWorkspace,
  updateWorkspaceMemberRole,
} from "../api";
import { useMe } from "../MeContext";
import { useDialog } from "../dialog/DialogContext";
import { useWorkspace } from "../WorkspaceContext";
import { assignableRoles, canManageMember, describeSpaceError, SPACE_ROLE_LABEL } from "../spaceRoles";
import Avatar from "./Avatar";
import Modal from "./Modal";
import { Segmented } from "./Segmented";
import { roleOptions } from "./spaceOptions";
import WorkspaceInviteList from "./WorkspaceInviteList";
import { CopyIcon } from "../icons/SpaceIcons";

export default function WorkspaceSettingsModal({ onClose }) {
  const dialog = useDialog();
  const { activeWorkspaceId, activeWorkspace, isWorkspaceAdmin, isWorkspaceOwner, reload: reloadWorkspaces } =
    useWorkspace();
  const { me } = useMe();
  const myRole = activeWorkspace?.role;

  const [workspace, setWorkspace] = useState(null);
  const [error, setError] = useState(null);
  const [nameDraft, setNameDraft] = useState("");
  const [inviteRole, setInviteRole] = useState("member");
  const [inviteEmail, setInviteEmail] = useState("");
  const [lastInvite, setLastInvite] = useState(null);
  const [inviteListVersion, setInviteListVersion] = useState(0);
  const [copied, setCopied] = useState(false);

  const load = () => {
    if (!activeWorkspaceId) return;
    fetchWorkspace(activeWorkspaceId)
      .then((w) => {
        setWorkspace(w);
        setNameDraft(w.name);
      })
      .catch((err) => setError(describeSpaceError(err)));
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
      setError(null);
      await updateWorkspaceMemberRole(activeWorkspaceId, memberId, role);
      // 내 역할이 바뀌었을 수도 있다(다른 사람을 owner 로 지정한 뒤 스스로 강등 등) — 컨텍스트도 갱신.
      await reloadWorkspaces();
      load();
    } catch (err) {
      setError(describeSpaceError(err));
    }
  };

  const doRemoveMember = async (m) => {
    if (!(await dialog.confirm({ title: "멤버 제거", message: `${m.nickname}님을 워크스페이스에서 제거할까요?`, confirmLabel: "제거", danger: true }))) return;
    try {
      setError(null);
      await removeWorkspaceMember(activeWorkspaceId, m.member_id);
      load();
    } catch (err) {
      setError(describeSpaceError(err));
    }
  };

  const doLeave = async () => {
    if (!(await dialog.confirm({ title: "워크스페이스 나가기", message: `'${workspace.name}' 워크스페이스에서 나갈까요? 다시 들어오려면 초대가 필요합니다.`, confirmLabel: "나가기", danger: true }))) return;
    try {
      await leaveWorkspace(activeWorkspaceId);
      await reloadWorkspaces();
      onClose();
    } catch (err) {
      setError(describeSpaceError(err));
    }
  };

  const doDeleteWorkspace = async () => {
    const ok = await dialog.confirm({
      title: "워크스페이스 삭제",
      message: `'${workspace.name}' 워크스페이스를 삭제할까요?
소속 프로젝트도 더 이상 보이지 않게 되고, 되돌릴 수 없습니다(감사 로그는 보존).`,
      confirmLabel: "삭제",
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteWorkspace(activeWorkspaceId);
      await reloadWorkspaces();
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

  if (!workspace) {
    return (
      <Modal title="워크스페이스 관리" onClose={onClose} wide>
        {error ? <div className="alert-error">{error}</div> : <p className="ws-loading">불러오는 중...</p>}
      </Modal>
    );
  }

  const isLastOwner = myRole === "owner" && workspace.members.filter((m) => m.role === "owner").length <= 1;
  const nameChanged = nameDraft.trim() && nameDraft.trim() !== workspace.name;

  return (
    <Modal title="워크스페이스 관리" onClose={onClose} wide>
      {error && <div className="alert-error ws-error">{error}</div>}

      <div className="ws-row">
        <div className="ws-row-label">
          <h3>이름</h3>
          <p>사이드바와 초대 메일에 보이는 이름입니다.</p>
        </div>
        <div className="ws-row-control">
          {isWorkspaceAdmin ? (
            <form className="ws-inline" onSubmit={saveName}>
              <input className="ws-input" value={nameDraft} onChange={(e) => setNameDraft(e.target.value)}
                     aria-label="워크스페이스 이름" />
              <button type="submit" className="mp-btn mp-btn-secondary" disabled={!nameChanged}>저장</button>
            </form>
          ) : (
            <span className="ws-static">{workspace.name}</span>
          )}
        </div>
      </div>

      <div className="ws-row">
        <div className="ws-row-label">
          <h3>멤버 <span className="ws-count">{workspace.members.length}</span></h3>
          <p>소유자는 공간 삭제와 관리자 지정, 관리자는 초대와 멤버 관리를 할 수 있습니다.</p>
        </div>
        <ul className="ws-members">
          {workspace.members.map((m) => {
            const isMe = m.member_id === me?.id;
            const manageable = !isMe && canManageMember(myRole, m.role);
            return (
              <li key={m.member_id} className="ws-member">
                <Avatar profile={{ ...m, id: m.member_id }} size={28} />
                <div className="ws-member-names">
                  <span className="ws-member-nick">
                    {m.nickname}
                    {isMe && <span className="ws-chip">나</span>}
                    {m.is_guest && <span className="ws-chip ws-chip-guest">게스트</span>}
                  </span>
                  {m.name && m.name !== m.nickname && <span className="ws-member-name">{m.name}</span>}
                </div>
                <div className="ws-member-role">
                  {manageable ? (
                    <Segmented size="sm" value={m.role} ariaLabel={`${m.nickname} 역할`}
                               onChange={(r) => changeMemberRole(m.member_id, r)}
                               options={roleOptions(assignableRoles(myRole))} />
                  ) : (
                    <span className="ts-role-chip">{SPACE_ROLE_LABEL[m.role] || m.role}</span>
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
      </div>
      {isLastOwner && (
        <p className="ws-footnote">
          마지막 소유자는 나갈 수 없습니다. 다른 멤버를 소유자로 지정하거나 워크스페이스를 삭제하세요.
        </p>
      )}

      {isWorkspaceAdmin && (
        <div className="ws-row">
          <div className="ws-row-label">
            <h3>초대</h3>
            <p>이메일을 비워 두면 링크를 가진 누구나 들어올 수 있습니다.</p>
          </div>
          <div className="ws-row-control">
            <form className="ts-invite-form" onSubmit={submitInvite}>
              <div className="ts-field">
                <span className="ts-field-label">역할</span>
                <Segmented ariaLabel="초대 역할" value={inviteRole} onChange={setInviteRole}
                           options={roleOptions(["member", "admin"])} />
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
            <WorkspaceInviteList workspaceId={activeWorkspaceId} version={inviteListVersion} />
          </div>
        </div>
      )}

      {isWorkspaceOwner && (
        <div className="ws-row ws-row-danger">
          <div className="ws-row-label">
            <h3>워크스페이스 삭제</h3>
            <p>소속 프로젝트도 함께 보이지 않게 됩니다. 감사 로그는 남고, 되돌릴 수 없습니다.</p>
          </div>
          <div className="ws-row-control ws-row-control-end">
            <button type="button" className="mp-btn mp-btn-ghost mp-btn-danger-text" onClick={doDeleteWorkspace}>
              삭제
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
