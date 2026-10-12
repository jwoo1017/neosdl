import { useState } from "react";
import { createTeamspace } from "../api";
import Modal from "./Modal";

const VISIBILITY_OPTIONS = [
  {
    value: "open",
    label: "공개",
    hint: "워크스페이스 멤버 누구나 팀스페이스를 보고, 스스로 가입할 수 있습니다.",
  },
  {
    value: "closed",
    label: "참가 제한",
    hint: "워크스페이스 멤버 누구나 볼 수 있지만, 가입하려면 초대가 필요합니다.",
  },
  {
    value: "private",
    label: "비공개",
    hint: "초대받은 멤버에게만 팀스페이스가 표시됩니다.",
  },
];

export default function CreateTeamspaceModal({ workspaceId, onCreated, onClose }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [visibility, setVisibility] = useState("open");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const teamspace = await createTeamspace(workspaceId, name.trim(), {
        description: description.trim() || undefined,
        visibility,
      });
      onCreated(teamspace);
      onClose();
    } catch (err) {
      setError(err.message);
      setSubmitting(false);
    }
  };

  return (
    <Modal title="새 팀스페이스" onClose={onClose}>
      <form className="auth-form" onSubmit={submit}>
        <input
          autoFocus
          placeholder="팀스페이스 이름"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <textarea
          placeholder="설명 (선택)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
        />
        <div className="role-permission-list">
          {VISIBILITY_OPTIONS.map((opt) => (
            <label key={opt.value} className="visibility-option">
              <input
                type="radio"
                name="visibility"
                value={opt.value}
                checked={visibility === opt.value}
                onChange={() => setVisibility(opt.value)}
              />
              <span>
                <strong>{opt.label}</strong>
                <span className="muted"> — {opt.hint}</span>
              </span>
            </label>
          ))}
        </div>
        {error && <div className="alert-error">{error}</div>}
        <button className="btn-primary auth-submit" type="submit" disabled={submitting}>
          {submitting ? "만드는 중..." : "팀스페이스 만들기"}
        </button>
      </form>
    </Modal>
  );
}
