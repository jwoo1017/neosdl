import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { explainRiskIdentity, fetchRiskIdentity } from "../api";
import { useProject } from "../ProjectContext";

const SEV_BADGE = { critical: "badge-risk-critical", high: "badge-risk-high",
  medium: "badge-risk-medium", low: "badge-risk-low" };
const STATUS_LABEL = { active: "활성", fixed: "수정됨", resolved: "해결됨" };
const EVENT_LABEL = {
  detected: "감지됨", introduced: "발생", propagated: "전파", merged: "병합",
  fixed: "수정됨", resolved: "해결됨",
};

function symbolName(sk) {
  return sk && sk.includes("#") ? sk.split("#").pop() : sk;
}

export default function RiskLifecyclePage() {
  const { fingerprint } = useParams();
  const [search] = useSearchParams();
  const { activeProject } = useProject();
  const repositoryId = search.get("repo");

  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [explain, setExplain] = useState(null);
  const [explaining, setExplaining] = useState(false);

  useEffect(() => {
    if (!repositoryId || !fingerprint) return;
    setData(null); setError(null); setExplain(null);
    fetchRiskIdentity(repositoryId, fingerprint).then(setData).catch((e) => setError(e.message));
  }, [repositoryId, fingerprint]);

  const runExplain = () => {
    setExplaining(true);
    explainRiskIdentity(repositoryId, fingerprint)
      .then((r) => setExplain(r.explanation))
      .catch((e) => setError(e.message))
      .finally(() => setExplaining(false));
  };

  if (error) return <div className="dashboard"><div className="alert-error">{error}</div></div>;
  if (!repositoryId) return <div className="dashboard"><p className="muted">레포 정보가 없습니다. Risks 목록에서 다시 진입하세요.</p></div>;
  if (!data) return <div className="dashboard"><p className="muted">불러오는 중...</p></div>;

  const { identity: id, lifecycle, observations } = data;
  const df = (id.sample?.path || []).map((h) => symbolName(h.symbol_key)).filter(Boolean);

  return (
    <div className="dashboard">
      <Link to={`/projects/${activeProject?.id}/detail/security/risks`} className="muted">← Risks 목록</Link>
      <h1>
        {id.category}{" "}
        <span className={`badge ${SEV_BADGE[id.worst_severity] || ""}`}>{id.worst_severity}</span>{" "}
        <span className="badge">{STATUS_LABEL[id.status] || id.status}</span>
      </h1>
      <p className="muted">지문 {id.fingerprint} · {symbolName(id.sample?.sink_symbol_key)}</p>

      <section className="dashboard-section">
        <h2>생애주기</h2>
        <ol className="lifecycle-list">
          {lifecycle.map((e, i) => (
            <li key={i} className={`lifecycle-item lifecycle-${e.kind}`}>
              <span className="lifecycle-dot" />
              <div>
                <div className="lifecycle-kind">
                  {EVENT_LABEL[e.kind] || e.kind}
                  {e.pr_number && <span className="muted"> · PR #{e.pr_number}</span>}
                  {e.branch && <span className="muted"> · {e.branch}</span>}
                  {e.detail?.from && <span className="muted"> ({e.detail.from} → {e.detail.to})</span>}
                </div>
                <div className="muted lifecycle-at">{new Date(e.at).toLocaleString()}</div>
              </div>
            </li>
          ))}
        </ol>
      </section>

      <section className="dashboard-section">
        <h2>왜 이렇게 됐나?</h2>
        {explain ? (
          <p style={{ whiteSpace: "pre-wrap" }}>{explain}</p>
        ) : (
          <button className="btn-primary" onClick={runExplain} disabled={explaining}>
            {explaining ? "분석 중..." : "생애주기 설명 생성 (LLM)"}
          </button>
        )}
      </section>

      {df.length > 0 && (
        <section className="dashboard-section">
          <h2>데이터 흐름</h2>
          <div className="muted" style={{ fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>
            {df.join("  ▸  ")}
          </div>
        </section>
      )}

      <section className="dashboard-section">
        <h2>관측된 커밋 ({observations.length})</h2>
        <div className="table-scroll">
          <table className="repo-table">
            <thead><tr><th>커밋</th><th>브랜치</th><th>심각도</th><th>시각</th></tr></thead>
            <tbody>
              {observations.map((o, i) => (
                <tr key={i}>
                  <td className="muted">{o.commit_sha?.slice(0, 8)}</td>
                  <td>{o.branch || "?"}</td>
                  <td><span className={`badge ${SEV_BADGE[o.severity] || ""}`}>{o.severity}</span></td>
                  <td className="muted">{new Date(o.committed_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
