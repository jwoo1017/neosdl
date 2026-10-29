// 채팅 답변 아래 "코드 흐름" 미니 카드 — 근거에 호출/오염 경로가 있을 때만 뜬다.
// "흐름 탭에서 재생" 을 누르면 새 탭(panes/FlowPane.jsx)에서 애니메이션으로 따라간다.
const MAX_INLINE_HOPS = 5;

function FlowCard({ flow, onPlay }) {
  const hops = flow.hops || [];
  const shown = hops.length > MAX_INLINE_HOPS
    ? [...hops.slice(0, 2), null, ...hops.slice(-2)] // 가운데는 "…" 로 접는다
    : hops;
  return (
    <div className="chat-flow-card">
      <div className="chat-flow-card-head">
        <span className="chat-flow-card-title">
          <span className="badge">{flow.kind === "taint" ? "오염 경로" : "호출 흐름"}</span>
          <span>{hops.length}단계</span>
        </span>
        {onPlay && (
          <button type="button" className="btn-primary" onClick={() => onPlay(flow)}>
            흐름 탭에서 재생
          </button>
        )}
      </div>
      <div className="chat-flow-path">
        {shown.map((h, i) => (
          <span key={i} style={{ display: "contents" }}>
            {i > 0 && <span className="chat-flow-arrow">→</span>}
            {h ? (
              <span className="chat-flow-hop" title={h.qualified_name || h.label}>{h.label}</span>
            ) : (
              <span className="chat-flow-arrow">…</span>
            )}
          </span>
        ))}
        {flow.kind === "taint" && flow.sink && (
          <>
            <span className="chat-flow-arrow">→</span>
            <span className="chat-flow-hop is-sink">{flow.sink}</span>
          </>
        )}
      </div>
    </div>
  );
}

export default function FlowCards({ flows, onPlay }) {
  if (!flows || flows.length === 0) return null;
  return (
    <div className="chat-flow-cards">
      {flows.map((f, i) => (
        <FlowCard key={i} flow={f} onPlay={onPlay} />
      ))}
    </div>
  );
}
