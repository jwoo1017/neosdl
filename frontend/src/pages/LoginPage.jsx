import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { githubLoginUrl, login, signup } from "../api";

export default function LoginPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get("next") || "/workspace";
  const invitedEmail = searchParams.get("email") || "";

  const [mode, setMode] = useState("login"); // "login" | "signup"
  const [email, setEmail] = useState(invitedEmail);
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      if (mode === "signup") {
        await signup(email, password, nickname);
        // 가입 직후 인증 코드가 발송된다 — 인증을 마쳐야 나머지 기능이 열린다.
        navigate(`/verify-email?sent=1&next=${encodeURIComponent(next)}`);
        return;
      }
      await login(email, password);
      navigate(next);
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h1>NeoSDL</h1>
        <p>{mode === "signup" ? "이메일로 가입하세요." : "로그인하세요."}</p>

        <form className="auth-form" onSubmit={submit}>
          {mode === "signup" && (
            <input
              placeholder="닉네임"
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
              required
            />
          )}
          <input
            type="email"
            placeholder="이메일"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <input
            type="password"
            placeholder="비밀번호"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={mode === "signup" ? 8 : undefined}
            required
          />
          {error && <div className="alert-error">{error}</div>}
          <button className="btn-primary auth-submit" type="submit" disabled={submitting}>
            {submitting ? "처리 중..." : mode === "signup" ? "가입하기" : "로그인"}
          </button>
        </form>

        <button
          type="button"
          className="auth-mode-toggle"
          onClick={() => {
            setMode((m) => (m === "signup" ? "login" : "signup"));
            setError(null);
          }}
        >
          {mode === "signup" ? "이미 계정이 있으신가요? 로그인" : "계정이 없으신가요? 이메일로 가입"}
        </button>

        <div className="auth-divider">또는</div>

        {/* OAuth 리다이렉트 체인이라 fetch가 아니라 풀 페이지 이동이어야 한다. */}
        <a className="btn-github" href={githubLoginUrl(next)}>
          GitHub로 로그인
        </a>
      </div>
    </div>
  );
}
