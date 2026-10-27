import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  changePassword, confirmEmailChange, deleteAvatar, fetchGithubAccounts, githubLoginUrl, requestEmailChange,
  setAvatarSource, unlinkGithubAccount, updateProfile, uploadAvatar, withdrawAccount,
} from "../api";
import Avatar from "../components/Avatar";
import Modal from "../components/Modal";
import {
  ChevronDownIcon, DangerTabIcon, GithubMarkIcon, KeyTabIcon, LinkTabIcon, UserTabIcon,
} from "../icons/SettingsIcons";
import { useMe } from "../MeContext";
import { describeAccountError, passwordChecks } from "../auth/accountPolicy";
import { PasswordChecklist } from "../auth/PrivacyNotice";

// 설정 화면 — 가운데 정렬 컨테이너, 왼쪽 탭 + 오른쪽 본문(GitHub/Notion/Linear 설정 방식).
// 각 항목은 "라벨 | 현재 값 | 변경" 행이고, 변경을 누르면 그 행 안에서 편집 영역이 펼쳐진다.
// 현재 탭은 ?tab= 로 유지해 새로고침·링크 공유에도 같은 화면이 열린다.

const TABS = [
  { id: "profile", label: "프로필", Icon: UserTabIcon },
  { id: "account", label: "계정", Icon: KeyTabIcon },
  { id: "connections", label: "연결된 계정", Icon: LinkTabIcon },
  { id: "danger", label: "계정 삭제", Icon: DangerTabIcon, danger: true },
];

// 백엔드 detail 코드 → 사용자 문구(app/me.py, app/avatars.py, app/email_verification.py).
const ERRORS = {
  invalid_password: "현재 비밀번호가 올바르지 않습니다.",
  email_already_registered: "이미 다른 계정이 사용 중인 이메일입니다.",
  same_email: "지금 사용 중인 이메일과 같습니다.",
  confirm_mismatch: "확인 문구를 정확히 입력하세요.",
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
const CONFIRM_TEXT = "탈퇴합니다";

function describe(err) {
  const d = err?.detail;
  const policyText = describeAccountError(d);   // 비밀번호 규칙·잠금 등 계정 정책 문구(auth/accountPolicy.js)
  if (policyText && !String(d).startsWith("invalid_code") && !String(d).startsWith("resend_cooldown")) {
    return policyText;
  }
  if (typeof d === "string") {
    const [kind, value] = d.split(":");
    if (kind === "invalid_code") return `인증 코드가 올바르지 않습니다. (남은 시도 ${value}회)`;
    if (kind === "resend_cooldown") return `${value}초 후에 다시 요청할 수 있습니다.`;
    if (ERRORS[d]) return ERRORS[d];
  }
  return err?.message || "요청을 처리하지 못했습니다.";
}

// 비동기 작업 + 행 단위 결과 메시지.
function useAction() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null); // { type: "ok" | "error", text }
  const run = async (fn, okText) => {
    setBusy(true);
    setMsg(null);
    try {
      const result = await fn();
      if (okText) setMsg({ type: "ok", text: okText });
      return result;
    } catch (err) {
      setMsg({ type: "error", text: describe(err) });
      throw err;
    } finally {
      setBusy(false);
    }
  };
  return { busy, msg, setMsg, run };
}

function InlineMessage({ msg }) {
  if (!msg) return null;
  return <p className={`mp-msg mp-msg-${msg.type}`} role={msg.type === "error" ? "alert" : "status"}>{msg.text}</p>;
}

function SettingRow({ label, description, value, action, children, msg }) {
  return (
    <div className="mp-row">
      <div className="mp-row-main">
        <div className="mp-row-label">
          <span>{label}</span>
          {description && <small>{description}</small>}
        </div>
        <div className="mp-row-value">{value}</div>
        <div className="mp-row-action">{action}</div>
      </div>
      {children && <div className="mp-row-expand">{children}</div>}
      <InlineMessage msg={msg} />
    </div>
  );
}

function Card({ title, description, children, danger = false }) {
  return (
    <section className={`mp-card ${danger ? "mp-card-danger" : ""}`}>
      {(title || description) && (
        <header className="mp-card-head">
          {title && <h2>{title}</h2>}
          {description && <p>{description}</p>}
        </header>
      )}
      <div className="mp-card-body">{children}</div>
    </section>
  );
}

// ------------------------------------------------------------------ 프로필 탭

function AvatarMenu({ me, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const pick = (fn) => {
    setOpen(false);
    fn();
  };

  return (
    <div className="mp-menu" ref={ref}>
      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0];
          e.target.value = ""; // 같은 파일을 다시 골라도 change 가 나게
          if (file) onChange("upload", file);
        }}
      />
      <button type="button" className="mp-btn mp-btn-secondary" aria-haspopup="menu" aria-expanded={open}
              onClick={() => setOpen((v) => !v)}>
        변경 <ChevronDownIcon />
      </button>
      {open && (
        <div className="mp-menu-list" role="menu">
          <button type="button" role="menuitem" onClick={() => pick(() => fileRef.current?.click())}>
            사진 업로드…
          </button>
          {me.has_github_avatar && me.avatar_source !== "github" && (
            <button type="button" role="menuitem" onClick={() => pick(() => onChange("github"))}>
              GitHub 사진 사용
            </button>
          )}
          {me.avatar_source !== "initials" && (
            <button type="button" role="menuitem" onClick={() => pick(() => onChange("initials"))}>
              기본 아바타로
            </button>
          )}
          {me.avatar_source === "upload" && (
            <button type="button" role="menuitem" className="mp-menu-danger" onClick={() => pick(() => onChange("delete"))}>
              업로드한 사진 삭제
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function EditableTextRow({ label, description, field, value, maxLength, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const { busy, msg, setMsg, run } = useAction();

  const start = () => {
    setDraft(value);
    setMsg(null);
    setEditing(true);
  };
  const save = async (e) => {
    e.preventDefault();
    try {
      const updated = await run(() => updateProfile({ [field]: draft.trim() }), `${label}을(를) 저장했습니다.`);
      onSaved(updated);
      setEditing(false);
    } catch {
      /* 메시지는 useAction 이 표시 */
    }
  };

  return (
    <SettingRow
      label={label}
      description={description}
      value={!editing && <span className="mp-value">{value}</span>}
      action={!editing && <button type="button" className="mp-btn mp-btn-ghost" onClick={start}>수정</button>}
      msg={msg}
    >
      {editing && (
        <form className="mp-inline-form" onSubmit={save}>
          <input autoFocus value={draft} maxLength={maxLength} onChange={(e) => setDraft(e.target.value)} required />
          <div className="mp-form-actions">
            <button type="button" className="mp-btn mp-btn-secondary" onClick={() => setEditing(false)}>취소</button>
            <button type="submit" className="mp-btn mp-btn-primary" disabled={busy || !draft.trim() || draft.trim() === value}>
              저장
            </button>
          </div>
        </form>
      )}
    </SettingRow>
  );
}

function ProfileTab() {
  const { me, setMe } = useMe();
  const { busy, msg, run } = useAction();
  const merge = (updated) => updated && setMe((prev) => ({ ...prev, ...updated }));

  const changeAvatar = (kind, file) => {
    const actions = {
      upload: [() => uploadAvatar(file), "프로필 사진을 바꿨습니다."],
      github: [() => setAvatarSource("github"), "GitHub 사진을 사용합니다."],
      initials: [() => setAvatarSource("initials"), "기본 아바타로 바꿨습니다."],
      delete: [deleteAvatar, "업로드한 사진을 삭제했습니다."],
    };
    const [fn, ok] = actions[kind];
    run(fn, ok).then(merge).catch(() => {});
  };

  return (
    <Card title="프로필" description="팀원에게 보이는 정보입니다. 화면에는 닉네임이 표시됩니다.">
      <SettingRow
        label="프로필 사진"
        description="PNG·JPEG·WebP, 최대 2MB"
        value={<span className={busy ? "mp-avatar-busy" : ""}><Avatar profile={me} size={48} /></span>}
        action={<AvatarMenu me={me} onChange={changeAvatar} />}
        msg={msg}
      />
      <EditableTextRow label="이름" description="실명 — 멤버 목록에 함께 표시" field="name"
                       value={me.name} maxLength={50} onSaved={merge} />
      <EditableTextRow label="닉네임" description="화면 표시 이름" field="nickname"
                       value={me.nickname} maxLength={100} onSaved={merge} />
    </Card>
  );
}

// ------------------------------------------------------------------ 계정 탭

function EmailRow() {
  const { me, setMe } = useMe();
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(1); // 1: 새 주소 입력, 2: 코드 확인
  const [newEmail, setNewEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const { busy, msg, setMsg, run } = useAction();

  const reset = () => {
    setOpen(false);
    setStep(1);
    setNewEmail("");
    setPassword("");
    setCode("");
  };

  const request = async (e) => {
    e.preventDefault();
    try {
      await run(() => requestEmailChange(newEmail.trim(), password));
      setPassword("");
      setStep(2);
      setMsg({ type: "ok", text: `${newEmail.trim()} 로 인증 코드를 보냈습니다. 10분 안에 입력하세요.` });
    } catch {
      /* useAction 이 표시 */
    }
  };
  const confirm = async (e) => {
    e.preventDefault();
    try {
      const updated = await run(() => confirmEmailChange(code.trim()), "이메일을 바꿨습니다. 다음 로그인부터 새 주소를 사용하세요.");
      setMe((prev) => ({ ...prev, ...updated }));
      reset();
    } catch {
      /* useAction 이 표시 */
    }
  };

  return (
    <SettingRow
      label="이메일"
      description="로그인과 초대 수신에 사용"
      value={<span className="mp-value">{me.email || "없음 (GitHub 로그인 전용)"}</span>}
      action={!open && <button type="button" className="mp-btn mp-btn-ghost" onClick={() => { setMsg(null); setOpen(true); }}>변경</button>}
      msg={msg}
    >
      {open && (
        <div className="mp-steps">
          <ol className="mp-stepper">
            <li className={step === 1 ? "current" : "done"}>새 주소 입력</li>
            <li className={step === 2 ? "current" : ""}>인증 코드 확인</li>
          </ol>
          {step === 1 ? (
            <form className="mp-inline-form" onSubmit={request}>
              <input type="email" placeholder="새 이메일 주소" value={newEmail} autoFocus
                     onChange={(e) => setNewEmail(e.target.value)} required />
              <input type="password" placeholder="현재 비밀번호 (없는 계정은 비워두세요)" value={password}
                     autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
              <div className="mp-form-actions">
                <button type="button" className="mp-btn mp-btn-secondary" onClick={reset}>취소</button>
                <button type="submit" className="mp-btn mp-btn-primary" disabled={busy || !newEmail}>인증 코드 받기</button>
              </div>
            </form>
          ) : (
            <form className="mp-inline-form" onSubmit={confirm}>
              <input inputMode="numeric" autoComplete="one-time-code" placeholder="6자리 인증 코드" maxLength={6} autoFocus
                     value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))} required />
              <div className="mp-form-actions">
                <button type="button" className="mp-btn mp-btn-secondary" onClick={() => setStep(1)}>이전</button>
                <button type="submit" className="mp-btn mp-btn-primary" disabled={busy || code.length !== 6}>변경 확인</button>
              </div>
            </form>
          )}
        </div>
      )}
    </SettingRow>
  );
}

function PasswordRow() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirmNext, setConfirmNext] = useState("");
  const { busy, msg, setMsg, run } = useAction();
  const { me } = useMe();
  const checks = passwordChecks(next, me?.email);

  const close = () => {
    setOpen(false);
    setCurrent("");
    setNext("");
    setConfirmNext("");
  };
  const submit = async (e) => {
    e.preventDefault();
    if (next !== confirmNext) {
      setMsg({ type: "error", text: "새 비밀번호가 서로 다릅니다." });
      return;
    }
    try {
      await run(() => changePassword(current, next), "비밀번호를 바꿨습니다. 다른 기기에서는 다시 로그인해야 합니다.");
      close();
    } catch {
      /* useAction 이 표시 */
    }
  };

  return (
    <SettingRow
      label="비밀번호"
      description="바꾸면 이 기기를 뺀 모든 기기에서 로그아웃됩니다"
      value={<span className="mp-value mp-value-mask">••••••••</span>}
      action={!open && <button type="button" className="mp-btn mp-btn-ghost" onClick={() => { setMsg(null); setOpen(true); }}>변경</button>}
      msg={msg}
    >
      {open && (
        <form className="mp-inline-form" onSubmit={submit}>
          <input type="password" placeholder="현재 비밀번호 (처음 설정이면 비워두세요)" value={current} autoFocus
                 autoComplete="current-password" onChange={(e) => setCurrent(e.target.value)} />
          <input type="password" placeholder="새 비밀번호" value={next} required
                 autoComplete="new-password" onChange={(e) => setNext(e.target.value)} />
          <input type="password" placeholder="새 비밀번호 확인" value={confirmNext} required
                 autoComplete="new-password" onChange={(e) => setConfirmNext(e.target.value)} />
          <PasswordChecklist checks={checks} />
          <div className="mp-form-actions">
            <button type="button" className="mp-btn mp-btn-secondary" onClick={close}>취소</button>
            <button type="submit" className="mp-btn mp-btn-primary" disabled={busy || !checks.every((c) => c.ok)}>
              비밀번호 변경
            </button>
          </div>
        </form>
      )}
    </SettingRow>
  );
}

function AccountTab() {
  return (
    <Card title="계정" description="로그인 정보를 관리합니다.">
      <EmailRow />
      <PasswordRow />
    </Card>
  );
}

// ------------------------------------------------------------------ 연결된 계정 탭

function ConnectionsTab() {
  const [accounts, setAccounts] = useState(null);
  const { msg, run } = useAction();

  const load = () => fetchGithubAccounts().then(setAccounts).catch(() => setAccounts([]));
  useEffect(() => {
    load();
  }, []);

  const unlink = (id) => run(() => unlinkGithubAccount(id), "연결을 해제했습니다.").then(load).catch(() => {});

  return (
    <Card title="연결된 계정" description="GitHub 계정은 로그인과 저장소 연동에 사용됩니다.">
      {accounts === null ? (
        <p className="mp-empty">불러오는 중…</p>
      ) : accounts.length === 0 ? (
        <p className="mp-empty">연결된 GitHub 계정이 없습니다.</p>
      ) : (
        accounts.map((a) => (
          <SettingRow
            key={a.id}
            label={<span className="mp-gh-label"><GithubMarkIcon /> GitHub</span>}
            value={
              <span className="mp-gh-account">
                {a.avatar_url && <img src={a.avatar_url} alt="" />}
                <span className="mp-value">{a.github_login}</span>
              </span>
            }
            action={<button type="button" className="mp-btn mp-btn-ghost mp-btn-danger-text" onClick={() => unlink(a.id)}>연결 해제</button>}
          />
        ))
      )}
      <InlineMessage msg={msg} />
      <div className="mp-card-foot">
        {/* OAuth 리다이렉트 체인이라 풀 페이지 이동 — 로그인된 상태면 콜백이 "연동"으로 처리한다 */}
        <a className="mp-btn mp-btn-secondary" href={githubLoginUrl("/me?tab=connections")}>
          <GithubMarkIcon /> GitHub 계정 연결
        </a>
      </div>
    </Card>
  );
}

// ------------------------------------------------------------------ 계정 삭제 탭

function DeleteAccountModal({ onClose }) {
  const [password, setPassword] = useState("");
  const [confirmText, setConfirmText] = useState("");
  const [blockers, setBlockers] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

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
    <Modal title="계정을 삭제할까요?" onClose={onClose}>
      <form className="mp-modal-form" onSubmit={submit}>
        <p className="mp-modal-warn">
          이름·이메일·프로필 사진이 삭제되고 되돌릴 수 없습니다. 내가 만든 기록은 "탈퇴한 사용자"로 남습니다.
        </p>
        <label>
          비밀번호
          <input type="password" value={password} autoFocus autoComplete="current-password"
                 placeholder="비밀번호가 없는 계정은 비워두세요" onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label>
          <span>확인을 위해 <strong>{CONFIRM_TEXT}</strong>를 입력하세요</span>
          <input value={confirmText} onChange={(e) => setConfirmText(e.target.value)} />
        </label>
        {blockers && (
          <div className="mp-blockers" role="alert">
            <p>아래 항목의 관리자 권한을 다른 사람에게 넘기거나 정리한 뒤 다시 시도하세요.</p>
            <ul>
              {blockers.map((b) => (
                <li key={`${b.kind}-${b.id}`}><span>{KIND_LABEL[b.kind] || b.kind}</span>{b.name}</li>
              ))}
            </ul>
          </div>
        )}
        {error && <p className="mp-msg mp-msg-error" role="alert">{error}</p>}
        <div className="mp-form-actions">
          <button type="button" className="mp-btn mp-btn-secondary" onClick={onClose}>취소</button>
          <button type="submit" className="mp-btn mp-btn-danger" disabled={busy || confirmText !== CONFIRM_TEXT}>
            계정 삭제
          </button>
        </div>
      </form>
    </Modal>
  );
}

function DangerTab() {
  const [open, setOpen] = useState(false);
  return (
    <Card title="계정 삭제" description="계정을 삭제하면 다시 복구할 수 없습니다." danger>
      <div className="mp-danger-row">
        <div>
          <strong>내 계정 삭제</strong>
          <p>혼자 소유자인 워크스페이스·팀스페이스·프로젝트가 있으면 먼저 권한을 넘겨야 합니다.</p>
        </div>
        <button type="button" className="mp-btn mp-btn-danger" onClick={() => setOpen(true)}>계정 삭제</button>
      </div>
      {open && <DeleteAccountModal onClose={() => setOpen(false)} />}
    </Card>
  );
}

// ------------------------------------------------------------------ 페이지

export default function MyPage() {
  const { me } = useMe();
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "profile";
  if (!me) return null;

  return (
    <div className="mp-page">
      <div className="mp-container">
        <header className="mp-hero">
          <Avatar profile={me} size={64} />
          <div className="mp-hero-text">
            <h1>{me.nickname}</h1>
            <p>
              {me.name && me.name !== me.nickname && <span>{me.name}</span>}
              {me.email && <span>{me.email}</span>}
            </p>
          </div>
        </header>

        <div className="mp-layout">
          <nav className="mp-tabs" aria-label="설정 메뉴">
            {TABS.map(({ id, label, Icon, danger }) => (
              <button
                key={id}
                type="button"
                className={`mp-tab ${tab === id ? "active" : ""} ${danger ? "mp-tab-danger" : ""}`}
                aria-current={tab === id ? "page" : undefined}
                onClick={() => setParams({ tab: id }, { replace: true })}
              >
                <Icon /> <span>{label}</span>
              </button>
            ))}
          </nav>
          <div className="mp-content">
            {tab === "profile" && <ProfileTab />}
            {tab === "account" && <AccountTab />}
            {tab === "connections" && <ConnectionsTab />}
            {tab === "danger" && <DangerTab />}
          </div>
        </div>
      </div>
    </div>
  );
}
