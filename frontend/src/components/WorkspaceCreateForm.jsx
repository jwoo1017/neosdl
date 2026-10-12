import { useState } from "react";
import { createWorkspace, createWorkspaceInvite } from "../api";
import PersonIcon from "../icons/PersonIcon";
import BuildingIcon from "../icons/BuildingIcon";

const TYPES = [
  {
    value: "personal",
    label: "개인",
    Icon: PersonIcon,
    tagline: "혼자 또는 소규모로 시작할 때",
    description:
      "개인 프로젝트나 사이드 작업을 정리하기에 좋아요. 지금은 혼자 쓰다가 나중에 팀원을 초대해 함께 쓸 수도 있습니다.",
  },
  {
    value: "company",
    label: "회사",
    Icon: BuildingIcon,
    tagline: "팀·조직 단위로 함께 쓸 때",
    description:
      "팀스페이스로 부서나 팀을 나누고, 멤버별 권한과 프로젝트 접근을 관리할 수 있어요. 회사 이름으로 워크스페이스를 만듭니다.",
  },
];

const MIN_EMAIL_ROWS = 3;
const EMAIL_SPLIT_RE = /[,\s]+/;

export default function WorkspaceCreateForm({ onCreated }) {
  const [step, setStep] = useState("type"); // "type" | "details" | "invite"
  const [type, setType] = useState(null);
  const [name, setName] = useState("");
  const [workspace, setWorkspace] = useState(null);
  const [emails, setEmails] = useState(Array(MIN_EMAIL_ROWS).fill(""));
  const [linkCopied, setLinkCopied] = useState(false);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const chooseType = (value) => {
    setType(value);
    setStep("details");
  };

  const submitDetails = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const ws = await createWorkspace(name.trim(), type);
      setWorkspace(ws);
      setStep("invite");
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const updateEmail = (index, value) => {
    setEmails((rows) => {
      // 쉼표/공백/줄바꿈으로 여러 이메일을 한 번에 붙여넣으면(일괄 초대) 자동으로
      // 여러 칸에 나눠 담는다 — 한 명씩 따로 입력할 필요가 없게.
      const pasted = value.split(EMAIL_SPLIT_RE).map((v) => v.trim()).filter(Boolean);
      if (pasted.length <= 1) {
        return rows.map((r, i) => (i === index ? value : r));
      }
      const next = [...rows];
      let cursor = index;
      for (const email of pasted) {
        if (cursor >= next.length) next.push("");
        next[cursor] = email;
        cursor += 1;
      }
      return next;
    });
  };

  const addEmailRow = () => setEmails((rows) => [...rows, ""]);

  const copyInviteLink = async () => {
    setError(null);
    try {
      const invite = await createWorkspaceInvite(workspace.id, "member", null);
      await navigator.clipboard?.writeText(invite.invite_url);
      setLinkCopied(true);
    } catch (err) {
      setError(err.message);
    }
  };

  const finish = async () => {
    setSubmitting(true);
    setError(null);
    const targets = [...new Set(emails.map((e) => e.trim()).filter(Boolean))];
    // 초대 발송 실패는 개별적으로 무시한다 — 워크스페이스는 이미 만들어졌고,
    // 워크스페이스 관리 화면에서 언제든 다시 초대할 수 있다.
    await Promise.all(targets.map((email) => createWorkspaceInvite(workspace.id, "member", email).catch(() => {})));
    setSubmitting(false);
    onCreated(workspace);
  };

  if (step === "type") {
    return (
      <div className="type-choice-grid">
        {TYPES.map(({ value, label, tagline, description, Icon }) => (
          <button type="button" className="type-choice-card" key={value} onClick={() => chooseType(value)}>
            <span className="type-choice-icon">
              <Icon size={32} />
            </span>
            <span className="type-choice-body">
              <span className="type-choice-label">{label}</span>
              <span className="type-choice-tagline">{tagline}</span>
              <span className="type-choice-desc">{description}</span>
            </span>
          </button>
        ))}
      </div>
    );
  }

  if (step === "details") {
    return (
      <form className="auth-form" onSubmit={submitDetails}>
        <button type="button" className="wizard-back" onClick={() => setStep("type")}>
          ← {TYPES.find((t) => t.value === type)?.label} 워크스페이스
        </button>
        <input
          autoFocus
          placeholder="워크스페이스 이름"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        {error && <div className="alert-error">{error}</div>}
        <button className="btn-primary auth-submit" type="submit" disabled={submitting}>
          {submitting ? "만드는 중..." : "다음"}
        </button>
      </form>
    );
  }

  return (
    <div className="invite-teammates-step">
      <h3>다른 팀원이 또 있나요?</h3>
      <p className="muted">이메일로 팀원 추가</p>

      <p className="invite-teammates-label">팀 초대</p>
      <div className="auth-form">
        {emails.map((value, i) => (
          <input
            key={i}
            type="email"
            placeholder="name@company.com"
            value={value}
            onChange={(e) => updateEmail(i, e.target.value)}
          />
        ))}
      </div>
      <button type="button" className="team-switcher-new-btn invite-teammates-add" onClick={addEmailRow}>
        + 이메일 추가
      </button>

      <button type="button" className="invite-teammates-link" onClick={copyInviteLink}>
        🔗 {linkCopied ? "링크가 복사되었습니다" : "초대 링크 복사"}
      </button>

      {error && <div className="alert-error">{error}</div>}
      <button className="btn-primary auth-submit" type="button" onClick={finish} disabled={submitting}>
        {submitting ? "처리 중..." : "계속하기 →"}
      </button>
    </div>
  );
}
