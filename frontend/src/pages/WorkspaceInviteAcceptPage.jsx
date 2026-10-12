import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { acceptWorkspaceInvite, fetchWorkspaceInvitePreview, logout } from "../api";

// WorkspaceContext가 쓰는 것과 같은 키 — 로그인 전에는 WorkspaceProvider가
// 없어 그 context를 못 쓰니, 수락 후 풀 페이지 이동 전에 직접 써 둔다.
const ACTIVE_WORKSPACE_KEY = "neosdl_active_workspace_id";

export default function WorkspaceInviteAcceptPage() {
  const { token } = useParams();
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [accepting, setAccepting] = useState(false);

  useEffect(() => {
    fetchWorkspaceInvitePreview(token).then(setPreview).catch((err) => setError(err.message));
  }, [token]);

  const accept = async () => {
    setAccepting(true);
    try {
      const result = await acceptWorkspaceInvite(token);
      localStorage.setItem(ACTIVE_WORKSPACE_KEY, result.workspace_id);
      window.location.href = "/workspace";
    } catch (err) {
      setError(err.message);
      setAccepting(false);
    }
  };

  const loginNext = `/workspace-invites/${token}`;
  const loginHref = preview?.target_email
    ? `/login?next=${encodeURIComponent(loginNext)}&email=${encodeURIComponent(preview.target_email)}`
    : `/login?next=${encodeURIComponent(loginNext)}`;

  return (
    <div className="dashboard">
      <h1>워크스페이스 초대</h1>
      {error && <div className="alert-error">{error}</div>}
      {!preview && !error && <p className="muted">불러오는 중...</p>}
      {preview && (
        <div className="dashboard-empty">
          <p>
            <strong>{preview.invited_by_nickname}</strong>님이 <strong>{preview.workspace_name}</strong> 워크스페이스에
            <strong> {preview.role}</strong> 역할로 초대했습니다.
          </p>

          {preview.viewer_status === "anonymous" && (
            <>
              <p className="muted">
                {preview.target_email
                  ? `이 초대는 ${preview.target_email}로 발송되었습니다. 로그인하거나 계정을 만들어 수락하세요.`
                  : "수락하려면 로그인하거나 계정을 만드세요."}
              </p>
              <a className="btn-primary" href={loginHref}>
                로그인/가입하고 수락하기
              </a>
            </>
          )}

          {preview.viewer_status === "email_mismatch" && (
            <>
              <div className="alert-error">
                이 초대는 {preview.target_email}로 발송되었습니다. 지금 다른 계정으로 로그인되어 있습니다.
              </div>
              <button
                className="btn-primary"
                onClick={async () => {
                  await logout();
                  window.location.href = loginHref;
                }}
              >
                로그아웃하고 다시 로그인
              </button>
            </>
          )}

          {preview.viewer_status === "ok" && (
            <button className="btn-primary" onClick={accept} disabled={accepting}>
              {accepting ? "합류하는 중..." : "초대 수락하기"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
