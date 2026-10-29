import { useState } from "react";
import { toolIcon, describeCommand, IconThought } from "./EngageIcons";

// engagement 실행 스텝 타임라인 — 마크다운 덤프 대신 도구 아이콘·상태·접이식 출력 카드.
// EngageTab(B)와 채팅 라이브(A) 공용. step: {command, ok, exit_code, timed_out, output, rejected, reason?}

function statusOf(step) {
  if (step.rejected) return { label: `거부(${step.rejected})`, cls: "badge-risk-medium" };
  if (step.timed_out) return { label: "타임아웃", cls: "badge-risk-high" };
  const ex = step.exit_code != null ? ` · exit ${step.exit_code}` : "";
  if (step.ok) return { label: `성공${ex}`, cls: "badge-risk-low" };
  if (step.ok === false || step.exit_code != null) return { label: `실패${ex}`, cls: "badge-risk-critical" };
  return { label: "실행 중", cls: "badge-risk-medium" };
}

const PREVIEW_LINES = 8;

export function StepCard({ step, index }) {
  const [open, setOpen] = useState(false);
  const Icon = toolIcon(step.command);
  const st = statusOf(step);
  const out = (step.output || "").replace(/\s+$/, "");
  const lines = out ? out.split("\n") : [];
  const long = lines.length > PREVIEW_LINES;
  const shown = open ? out : lines.slice(0, PREVIEW_LINES).join("\n");

  return (
    <div style={{ display: "flex", gap: "0.6rem", alignItems: "flex-start", padding: "0.5rem 0" }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", alignSelf: "stretch" }}>
        <span style={{ width: 28, height: 28, borderRadius: "50%", display: "grid", placeItems: "center",
                       border: "1px solid var(--border, #3a3a3a)", color: "var(--text, inherit)", flex: "none" }}>
          <Icon size={16} />
        </span>
        <span style={{ flex: 1, width: 1, background: "var(--border, #3a3a3a)", marginTop: 4 }} />
      </div>
      <div style={{ flex: 1, minWidth: 0, paddingBottom: "0.4rem" }}>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
          {index != null && <span className="muted" style={{ fontSize: "0.72rem" }}>#{index + 1}</span>}
          <strong style={{ fontSize: "0.8rem" }}>{describeCommand(step.command)}</strong>
          <span className={`badge ${st.cls}`} style={{ fontSize: "0.68rem" }}>{st.label}</span>
        </div>
        <code style={{ fontSize: "0.76rem", wordBreak: "break-all", color: "var(--text-dim)",
                       display: "block", marginTop: "0.15rem" }}>{step.command}</code>
        {step.reason && (
          <div className="muted" style={{ fontSize: "0.76rem", marginTop: "0.25rem", fontStyle: "italic",
                                           display: "flex", alignItems: "flex-start", gap: "0.3rem" }}>
            <IconThought size={14} /> <span>{step.reason}</span>
          </div>
        )}
        {out && (
          <pre style={{ margin: "0.4rem 0 0", padding: "0.5rem 0.6rem", fontSize: "0.72rem",
                        background: "var(--code-bg, rgba(127,127,127,0.1))", borderRadius: 6,
                        overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word",
                        maxHeight: open ? "none" : 180, overflowY: "auto" }}>
            {shown}
          </pre>
        )}
        {long && (
          <button onClick={() => setOpen((v) => !v)} className="link-button"
                  style={{ fontSize: "0.72rem", marginTop: "0.2rem", background: "none", border: "none",
                           color: "var(--accent, #6ea8fe)", cursor: "pointer", padding: 0 }}>
            {open ? "접기" : `더 보기 (+${lines.length - PREVIEW_LINES}줄)`}
          </button>
        )}
      </div>
    </div>
  );
}

export default function EngageTimeline({ steps }) {
  const list = steps || [];
  if (list.length === 0) return null;
  return (
    <div>
      {list.map((s, i) => <StepCard key={i} step={s} index={i} />)}
    </div>
  );
}
