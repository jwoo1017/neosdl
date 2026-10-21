import { useEffect, useRef, useState } from "react";
import {
  changePassword, confirmEmailChange, deleteAvatar, fetchGithubAccounts, githubLoginUrl, requestEmailChange,
  setAvatarSource, unlinkGithubAccount, updateProfile, uploadAvatar, withdrawAccount,
} from "../api";
import Avatar from "../components/Avatar";
import { useMe } from "../MeContext";

// 백엔드 detail 코드 → 사용자 문구(app/me.py, app/avatars.py, app/email_verification.py).
const ERRORS = {
  invalid_password: "현재 비밀번호가 올바르지 않습니다.",
  email_already_registered: "이미 다른 계정이 사용 중인 이메일입니다.",
  same_email: "지금 사용 중인 이메일과 같습니다.",
  confirm_mismatch: "확인 문구를 정확히 입력하세요: 탈퇴합니다",
  unsupported_image_type: "PNG, JPEG, WebP 이미지만 올릴 수 있습니다.",
  invalid_image: "이미지 파일을 읽을 수 없습니다.",
  image_too_large: "이미지 해상도가 너무 큽니다.",
  file_too_large: "파일이 너무 큽니다(최대 2MB).",
  no_github_avatar: "연결된 GitHub 계정에 프로필 사진이 없습니다.",
  no_uploaded_avatar: "업로드한 사진이 없습니다.",
  code_expired: "인증 코드가 만료되었습니다. 다시 요청하세요.",
  too_many_attempts: "입력 횟수를 초과했습니다. 다시 요청하세요.",
  too_many_requests: "요청이 너무 많습니다. 1시간 후 다시 시도하세요.",
};
const KIND_LABEL = { workspace: "워크스페이스", teamspace: "팀스페이스", project: "프로젝트", installation: "GitHub App 설치" };

function describe(err) {
  const d = err?.detail;
  if (typeof d === "string") {
    const [kind, value] = d.split(":");
    if (kind === "invalid_code") return `인증 코드가 올바르지 않습니다. (남은 시도 ${value}회)`;
    if (kind === "resend_cooldown") return `${value}초 후에 다시 요청할 수 있습니다.`;
    if (ERRORS[d]) return ERRORS[d];
  }
  return err?.message || "요청을 처리하지 못했습니다.";
}

function Section({ title, description, children }) {
  return (
    <section className="mypage-section">
      <div className="mypage-section-head">
        <h2>{title}</h2>
        {description && <p className="muted">{description}</p>}
      </div>
      <div className="mypage-section-body">{children}</div>
    </section>
  );
}

function Status({ error, ok }) {
  if (error) return <div className="alert-error">{error}</div>;
  if (ok) return <p className="mypage-ok">{ok}</p>;
  return null;
}

function ProfileSection() {
  const { me, setMe } = useMe();
  const [name, setName] = useState(me.name);
  const [nickname, setNickname] = useState(me.nickname);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [ok, setOk] = useState(null);
  const fileRef = useRef(null);

  const run = async (fn, okText) => {
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const updated = await fn();
      if (updated) setMe((prev) => ({ ...prev, ...updated }));
      setOk(okText);
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  const onFile = (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // 같은 파일을 다시 골라도 change 가 나게
    if (file) run(() => uploadAvatar(file), "프로필 사진을 바꿨습니다.");
  };

  const dirty = name.trim() !== me.name || nickname.trim() !== me.nickname;

  return (
    <Section title="프로필" description="팀원에게 보이는 사진과 이름입니다. 화면에는 닉네임이 표시됩니다.">
      <div className="mypage-avatar-row">
        <Avatar profile={me} size={72} />
        <div className="mypage-avatar-actions">
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={onFile} />
          <button type="button" className="btn-primary" disabled={busy} onClick={() => fileRef.current?.click()}>
            사진 업로드
          </button>
          {me.avatar_source !== "initials" && (
            <button type="button" className="btn-secondary" disabled={busy}
                    onClick={() => run(() => setAvatarSource("initials"), "기본 아바타로 바꿨습니다.")}>
              기본 아바타로
            </button>
          )}
          {me.has_github_avatar && me.avatar_source !== "github" && (
            <button type="button" className="btn-secondary" disabled={busy}
                    onClick={() => run(() => setAvatarSource("github"), "GitHub 사진을 사용합니다.")}>
              GitHub 사진 사용
            </button>
          )}
          {me.avatar_source === "upload" && (
            <button type="button" className="btn-danger-text" disabled={busy}
                    onClick={() => run(deleteAvatar, "업로드한 사진을 삭제했습니다.")}>
              사진 삭제
            </button>
          )}
          <p className="muted mypage-hint">PNG·JPEG·WebP, 최대 2MB. 512px로 줄여 저장됩니다.</p>
        </div>
      </div>

      <form
        className="mypage-form"
        onSubmit={(e) => {
          e.preventDefault();
          run(() => updateProfile({ name: name.trim(), nickname: nickname.trim() }), "프로필을 저장했습니다.");
        }}
      >
        <label>
          이름
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={50} required />
        </label>
        <label>
          닉네임
          <input value={nickname} onChange={(e) => setNickname(e.target.value)} maxLength={100} required />
        </label>
        <div className="mypage-actions">
          <button className="btn-primary" type="submit" disabled={busy || !dirty}>저장</button>
        </div>
      </form>
      <Status error={error} ok={ok} />
    </Section>
  );
}

function EmailSection() {
  const { me, setMe } = useMe();
  const [newEmail, setNewEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [pending, setPending] = useState(null); // 코드를 보낸 새 주소
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [ok, setOk] = useState(null);

  const request = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      await requestEmailChange(newEmail.trim(), password);
      setPending(newEmail.trim());
      setPassword("");
      setOk(`${newEmail.trim()}(으)로 인증 코드를 보냈습니다. 10분 안에 입력하세요.`);
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const updated = await confirmEmailChange(code.trim());
      setMe((prev) => ({ ...prev, ...updated }));
      setPending(null);
      setNewEmail("");
      setCode("");
      setOk("이메일을 바꿨습니다. 다음 로그인부터 새 주소를 사용하세요.");
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="이메일" description={`현재: ${me.email || "없음(GitHub 로그인 전용)"}`}>
      {!pending ? (
        <form className="mypage-form" onSubmit={request}>
          <label>
            새 이메일
            <input type="email" value={newEmail} onChange={(e) => setNewEmail(e.target.value)} required />
          </label>
          <label>
            현재 비밀번호
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                   autoComplete="current-password" placeholder="비밀번호가 없는 계정은 비워두세요" />
          </label>
          <div className="mypage-actions">
            <button className="btn-primary" type="submit" disabled={busy || !newEmail}>인증 코드 받기</button>
          </div>
        </form>
      ) : (
        <form className="mypage-form" onSubmit={confirm}>
          <label>
            {pending} 로 받은 인증 코드
            <input inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
                   onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} required />
          </label>
          <div className="mypage-actions">
            <button className="btn-secondary" type="button" onClick={() => { setPending(null); setOk(null); }}>
              취소
            </button>
            <button className="btn-primary" type="submit" disabled={busy || code.length !== 6}>변경 확인</button>
          </div>
        </form>
      )}
      <Status error={error} ok={ok} />
    </Section>
  );
}

function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmNext, setConfirmNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [ok, setOk] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setOk(null);
    if (next !== confirmNext) {
      setError("새 비밀번호가 서로 다릅니다.");
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirmNext("");
      setOk("비밀번호를 바꿨습니다. 다른 기기에서는 다시 로그인해야 합니다.");
    } catch (err) {
      setError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="비밀번호" description="바꾸면 이 기기를 뺀 다른 모든 기기에서 로그아웃됩니다.">
      <form className="mypage-form" onSubmit={submit}>
        <label>
          현재 비밀번호
          <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)}
                 autoComplete="current-password" placeholder="처음 설정하는 경우 비워두세요" />
        </label>
        <label>
          새 비밀번호
          <input type="password" value={next} onChange={(e) => setNext(e.target.value)}
                 autoComplete="new-password" minLength={8} required />
        </label>
        <label>
          새 비밀번호 확인
          <input type="password" value={confirmNext} onChange={(e) => setConfirmNext(e.target.value)}
                 autoComplete="new-password" minLength={8} required />
        </label>
        <div className="mypage-actions">
          <button className="btn-primary" type="submit" disabled={busy || next.length < 8}>비밀번호 변경</button>
        </div>
      </form>
      <Status error={error} ok={ok} />
    </Section>
  );
}

function GithubSection() {
  const [accounts, setAccounts] = useState(null);
  const [error, setError] = useState(null);

  const load = () => fetchGithubAccounts().then(setAccounts).catch((err) => setError(describe(err)));
  useEffect(() => {
    load();
  }, []);

  const unlink = async (id) => {
    setError(null);
    try {
      await unlinkGithubAccount(id);
      await load();
    } catch (err) {
      setError(describe(err));
    }
  };

  return (
    <Section title="연결된 GitHub 계정" description="저장소 연동과 GitHub 로그인에 사용됩니다.">
      {accounts === null ? (
        <p className="muted">불러오는 중...</p>
      ) : accounts.length === 0 ? (
        <p className="muted">연결된 계정이 없습니다.</p>
      ) : (
        <ul className="mypage-list">
          {accounts.map((a) => (
            <li key={a.id}>
              {a.avatar_url && <img src={a.avatar_url} alt="" className="mypage-gh-avatar" />}
              <span>{a.github_login}</span>
              <button type="button" className="btn-danger-text" onClick={() => unlink(a.id)}>연결 해제</button>
            </li>
          ))}
        </ul>
      )}
      <div className="mypage-actions">
        {/* OAuth 리다이렉트 체인이라 풀 페이지 이동 — 로그인된 상태면 콜백이 "연동" 으로 처리한다 */}
        <a className="btn-secondary" href={githubLoginUrl("/me")}>GitHub 계정 연결</a>
      </div>
      <Status error={error} />
    </Section>
  );
}

function WithdrawSection() {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [blockers, setBlockers] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setBlockers(null);
    try {
      await withdrawAccount(password, confirmText);
      window.location.href = "/login";
    } catch (err) {
      if (err.status === 409 && err.detail?.items) setBlockers(err.detail.items);
      else setError(describe(err));
      setBusy(false);
    }
  };

  return (
    <Section title="회원 탈퇴" description="탈퇴하면 이름·이메일 등 개인정보가 삭제되고 되돌릴 수 없습니다. 내가 만든 기록은 '탈퇴한 사용자'로 남습니다.">
      {!open ? (
        <button type="button" className="btn-danger-text" onClick={() => setOpen(true)}>회원 탈퇴 진행</button>
      ) : (
        <form className="mypage-form" onSubmit={submit}>
          <label>
            비밀번호
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                   autoComplete="current-password" placeholder="비밀번호가 없는 계정은 비워두세요" />
          </label>
          <label>
            확인을 위해 <strong>탈퇴합니다</strong> 를 입력하세요
            <input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} />
          </label>
          <div className="mypage-actions">
            <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>취소</button>
            <button type="submit" className="btn-danger" disabled={busy || confirmText !== "탈퇴합니다"}>
              탈퇴하기
            </button>
          </div>
        </form>
      )}
      {blockers && (
        <div className="alert-error">
          <p>먼저 관리자 권한을 다른 사람에게 넘기거나 정리해야 하는 항목이 있습니다.</p>
          <ul>
            {blockers.map((b) => (
              <li key={`${b.kind}-${b.id}`}>{KIND_LABEL[b.kind] || b.kind}: {b.name}</li>
            ))}
          </ul>
        </div>
      )}
      <Status error={error} />
    </Section>
  );
}

export default function MyPage() {
  const { me } = useMe();
  if (!me) return null;
  return (
    <div className="dashboard mypage">
      <h1>마이페이지</h1>
      <ProfileSection />
      <EmailSection />
      <PasswordSection />
      <GithubSection />
      <WithdrawSection />
    </div>
  );
}
