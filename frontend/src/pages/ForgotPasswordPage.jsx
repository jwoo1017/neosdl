import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { confirmPasswordReset, requestPasswordReset } from "../api";
import { describeAccountError, passwordChecks } from "../auth/accountPolicy";
import { PasswordChecklist } from "../auth/PrivacyNotice";

// 비밀번호 찾기: 이메일 → 6자리 코드 + 새 비밀번호 → 완료(app/github_oauth.py password-reset).
// 서버는 계정이 없어도 같은 응답을 주므로 화면도 "보냈어요"만 말한다(가입 여부를 드러내지 않음).
export default function ForgotPasswordPage() {
  const [searchParams] = useSearchParams();
  const [step, setStep] = useState("email"); // email | code | done
  const [email, setEmail] = useState(searchParams.get("email") || "");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [cooldown, setCooldown] = useState(0);

  const checks = useMemo(() => passwordChecks(password, email), [password, email]);
  const ready = code.length === 6 && checks.every((c) => c.ok) && password === confirm;

  useEffect(() => {
    if (cooldown <= 0) return undefined;
    const t = setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  const sendCode = async (e) => {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await requestPasswordReset(email.trim());
      setCooldown(r.resend_after || 60);
      setNotice("가입된 이메일이면 재설정 코드를 보냈어요. 메일함(스팸함 포함)을 확인하세요.");
      setStep("code");
    } catch (err) {
      setError(describeAccountError(err.detail) || err.message);
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await confirmPasswordReset(email.trim(), code, password);
      setStep("done");
    } catch (err) {
      setError(describeAccountError(err.detail) || err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>비밀번호 찾기</h1>

        {step === "email" && (
          <>
            <p>가입한 이메일을 입력하면 재설정 코드를 보내 드려요.</p>
            <form className="auth-form" onSubmit={sendCode}>
              <input type="email" placeholder="이메일" autoComplete="email" value={email} autoFocus required
                     onChange={(e) => setEmail(e.target.value)} />
              {error && <div className="alert-error">{error}</div>}
              <button className="btn-primary auth-submit" type="submit" disabled={busy || !email.trim()}>
                {busy ? "보내는 중..." : "재설정 코드 받기"}
              </button>
            </form>
          </>
        )}

        {step === "code" && (
          <>
            <p><strong>{email}</strong>{notice ? ` — ${notice}` : ""}</p>
            <form className="auth-form" onSubmit={submit}>
              <input inputMode="numeric" autoComplete="one-time-code" placeholder="재설정 코드 6자리" value={code}
                     autoFocus required maxLength={6}
                     onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} />
              <input type="password" placeholder="새 비밀번호" autoComplete="new-password" value={password} required
                     onChange={(e) => setPassword(e.target.value)} />
              <input type="password" placeholder="새 비밀번호 확인" autoComplete="new-password" value={confirm} required
                     onChange={(e) => setConfirm(e.target.value)} />
              <PasswordChecklist
                checks={[...checks, { key: "match", label: "두 비밀번호가 같음", ok: !!password && password === confirm }]}
              />
              {error && <div className="alert-error">{error}</div>}
              <button className="btn-primary auth-submit" type="submit" disabled={busy || !ready}>
                {busy ? "바꾸는 중..." : "비밀번호 바꾸기"}
              </button>
            </form>
            <button type="button" className="auth-mode-toggle" onClick={() => sendCode()} disabled={cooldown > 0 || busy}>
              {cooldown > 0 ? `코드 다시 받기 (${cooldown}초)` : "코드를 받지 못했나요? 다시 받기"}
            </button>
          </>
        )}

        {step === "done" && (
          <>
            <p>비밀번호를 바꿨어요. 보안을 위해 모든 기기에서 로그아웃됐어요. 새 비밀번호로 로그인하세요.</p>
            <Link className="btn-primary auth-submit auth-link-btn" to={`/login?email=${encodeURIComponent(email)}`}>
              로그인하러 가기
            </Link>
          </>
        )}

        {step !== "done" && (
          <Link className="auth-mode-toggle auth-back-link" to="/login">로그인으로 돌아가기</Link>
        )}
      </div>
    </div>
  );
}
