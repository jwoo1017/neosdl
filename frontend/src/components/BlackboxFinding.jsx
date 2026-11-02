import { useState } from "react";

// 블랙박스 발견 한 건 — 채팅(LivePentestPanel)과 Engagement 상세가 같이 쓴다.
// 백엔드 판정(app/finding_verdict.py)이 실은 것: status(confirmed/suspected), code_trace(route → handler →
// … → sink, 단계별 snippet), sink(호출·쿼리 구성·섞인 요청 값), patch(원문 대조 검증된 before/after).
// 위치는 실제 sink 줄이다 — 예전처럼 핸들러 선언 줄을 "취약 줄"로 보여주지 않는다.
const SEV_BADGE = { critical: "badge-risk-critical", high: "badge-risk-high",
  medium: "badge-risk-medium", low: "badge-risk-low" };
const OK = "#3fb8ac", WARN = "#d29922", BAD = "#e5534b";
const KIND_LABEL = { route: "라우트", handler: "핸들러", function: "호출", sink: "위험 호출" };

const base = (p) => String(p || "").split("/").pop();

function Chevron({ open }) {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2"
         style={{ flex: "none", transition: "transform .15s", transform: open ? "rotate(90deg)" : "none" }}>
      <path d="M6 3l5 5-5 5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function StatusChip({ status }) {
  if (!status) return null;
  const suspected = status === "suspected";
  return (
    <span title={suspected ? "코드 분석상 위험 — 자동 공격으로는 아직 확인되지 않음" : "도구가 실제 요청으로 확인"}
          style={{ flex: "none", fontSize: "0.64rem", padding: "0.05rem 0.4rem", borderRadius: 999,
                   border: `1px solid ${suspected ? WARN : OK}`, color: suspected ? WARN : OK }}>
      {suspected ? "코드상 의심" : "확인됨"}
    </span>
  );
}

// 코드 ±N줄 — 줄번호 + 앵커 줄 강조.
export function CodeSnippet({ snip }) {
  return (
    <div style={{ marginTop: "0.45rem", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
      <div style={{ padding: "0.25rem 0.6rem", fontSize: "0.72rem", fontFamily: "monospace",
                    color: "var(--text-dim)", background: "var(--surface-2)", borderBottom: "1px solid var(--border)" }}>
        {snip.path}:{snip.focus}
      </div>
      <pre style={{ margin: 0, padding: "0.35rem 0", fontSize: "0.72rem", background: "var(--surface)",
                    overflowX: "auto" }}>
        {snip.lines.map((text, i) => {
          const n = snip.start + i, hit = n === snip.focus;
          return (
            <div key={n} style={{ display: "flex", background: hit ? "rgba(210,153,34,.16)" : "none",
                                  borderLeft: `3px solid ${hit ? WARN : "transparent"}` }}>
              <span style={{ width: "3.2em", flex: "none", textAlign: "right", paddingRight: "0.8em",
                             color: "var(--text-faint)", userSelect: "none" }}>{n}</span>
              <span style={{ whiteSpace: "pre", paddingRight: "0.6rem" }}>{text}</span>
            </div>
          );
        })}
      </pre>
    </div>
  );
}

// 호출 경로 — 단계를 누르면 그 단계의 코드. 기본은 마지막(위험 호출) 단계.
function CodeTrace({ trace, fallback }) {
  const [sel, setSel] = useState(trace.length - 1);
  const hop = trace[sel] || {};
  const snip = hop.snippet || (sel === trace.length - 1 ? fallback : null);
  return (
    <div style={{ marginTop: "0.45rem" }}>
      <div style={{ fontSize: "0.72rem", color: "var(--text-dim)", marginBottom: "0.2rem" }}>호출 경로</div>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.25rem" }}>
        {trace.map((h, i) => (
          <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: "0.25rem" }}>
            {i > 0 && <span style={{ color: "var(--text-faint)" }}>→</span>}
            <button onClick={() => setSel(i)} title={`${h.file}:${h.line ?? "?"}`}
                    style={{ fontSize: "0.7rem", fontFamily: "monospace", cursor: "pointer", borderRadius: 6,
                             padding: "0.1rem 0.4rem", color: h.kind === "sink" ? BAD : "var(--text)",
                             background: i === sel ? "var(--surface-2)" : "none",
                             border: `1px solid ${i === sel ? "var(--border)" : "transparent"}` }}>
              <span style={{ color: "var(--text-faint)" }}>{KIND_LABEL[h.kind] || h.kind}</span>{" "}
              {h.kind === "route" ? h.name : `${h.name} · ${base(h.file)}:${h.line ?? "?"}`}
            </button>
          </span>
        ))}
      </div>
      {snip?.lines?.length > 0 && <CodeSnippet snip={snip} />}
    </div>
  );
}

function PatchDiff({ patch }) {
  const row = (text, sign, color, bg, key) => (
    <div key={key} style={{ display: "flex", background: bg }}>
      <span style={{ width: "1.6em", flex: "none", textAlign: "center", color, userSelect: "none" }}>{sign}</span>
      <span style={{ whiteSpace: "pre", paddingRight: "0.6rem" }}>{text}</span>
    </div>
  );
  return (
    <div style={{ marginTop: "0.45rem", border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
      <div style={{ padding: "0.25rem 0.6rem", fontSize: "0.72rem", color: "var(--text-dim)", display: "flex", gap: "0.5rem",
                    background: "var(--surface-2)", borderBottom: "1px solid var(--border)", flexWrap: "wrap" }}>
        <span style={{ fontFamily: "monospace" }}>{patch.file}:{patch.line_start}-{patch.line_end}</span>
        <span style={{ color: OK }}>수정 제안 · {patch.verified || "검증됨"}</span>
        {patch.note && <span>{patch.note}</span>}
      </div>
      <pre style={{ margin: 0, padding: "0.35rem 0", fontSize: "0.72rem", background: "var(--surface)", overflowX: "auto" }}>
        {patch.before.split("\n").map((t, i) => row(t, "-", BAD, "rgba(229,83,75,.10)", `b${i}`))}
        {patch.after.split("\n").map((t, i) => row(t, "+", OK, "rgba(63,184,172,.10)", `a${i}`))}
      </pre>
    </div>
  );
}

// 발견 한 줄 — 쉬운 이름만 보이고, 펼치면 무엇이 위험한지·호출 경로·고치는 법·수정 제안.
export default function BlackboxFinding({ f, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  const trace = (f.code_trace || []).filter((h) => h.file);
  const sink = f.sink;
  return (
    <div style={{ borderRadius: 8, border: open ? "1px solid var(--border)" : "1px solid transparent" }}>
      <button onClick={() => setOpen((v) => !v)}
              style={{ display: "flex", alignItems: "center", gap: "0.5rem", width: "100%", minWidth: 0,
                       padding: "0.3rem 0.5rem", background: "none", border: "none", cursor: "pointer",
                       color: "var(--text-dim)", textAlign: "left", fontSize: "0.82rem" }}>
        <Chevron open={open} />
        <span className={`badge ${SEV_BADGE[f.severity] || ""}`} style={{ fontSize: "0.66rem", flex: "none" }}>
          {f.severity || "?"}
        </span>
        <StatusChip status={f.status} />
        <span style={{ color: "var(--text)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
                       whiteSpace: "nowrap" }}>{f.label || f.title}</span>
        {f.file && (
          <code style={{ fontSize: "0.7rem", color: "var(--text-faint)", whiteSpace: "nowrap" }}>
            {base(f.file)}:{f.line ?? "?"}
          </code>
        )}
      </button>
      {open && (
        <div style={{ padding: "0 0.6rem 0.6rem 2.1rem", fontSize: "0.8rem", lineHeight: 1.55 }}>
          {f.plain && <div style={{ color: "var(--text)" }}>{f.plain}</div>}
          {sink && (
            <div style={{ marginTop: "0.3rem", fontSize: "0.74rem", color: "var(--text-dim)" }}>
              <code>{sink.callee}</code> · {sink.construction_ko}
              {sink.tainted?.length > 0 && <> · 섞인 요청 값 {sink.tainted.map((v) => <code key={v} style={{ marginLeft: 4 }}>{v}</code>)}</>}
            </div>
          )}
          {f.endpoints?.length > 1 && (
            <div style={{ marginTop: "0.2rem", fontSize: "0.74rem", color: "var(--text-dim)" }}>
              같은 줄에 닿는 엔드포인트: {f.endpoints.join(", ")}
            </div>
          )}
          {f.fix && (
            <div style={{ marginTop: "0.3rem", color: "var(--text-dim)" }}>
              <strong style={{ color: OK }}>고치는 법</strong> · {f.fix}
            </div>
          )}
          {f.patch && <PatchDiff patch={f.patch} />}
          {trace.length > 1
            ? <CodeTrace trace={trace} fallback={f.snippet} />
            : f.snippet?.lines?.length > 0 && <CodeSnippet snip={f.snippet} />}
          <div style={{ marginTop: "0.35rem", fontSize: "0.7rem", color: "var(--text-faint)", wordBreak: "break-word" }}>
            근거: {f.evidence || f.title}
          </div>
        </div>
      )}
    </div>
  );
}

// 판정 부가 정보 — 코드로 안전 확인된 엔드포인트, 근거 없어 제외한 AI 주장.
export function VerdictNotes({ report }) {
  const safe = report?.safe || [];
  const discarded = report?.discarded || [];
  if (!safe.length && !discarded.length) return null;
  return (
    <div style={{ marginTop: "0.45rem", fontSize: "0.74rem", color: "var(--text-faint)", lineHeight: 1.55 }}>
      {safe.length > 0 && (
        <div>코드로 안전 확인(SQL 전부 바인딩/고정 문자열): {safe.map((s) => s.endpoint).join(", ")}</div>
      )}
      {discarded.length > 0 && (
        <div title={discarded.map((d) => d.title).join("\n")}>
          근거 없어 제외한 AI 주장 {discarded.length}건 (도구 출력·코드에 근거 없음)
        </div>
      )}
    </div>
  );
}
