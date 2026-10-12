import { useState } from "react";
import {
  DIMENSION_LABELS, REPRESENTATIONS, REPRESENTATION_LABELS, exposurePillClass, scoreBucket, topDimension,
} from "../riskDisplay";

// 채팅 답변 아래에 붙는 Exposure 배너 — 이 답에 쓰인 코드를 외부 AI에 어느
// 수준까지 공개 가능한지(§11, §16). risk = evaluate_query 결과.
export default function EvidencePanel({ risk, onOpenDetail }) {
  const results = (risk?.results || []).filter((r) => r.risk_object);
  const [open, setOpen] = useState({});
  if (results.length === 0) return null;

  // 가장 제한적인 representation
  const worst = results
    .map((r) => r.exposure_boundary.allowed_representation)
    .reduce((a, b) => (REPRESENTATIONS.indexOf(b) > REPRESENTATIONS.indexOf(a) ? b : a), "RAW");
  const anyRawDenied = results.some((r) => !r.exposure_boundary.external_raw_allowed);
  const intent = risk?.query_intent?.intent;

  return (
    <div className="chat-evidence-panel">
      <div className="chat-evidence-banner">
        <span className={`pill ${exposurePillClass(worst)}`}>
          {anyRawDenied ? "🔒" : "🟢"} 외부 공개 한계: {REPRESENTATION_LABELS[worst] || worst}
        </span>
        {intent && intent !== "neutral" && (
          <span className="muted"> · 질문 의도: {intent}</span>
        )}
      </div>

      <ul className="chat-evidence-list">
        {results.map((r, i) => {
          const o = r.risk_object;
          const top = topDimension(o.risk);
          const b = scoreBucket(top.score);
          const rep = r.exposure_boundary.allowed_representation;
          const baseRep = r.base_exposure?.allowed_representation;
          const bumped = baseRep && baseRep !== rep;
          return (
            <li key={i} className="chat-evidence-row">
              <button className="chat-evidence-row-head" onClick={() => setOpen((s) => ({ ...s, [i]: !s[i] }))}>
                <span className="chat-evidence-caret">{open[i] ? "▾" : "▸"}</span>
                <span className="chat-evidence-symbol">{o.target.symbol}</span>
                {top.dim && <span className={`badge ${b.cls}`}>{DIMENSION_LABELS[top.dim]}</span>}
                <span className={`pill ${exposurePillClass(rep)}`}>{REPRESENTATION_LABELS[rep] || rep}</span>
                {bumped && <span className="muted"> (질문 때문에 {REPRESENTATION_LABELS[baseRep]}→{REPRESENTATION_LABELS[rep]})</span>}
              </button>
              {open[i] && (
                <div className="chat-evidence-detail">
                  <div className="muted">{o.target.file}</div>
                  <ul className="risk-reason-list">
                    {(r.exposure_boundary.reasons || []).map((x, j) => (
                      <li key={j} className="muted">{x}</li>
                    ))}
                  </ul>
                  {(o.rule_outcomes || []).length > 0 && (
                    <ul className="risk-rule-list">
                      {o.rule_outcomes.map((ro, j) => (
                        <li key={j}><span className="badge">{ro.effect}</span> {ro.reason}</li>
                      ))}
                    </ul>
                  )}
                  {onOpenDetail && (
                    <button className="chat-evidence-link" onClick={onOpenDetail}>
                      상세보기 Risk 탭에서 보기 →
                    </button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
