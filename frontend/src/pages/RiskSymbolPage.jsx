import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { evaluateRisk, fetchRiskSymbol } from "../api";
import { useProject } from "../ProjectContext";
import EvidenceTree from "../chat/EvidenceTree";
import {
  DIMENSION_LABELS, REPRESENTATION_LABELS, exposurePillClass, scoreBucket,
} from "../riskDisplay";

// 심볼 하나의 Risk Object 드릴다운 — 5차원 벡터 + 규칙 + Evidence 트리 + Exposure.
// 하단에서 질문을 넣으면 Contextual Risk(§11)로 조정된 Exposure를 나란히 보여준다.
export default function RiskSymbolPage() {
  const { symbolKey } = useParams();
  const [search] = useSearchParams();
  const { activeProject } = useProject();
  const scanId = search.get("scan");
  const decodedKey = decodeURIComponent(symbolKey || "");

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [contextual, setContextual] = useState(null);
  const [evaluating, setEvaluating] = useState(false);

  useEffect(() => {
    if (!scanId || !decodedKey) return;
    setData(null);
    setError(null);
    fetchRiskSymbol(scanId, decodedKey).then(setData).catch((err) => setError(err.message));
  }, [scanId, decodedKey]);

  const runQuery = (e) => {
    e.preventDefault();
    if (!query.trim()) return;
    setEvaluating(true);
    setContextual(null);
    evaluateRisk(scanId, query, [decodedKey])
      .then((res) => setContextual(res))
      .catch((err) => setError(err.message))
      .finally(() => setEvaluating(false));
  };

  if (error) return <div className="dashboard"><div className="alert-error">{error}</div></div>;
  if (!scanId) return <div className="dashboard"><p className="muted">스캔 정보가 없습니다. Risk 목록에서 다시 진입하세요.</p></div>;
  if (!data) return <div className="dashboard"><p className="muted">불러오는 중...</p></div>;

  const { target, risk, classification, exposure, rule_outcomes: rules, provenance } = data;

  return (
    <div className="dashboard">
      <Link to={`/projects/${activeProject?.id}/detail/security/risk`} className="muted">← Risk 목록</Link>
      <h1>{target.symbol}</h1>
      <p className="muted">{target.file} · L{target.lines?.[0]}–{target.lines?.[1]}</p>

      <section className="dashboard-section">
        <h2>Risk Vector</h2>
        <div className="risk-dim-bars">
          {["business_ip", "source_sensitivity", "security", "pii", "secret"].map((dim) => {
            const entry = risk[dim] || { score: 0, confidence: 0 };
            const b = scoreBucket(entry.score);
            return (
              <div key={dim} className="risk-dim-bar">
                <div className="risk-dim-bar-label">
                  {DIMENSION_LABELS[dim]}
                  <span className={`badge ${b.cls}`}>{b.label}</span>
                </div>
                <div className="risk-dim-bar-track">
                  <div className="risk-dim-bar-fill" style={{ width: `${Math.round(entry.score * 100)}%` }} />
                </div>
                <div className="muted risk-dim-bar-meta">
                  score {entry.score.toFixed(2)} · 신뢰도 {Math.round(entry.confidence * 100)}%
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="dashboard-section">
        <h2>Exposure</h2>
        <ExposureCard exposure={exposure} />
      </section>

      {contextual?.results?.[0] && (
        <section className="dashboard-section">
          <h2>질문 맥락 반영 (intent: {contextual.query_intent?.intent})</h2>
          <div className="risk-exposure-compare">
            <div>
              <div className="muted">기본</div>
              <ExposureCard exposure={contextual.results[0].base_exposure} />
            </div>
            <div>
              <div className="muted">이 질문에서</div>
              <ExposureCard exposure={contextual.results[0].exposure_boundary} />
            </div>
          </div>
        </section>
      )}

      <section className="dashboard-section">
        <h2>이 질문이라면?</h2>
        <form className="inline-form" onSubmit={runQuery}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="예: 우리 회사 할인 정책이 어떻게 계산되는지 설명해줘"
            style={{ minWidth: "22rem" }}
          />
          <button className="btn-primary" disabled={evaluating}>
            {evaluating ? "평가 중..." : "평가"}
          </button>
        </form>
      </section>

      {(classification && Object.keys(classification).length > 0) && (
        <section className="dashboard-section">
          <h2>분류</h2>
          <dl className="risk-kv">
            {Object.entries(classification).map(([k, v]) => (
              <div key={k}>
                <dt className="muted">{k}</dt>
                <dd>{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {rules?.length > 0 && (
        <section className="dashboard-section">
          <h2>적용된 규칙 (deterministic)</h2>
          <ul className="risk-rule-list">
            {rules.map((r, i) => (
              <li key={i}>
                <span className="badge">{r.effect}</span> {r.reason}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="dashboard-section">
        <h2>근거 (Evidence)</h2>
        <EvidenceTree detail={data.evidence_detail} scanId={scanId} />
      </section>

      <p className="muted" style={{ fontSize: "0.72rem" }}>
        판정 근거: {provenance?.decision_source} · 정책 v{provenance?.policy_version}
        {provenance?.reused_persisted ? " · 캐시 재사용" : ""}
      </p>
    </div>
  );
}

function ExposureCard({ exposure }) {
  const rep = exposure.allowed_representation;
  return (
    <div className="risk-exposure-card">
      <div>
        <span className={`pill ${exposurePillClass(rep)}`}>
          {REPRESENTATION_LABELS[rep] || rep}
        </span>
        <span className="muted" style={{ marginLeft: "0.6rem" }}>
          {exposure.external_raw_allowed ? "🟢 외부 원문 허용" : "🔒 외부 원문 차단"}
          {exposure.specialized_model_required && " · 보안 특화모델 필요"}
        </span>
      </div>
      <ul className="risk-reason-list">
        {(exposure.reasons || []).map((r, i) => <li key={i} className="muted">{r}</li>)}
      </ul>
    </div>
  );
}
