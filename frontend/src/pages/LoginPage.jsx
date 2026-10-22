import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { githubLoginUrl, login, signup } from "../api";
import { describeAccountError, passwordChecks } from "../auth/accountPolicy";
import PrivacyConsent, { PasswordChecklist } from "../auth/PrivacyNotice";

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get("next") || "/workspace";
  const invitedEmail = searchParams.get("email") || "";
  // GitHub 로그인 거절·계정 비활성 등 서버가 리다이렉트로 넘긴 사유(app/github_oauth.py)
  const redirectError = searchParams.get("error");

  const [mode, setMode] = useState("login"); // "login" | "signup"
  const [email, setEmail] = useState(invitedEmail);
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [nickname, setNickname] = useState("");
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState(() => describeAccountError(redirectError));
  const [submitting, setSubmitting] = useState(false);

  const checks = useMemo(() => passwordChecks(password, email), [password, email]);
  const signupReady = checks.every((c) => c.ok) && agreed;

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "signup") {
        await signup(email, password, name, nickname, agreed);
        // 가입 직후 인증 코드가 발송된다 — 인증을 마쳐야 나머지 기능이 열린다.
        navigate(`/verify-email?sent=1&next=${encodeURIComponent(next)}`);
        return;
      }
      await login(email, password);
      navigate(next);
    } catch (err) {
      setError(describeAccountError(err.detail) || err.message);
      setSubmitting(false);
    }
  };

  const switchMode = () => {
    setMode((m) => (m === "signup" ? "login" : "signup"));
    setError(null);
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>NeoSDL</h1>
        <p>
          {mode === "signup"
            ? "회사 이메일로 가입하세요. 초대를 받았다면 초대받은 주소로 가입할 수 있어요."
            : "로그인하세요."}
        </p>

        <form className="auth-form" onSubmit={submit}>
          {mode === "signup" && (
            <>
              <input
                placeholder="이름 (실명)"
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={50}
                required
              />
              <input
                placeholder="닉네임 (화면에 표시, 30자 이하)"
                autoComplete="nickname"
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
                maxLength={30}
                required
              />
            </>
          )}
          <input
            type="email"
            placeholder={mode === "signup" ? "회사 이메일" : "이메일"}
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <input
            type="password"
            placeholder="비밀번호"
            autoComplete={mode === "signup" ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {mode === "signup" && (
            <>
              <PasswordChecklist checks={checks} />
              <PrivacyConsent checked={agreed} onChange={setAgreed} />
            </>
          )}
          {error && <div className="alert-error">{error}</div>}
          <button
            className="btn-primary auth-submit"
            type="submit"
            disabled={submitting || (mode === "signup" && !signupReady)}
          >
            {submitting ? "처리 중..." : mode === "signup" ? "가입하기" : "로그인"}
          </button>
          {mode === "login" && (
            <Link className="auth-forgot" to={`/forgot-password${email ? `?email=${encodeURIComponent(email)}` : ""}`}>
              비밀번호를 잊으셨나요?
            </Link>
          )}
        </form>

        <button type="button" className="auth-mode-toggle" onClick={switchMode}>
          {mode === "signup" ? "이미 계정이 있으신가요? 로그인" : "계정이 없으신가요? 회사 이메일로 가입"}
        </button>

        <div className="auth-divider">또는</div>

        {/* OAuth 리다이렉트 체인이라 fetch가 아니라 풀 페이지 이동이어야 한다. */}
        <a className="btn-github" href={githubLoginUrl(next)}>
          GitHub로 로그인
        </a>
        <p className="auth-footnote">GitHub로 처음 가입하려면 GitHub 계정에 인증된 회사 이메일이 있어야 해요.</p>
      </div>
    </div>
  );
}
