import { useState } from "react";
import { createWorkspaceInvite } from "../api";
import Modal from "./Modal";

export default function InviteMemberModal({ workspaceId, onInvited, onClose }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!email.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const invite = await createWorkspaceInvite(workspaceId, role, email.trim());
      setResult(invite);
      onInvited();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const copyLink = () => {
    navigator.clipboard?.writeText(result.invite_url).catch(() => {});
  };

  return (
    <Modal title="멤버 추가" onClose={onClose}>
      {!result ? (
        <form className="auth-form" onSubmit={submit}>
          <input
            autoFocus
            type="email"
            placeholder="초대할 이메일"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
          {error && <div className="alert-error">{error}</div>}
          <button className="btn-primary auth-submit" type="submit" disabled={submitting}>
            {submitting ? "초대하는 중..." : "초대 보내기"}
          </button>
        </form>
      ) : (
        <div>
          {result.email_sent ? (
            <p>
              <strong>{email}</strong> 주소로 초대 메일을 발송했습니다.
            </p>
          ) : (
            <div className="alert-error">
              메일을 보내지 못했습니다(SMTP 미설정 또는 발송 실패). 아래 링크를 직접 전달하세요.
            </div>
          )}
          <p className="invite-url">
            <code>{result.invite_url}</code>
          </p>
          <div className="wizard-actions">
            <button onClick={copyLink}>링크 복사</button>
            <button className="btn-primary" onClick={onClose}>
              닫기
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
