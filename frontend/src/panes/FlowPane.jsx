import { useEffect, useMemo, useRef, useState } from "react";
import CodeViewer from "../CodeViewer";

// 코드 흐름 재생 탭 — 채팅 답변 근거의 호출/오염 경로를 왼쪽→오른쪽 단계로 그리고,
// 빛나는 점이 hop 사이를 이동하며 순서대로 하이라이트한다. 현재 hop 의 코드는
// 아래 CodeViewer 에 같이 띄운다. hop 은 많아야 수십 개라 Sigma 없이 SVG 로 충분하다.
// flow: { kind: "call"|"taint", title, sink, sink_line, hops: [{symbol_key, label, qualified_name, path, line, var}] }

const BOX_W = 168;
const BOX_H = 58;
const GAP = 64;
const PAD = 24;
const TRAVEL_MS = 900; // 1x 속도에서 한 엣지를 지나는 시간
const DWELL_MS = 700; // 각 hop 에 머무는 시간

const prefersReducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

function truncate(s, n) {
  if (!s) return "";
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}

export default function FlowPane({ scanId, flow, onOpenInMap }) {
  const steps = useMemo(() => {
    const hops = (flow?.hops || []).map((h) => ({ ...h, sink: false }));
    if (flow?.kind === "taint" && flow.sink) {
      const last = hops[hops.length - 1];
      hops.push({ symbol_key: "__sink__", label: flow.sink, qualified_name: `위험 호출: ${flow.sink}`,
                  path: last?.path, line: flow.sink_line || last?.line, sink: true });
    }
    return hops;
  }, [flow]);

  const [step, setStep] = useState(0); // 현재 머무는 hop
  const [progress, setProgress] = useState(0); // step → step+1 이동 진행도(0~1), 머무는 동안 0
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);
  const scrollRef = useRef(null);

  // 재생 루프: hop 에 DWELL 만큼 머문 뒤 TRAVEL 동안 점이 다음 hop 으로 이동.
  useEffect(() => {
    if (!playing || steps.length < 2) return undefined;
    if (step >= steps.length - 1) {
      setPlaying(false);
      return undefined;
    }
    const reduced = prefersReducedMotion();
    let raf = 0;
    let start = null;
    const dwell = DWELL_MS / speed;
    const travel = reduced ? 0 : TRAVEL_MS / speed;
    const tick = (now) => {
      if (start === null) start = now;
      const elapsed = now - start;
      if (elapsed < dwell) {
        raf = requestAnimationFrame(tick);
        return;
      }
      const t = travel === 0 ? 1 : Math.min(1, (elapsed - dwell) / travel);
      if (t >= 1) {
        setProgress(0);
        setStep((s) => s + 1);
        return;
      }
      setProgress(t);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, step, speed, steps.length]);

  // 현재 hop 이 화면 밖이면 가로 스크롤을 따라간다.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const x = PAD + step * (BOX_W + GAP);
    if (x < el.scrollLeft || x + BOX_W > el.scrollLeft + el.clientWidth) {
      el.scrollTo({ left: Math.max(0, x - el.clientWidth / 2 + BOX_W / 2), behavior: "smooth" });
    }
  }, [step]);

  if (!flow || steps.length === 0) {
    return <div className="flow-pane"><div className="app-empty">재생할 흐름이 없습니다.</div></div>;
  }

  const width = PAD * 2 + steps.length * BOX_W + (steps.length - 1) * GAP;
  const height = PAD * 2 + BOX_H + 34;
  const boxX = (i) => PAD + i * (BOX_W + GAP);
  const midY = PAD + BOX_H / 2;
  const dotX = boxX(step) + BOX_W + (GAP) * progress;
  const moving = progress > 0;
  const current = steps[step];
  const atEnd = step >= steps.length - 1;

  const goto = (i) => {
    setPlaying(false);
    setProgress(0);
    setStep(Math.max(0, Math.min(steps.length - 1, i)));
  };

  // 경로 함수들만 코드 지도에 순서대로 올려 강조한다(위험 호출 가짜 단계는 제외).
  const openInMap = () => {
    const keys = steps.filter((h) => !h.sink).map((h) => h.symbol_key);
    // 심볼 키엔 줄바꿈이 없으므로 구분자로 쓴다(GraphViewerPage 가 같은 문자로 나눈다).
    const q = new URLSearchParams({ hl: "path", keys: keys.join(String.fromCharCode(10)), title: flow.title || "흐름 경로" });
    onOpenInMap(`graph/${scanId}?${q.toString()}`);
  };

  const togglePlay = () => {
    if (atEnd) {
      setStep(0);
      setProgress(0);
      setPlaying(true);
      return;
    }
    setPlaying((p) => !p);
  };

  return (
    <div className="flow-pane">
      <header className="flow-header">
        <div className="flow-title">
          <span className={`badge ${flow.kind === "taint" ? "badge-danger" : ""}`}>
            {flow.kind === "taint" ? "오염 경로" : "호출 흐름"}
          </span>
          <h2>{flow.title}</h2>
        </div>
        <div className="flow-controls" role="toolbar" aria-label="흐름 재생">
          <button type="button" onClick={() => goto(0)} aria-label="처음으로">⏮</button>
          <button type="button" onClick={() => goto(step - 1)} disabled={step === 0} aria-label="이전 단계">◀</button>
          <button type="button" className="btn-primary flow-play" onClick={togglePlay}>
            {playing ? "일시정지" : atEnd ? "다시 재생" : "재생"}
          </button>
          <button type="button" onClick={() => goto(step + 1)} disabled={atEnd} aria-label="다음 단계">▶</button>
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="재생 속도">
            <option value={0.5}>0.5x</option>
            <option value={1}>1x</option>
            <option value={2}>2x</option>
          </select>
          <span className="muted flow-step-count">{step + 1} / {steps.length}</span>
          {onOpenInMap && scanId && (
            <button type="button" onClick={openInMap}>코드 지도에서 보기</button>
          )}
        </div>
      </header>

      <div className="flow-stage" ref={scrollRef}>
        <svg width={width} height={height} className="flow-svg" role="img" aria-label={flow.title}>
          <defs>
            <marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,0 L10,5 L0,10 z" className="flow-arrow-head" />
            </marker>
            <filter id="flow-glow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="3" result="b" />
              <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
            </filter>
          </defs>

          {steps.slice(0, -1).map((h, i) => {
            const x1 = boxX(i) + BOX_W;
            const x2 = boxX(i + 1);
            const done = i < step;
            const active = i === step && moving;
            return (
              <g key={`e${i}`}>
                <line x1={x1} y1={midY} x2={x2 - 2} y2={midY} markerEnd="url(#flow-arrow)"
                      className={`flow-edge${done ? " is-done" : ""}${active ? " is-active" : ""}`} />
                {flow.kind === "taint" && h.var && (
                  <text x={(x1 + x2) / 2} y={midY - 9} textAnchor="middle" className="flow-edge-var">
                    {truncate(h.var, 12)}
                  </text>
                )}
              </g>
            );
          })}

          {steps.map((h, i) => {
            const state = i < step ? "is-done" : i === step ? "is-current" : "";
            return (
              <g key={`${h.symbol_key}-${i}`} className={`flow-node ${state}${h.sink ? " is-sink" : ""}`}
                 transform={`translate(${boxX(i)}, ${PAD})`} onClick={() => goto(i)}
                 role="button" tabIndex={0} aria-label={`${i + 1}단계 ${h.qualified_name || h.label}`}
                 onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && goto(i)}>
                <title>{h.qualified_name || h.label}{h.path ? `\n${h.path}${h.line ? `:${h.line}` : ""}` : ""}</title>
                <rect width={BOX_W} height={BOX_H} rx={9} className="flow-node-box" />
                <text x={12} y={23} className="flow-node-label">{truncate(h.label, 20)}</text>
                <text x={12} y={42} className="flow-node-path">
                  {h.sink ? "위험 함수 호출" : truncate(h.path ? `${h.path.split("/").pop()}${h.line ? `:${h.line}` : ""}` : "", 24)}
                </text>
                <text x={BOX_W / 2} y={BOX_H + 20} textAnchor="middle" className="flow-node-index">{i + 1}</text>
              </g>
            );
          })}

          {moving && <circle cx={dotX} cy={midY} r={6} className="flow-dot" filter="url(#flow-glow)" />}
        </svg>
      </div>

      <div className="flow-detail">
        <div className="flow-detail-head">
          <strong>{current.qualified_name || current.label}</strong>
          {flow.kind === "taint" && current.var && <span className="badge">오염 변수 {current.var}</span>}
        </div>
        {scanId && current.path ? (
          <CodeViewer scanId={scanId} path={current.path} line={current.line} onClose={() => {}} />
        ) : (
          <div className="muted flow-detail-empty">코드 위치 정보가 없습니다.</div>
        )}
      </div>
    </div>
  );
}
