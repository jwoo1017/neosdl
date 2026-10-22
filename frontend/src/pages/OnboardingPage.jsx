import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { acceptMyInvite, fetchMyInvites } from "../api";
import { useWorkspace } from "../WorkspaceContext";
import { useMe } from "../MeContext";
import WorkspaceCreateForm from "../components/WorkspaceCreateForm";

const KIND_LABEL = { workspace: "워크스페이스", teamspace: "팀스페이스", project: "프로젝트" };

// 게스트용 — 내 이메일로 온 초대를 바로 수락하는 목록(헤더의 초대 수신함과 같은 API).
function GuestInvites({ onJoined }) {
  const [invites, setInvites] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchMyInvites().then(setInvites).catch(() => setInvites([]));
  }, []);

  const accept = async (invite) => {
    setBusyId(invite.id);
    setError(null);
    try {
      const result = await acceptMyInvite(invite.kind, invite.id);
      await onJoined(result.workspace_id);
    } catch (err) {
      setError(err.message);
      setBusyId(null);
    }
  };

  if (invites === null) return <p className="muted">불러오는 중...</p>;
  if (invites.length === 0) return <p className="muted">받은 초대가 없어요.</p>;
  return (
    <ul className="guest-invites">
      {error && <li className="alert-error">{error}</li>}
      {invites.map((inv) => (
        <li key={inv.id} className="guest-invite">
          <span className="guest-invite-text">
            <strong>{inv.scope_name}</strong>
            <span className="muted"> {KIND_LABEL[inv.kind]} · {inv.invited_by_nickname}님의 초대</span>
          </span>
          <button type="button" className="btn-primary" disabled={busyId === inv.id} onClick={() => accept(inv)}>
            {busyId === inv.id ? "합류 중..." : "합류"}
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function OnboardingPage() {
  const navigate = useNavigate();
  const { setActiveWorkspaceId, reload } = useWorkspace();
  const { me } = useMe();

  const handleCreated = async (workspace) => {
    await reload();
    setActiveWorkspaceId(workspace.id);
    navigate("/workspace");
  };

  // 게스트(회사 도메인 밖, 초대로 가입)는 워크스페이스를 만들 수 없다 — 받은 초대만 보여준다.
  if (me?.is_guest) {
    return (
      <div className="auth-page">
        <div className="auth-card onboarding-card">
          <h1>초대받은 공간에 합류하세요</h1>
          <p>게스트 계정은 초대받은 워크스페이스·팀스페이스·프로젝트만 이용할 수 있어요.</p>
          <GuestInvites
            onJoined={async (workspaceId) => {
              await reload();
              if (workspaceId) setActiveWorkspaceId(workspaceId);
              navigate("/workspace");
            }}
          />
          <p className="muted onboarding-hint">초대가 보이지 않으면 초대한 분에게 이 이메일({me.email})로 다시 보내 달라고 하세요.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="auth-page">
      <div className="auth-card onboarding-card">
        <h1>워크스페이스를 만들어보세요</h1>
        <p>개인 또는 회사, 어떤 용도로 쓸지 먼저 골라주세요.</p>

        <WorkspaceCreateForm onCreated={handleCreated} />

        <p className="muted onboarding-hint">초대 링크를 받았다면, 새로 만들지 말고 그 링크를 열어 합류하세요.</p>
      </div>
    </div>
  );
}
