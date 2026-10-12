import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { acceptProjectInvite, fetchProjectInvitePreview, logout } from "../api";
import { activeProjectStorageKey } from "../ProjectContext";

// WorkspaceContext가 쓰는 것과 같은 키 — 로그인 전에는 그 context가 없어
// 수락 후 풀 페이지 이동 전에 직접 써 둔다.
const ACTIVE_WORKSPACE_KEY = "neosdl_active_workspace_id";

export default function ProjectInviteAcceptPage() {
  const { token } = useParams();
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [accepting, setAccepting] = useState(false);

  useEffect(() => {
    fetchProjectInvitePreview(token).then(setPreview).catch((err) => setError(err.message));
  }, [token]);

  const accept = async () => {
    setAccepting(true);
    try {
      const result = await acceptProjectInvite(token);
      // ProjectProvider가 workspace 전환 시 이 키를 읽어 자동으로 이
      // project를 활성화한다 — workspace 멤버가 아니어도(project 소속과
      // workspace 소속은 독립적이다) project 목록 자체는 뜬다.
      localStorage.setItem(activeProjectStorageKey(result.workspace_id), result.project_id);
      localStorage.setItem(ACTIVE_WORKSPACE_KEY, result.workspace_id);
      window.location.href = `/projects/${result.project_id}/chat`;
    } catch (err) {
      setError(err.message);
      setAccepting(false);
    }
  };

  const loginNext = `/project-invites/${token}`;
  const loginHref = preview?.target_email
    ? `/login?next=${encodeURIComponent(loginNext)}&email=${encodeURIComponent(preview.target_email)}`
    : `/login?next=${encodeURIComponent(loginNext)}`;

  return (
    <div className="dashboard">
      <h1>프로젝트 초대</h1>
      {error && <div className="alert-error">{error}</div>}
      {!preview && !error && <p className="muted">불러오는 중...</p>}
      {preview && (
        <div className="dashboard-empty">
          <p>
            <strong>{preview.invited_by_nickname}</strong>님이 <strong>{preview.project_name}</strong> 프로젝트에
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
