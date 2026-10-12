import { useEffect, useRef, useState } from "react";
import { acceptMyInvite, fetchMyInvites } from "./api";
import { useWorkspace } from "./WorkspaceContext";
import { useTeamspace } from "./TeamspaceContext";
import { useProject } from "./ProjectContext";
import InboxIcon from "./icons/InboxIcon";

const KIND_LABEL = { workspace: "워크스페이스", teamspace: "팀스페이스", project: "프로젝트" };
const POLL_INTERVAL_MS = 60000;

export default function InviteInbox() {
  const [invites, setInvites] = useState([]);
  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);
  const panelRef = useRef(null);

  const { reload: reloadWorkspaces, setActiveWorkspaceId } = useWorkspace();
  const { reload: reloadTeamspaces } = useTeamspace();
  const { reload: reloadProjects } = useProject();

  const load = () => {
    fetchMyInvites().then(setInvites).catch(() => {});
  };

  useEffect(() => {
    load();
    const interval = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onClickOutside = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [open]);

  const accept = async (invite) => {
    setBusyId(invite.id);
    setError(null);
    try {
      const result = await acceptMyInvite(invite.kind, invite.id);
      setInvites((list) => list.filter((i) => i.id !== invite.id));
      // 방금 합류한 워크스페이스/팀스페이스/프로젝트가 사이드바에 바로
      // 보이도록 세 컨텍스트를 전부 새로고침한다 — 어느 kind든 workspace_id는
      // 항상 응답에 실려온다.
      await reloadWorkspaces();
      await reloadTeamspaces();
      await reloadProjects();
      if (result.workspace_id) setActiveWorkspaceId(result.workspace_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="inbox" ref={panelRef}>
      <button
        type="button"
        className="inbox-toggle"
        onClick={() => setOpen((v) => !v)}
        aria-label="초대 수신함"
        title="초대 수신함"
      >
        <InboxIcon />
        {invites.length > 0 && <span className="inbox-badge">{invites.length}</span>}
      </button>

      {open && (
        <div className="inbox-panel">
          <div className="inbox-panel-header">초대 수신함</div>
          {error && <div className="alert-error">{error}</div>}
          {invites.length === 0 ? (
            <p className="muted inbox-empty">받은 초대가 없습니다.</p>
          ) : (
            invites.map((inv) => (
              <div key={`${inv.kind}-${inv.id}`} className="inbox-item">
                <p className="inbox-item-text">
                  <strong>{inv.invited_by_nickname}</strong>님이 <strong>{inv.scope_name}</strong>
                  {" "}({KIND_LABEL[inv.kind]})에 <strong>{inv.role}</strong> 역할로 초대했습니다.
                </p>
                <button disabled={busyId === inv.id} onClick={() => accept(inv)}>
                  {busyId === inv.id ? "처리 중..." : "수락"}
                </button>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
