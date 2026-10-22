import { useState } from "react";

// 개인정보 수집·이용 안내(가입 폼, GitHub 가입 후 첫 화면 동의에서 같이 쓴다).
const PRIVACY_ITEMS = [
  ["수집 항목", "이메일, 이름, 닉네임, 비밀번호(암호화 저장), 프로필 사진(선택), 연결한 GitHub 계정 정보, 접속·이용 기록"],
  ["이용 목적", "회원 식별과 로그인, 협업 공간·채팅 등 서비스 제공, 보안 사고 대응과 감사 기록"],
  ["보유 기간", "회원 탈퇴 시까지. 단, 보안 감사 기록은 관련 법령과 사내 보안 정책이 정한 기간 동안 보관"],
];

export function PrivacyDetails() {
  return (
    <dl className="privacy-details">
      {PRIVACY_ITEMS.map(([k, v]) => (
        <div key={k} className="privacy-details-row">
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
      <p className="privacy-details-note">동의하지 않을 수 있지만, 동의하지 않으면 서비스를 이용할 수 없어요.</p>
    </dl>
  );
}

export default function PrivacyConsent({ checked, onChange }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="privacy-consent">
      <label className="privacy-consent-label">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span><strong>[필수]</strong> 개인정보 수집·이용에 동의합니다</span>
        <button type="button" className="link-btn privacy-consent-toggle" onClick={() => setOpen((v) => !v)}
                aria-expanded={open}>
          {open ? "접기" : "자세히"}
        </button>
      </label>
      {open && <PrivacyDetails />}
    </div>
  );
}

// 비밀번호 규칙 체크리스트(입력하는 동안 실시간)
export function PasswordChecklist({ checks }) {
  return (
    <ul className="pw-checklist" aria-label="비밀번호 규칙">
      {checks.map((c) => (
        <li key={c.key} className={c.ok ? "pw-check-ok" : ""}>
          <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
            {c.ok
              ? <path d="M2.5 6.2l2.2 2.2 4.8-4.9" fill="none" stroke="currentColor" strokeWidth="1.6"
                      strokeLinecap="round" strokeLinejoin="round" />
              : <circle cx="6" cy="6" r="2" fill="currentColor" />}
          </svg>
          {c.label}
        </li>
      ))}
    </ul>
  );
}
