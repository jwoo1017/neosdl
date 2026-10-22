import { useState } from "react";
import { agreePrivacy, logout } from "../api";
import { PrivacyDetails } from "./PrivacyNotice";

// 개인정보 동의 전 계정(GitHub 로 가입, 또는 정책 도입 전 가입)이 앱에 들어오면 먼저 보는 화면.
// 동의해야 나머지 화면이 열린다(App.jsx AppLayout).
export default function PrivacyGate({ me, onAgreed }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const agree = async () => {
    setBusy(true);
    setError(null);
    try {
      await agreePrivacy();
      onAgreed();
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const leave = async () => {
    await logout().catch(() => {});
    window.location.href = "/login";
  };

  return (
    <div className="auth-page">
      <div className="auth-card privacy-gate">
        <h1>개인정보 수집·이용 동의</h1>
        <p>{me?.name || me?.nickname}님, NeoSDL을 쓰려면 아래 내용에 동의해 주세요.</p>
        <PrivacyDetails />
        {error && <div className="alert-error">{error}</div>}
        <button type="button" className="btn-primary auth-submit" onClick={agree} disabled={busy}>
          {busy ? "처리 중..." : "동의하고 시작하기"}
        </button>
        <button type="button" className="auth-mode-toggle privacy-gate-leave" onClick={leave}>
          동의하지 않고 로그아웃
        </button>
      </div>
    </div>
  );
}
