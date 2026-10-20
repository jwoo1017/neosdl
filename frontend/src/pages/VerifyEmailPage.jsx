import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { fetchMe, logout, sendVerificationCode, verifyEmailCode } from "../api";

// 백엔드 detail → 사용자 문구. invalid_code:<남은 횟수>, resend_cooldown:<초> 는 숫자를 떼서 쓴다.
function describeError(detail) {
  if (!detail) return "요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.";
  const [kind, value] = String(detail).split(":");
  switch (kind) {
    case "invalid_code":
      return `인증 코드가 올바르지 않습니다. (남은 시도 ${value}회)`;
    case "too_many_attempts":
      return "입력 횟수를 초과했습니다. 코드를 다시 받아 입력하세요.";
    case "code_expired":
      return "인증 코드가 만료되었습니다. 코드를 다시 받아 입력하세요.";
    case "resend_cooldown":
      return `${value}초 후에 다시 보낼 수 있습니다.`;
    case "too_many_requests":
      return "인증 메일을 너무 많이 요청했습니다. 1시간 후에 다시 시도하세요.";
    default:
      return detail;
  }
}

export default function VerifyEmailPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get("next") || "/workspace";

  const [me, setMe] = useState(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  // 가입 직후(sent=1)엔 방금 코드가 나갔으니 재발송 버튼을 쿨다운으로 시작한다.
  const [cooldown, setCooldown] = useState(searchParams.get("sent") ? 60 : 0); // 재발송 버튼 남은 초

  useEffect(() => {
    // 비로그인이면 fetchMe(apiFetch)가 401 을 잡아 /login 으로 보낸다.
    fetchMe().then((m) => {
      if (m.email_verified) navigate(next, { replace: true });
      else setMe(m);
    });
  }, [navigate, next]);

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const t = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      await verifyEmailCode(code.trim());
      navigate(next, { replace: true });
    } catch (err) {
      setError(describeError(err.detail));
      setSubmitting(false);
    }
  };

  const resend = async () => {
    setError(null);
    setNotice(null);
    try {
      const r = await sendVerificationCode();
      setNotice(r.sent ? "인증 코드를 다시 보냈습니다. 메일함(스팸함 포함)을 확인하세요." : "인증 코드를 발급했습니다.");
      setCooldown(r.resend_after || 60);
      setCode("");
    } catch (err) {
      setError(describeError(err.detail));
      const [kind, value] = String(err.detail || "").split(":");
      if (kind === "resend_cooldown") setCooldown(Number(value) || 60);
    }
  };

  const useOtherAccount = async () => {
    await logout();
    navigate("/login");
  };

  if (!me) return <div className="auth-page"><div className="auth-card"><p>불러오는 중...</p></div></div>;

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>이메일 인증</h1>
        <p>
          <strong>{me.email}</strong>로 보낸 6자리 인증 코드를 입력하세요.
          <br />
          코드는 10분 동안 유효합니다.
        </p>

        <form className="auth-form" onSubmit={submit}>
          <input
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="인증 코드 6자리"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            maxLength={6}
            required
            autoFocus
          />
          {error && <div className="alert-error">{error}</div>}
          {notice && <p className="muted">{notice}</p>}
          <button className="btn-primary auth-submit" type="submit" disabled={submitting || code.length !== 6}>
            {submitting ? "확인 중..." : "인증하기"}
          </button>
        </form>

        <button type="button" className="auth-mode-toggle" onClick={resend} disabled={cooldown > 0}>
          {cooldown > 0 ? `코드 다시 받기 (${cooldown}초)` : "코드를 받지 못했나요? 다시 받기"}
        </button>
        <button type="button" className="auth-mode-toggle" onClick={useOtherAccount}>
          다른 계정으로 로그인
        </button>
      </div>
    </div>
  );
}
