import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchRiskIdentities } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

const SEV_BADGE = { critical: "badge-risk-critical", high: "badge-risk-high",
  medium: "badge-risk-medium", low: "badge-risk-low" };
const STATUS_LABEL = { active: "활성", fixed: "수정됨", resolved: "해결됨" };
const STATUSES = ["active", "fixed", "resolved"];

// Risk Identity 목록 — 지문 하나 = 여러 커밋/브랜치에서 관측된 하나의 위험.
export default function RiskListPage() {
  const { activeProject } = useProject();
  const navigate = useNavigate();
  const [repositoryId, setRepositoryId] = useState(null);
  const [status, setStatus] = useState("active");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!repositoryId) return;
    setData(null); setError(null);
    fetchRiskIdentities(repositoryId, { status }).then(setData).catch((e) => setError(e.message));
  }, [repositoryId, status]);

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>Risks</h1>
      <p className="muted">스캔마다 새로 생기는 게 아니라, 같은 취약점 하나를 커밋/브랜치를 거쳐 추적합니다.</p>
      <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
      {error && <div className="alert-error">{error}</div>}

      {data && (
        <div className="detail-tab-bar-secondary" style={{ marginBottom: "0.6rem" }}>
          {STATUSES.map((s) => (
            <button key={s} className={s === status ? "active" : ""} onClick={() => setStatus(s)}
                    style={{ background: "none", border: "none", cursor: "pointer",
                             color: s === status ? "var(--accent-strong)" : "var(--text-dim)",
                             padding: "0.3rem 0.6rem", font: "inherit" }}>
              {STATUS_LABEL[s]} {data.summary[s] || 0}
            </button>
          ))}
        </div>
      )}

      {repositoryId && data === null && <p className="muted">불러오는 중...</p>}
      {data && data.risks.length === 0 && (
        <div className="dashboard-empty">{STATUS_LABEL[status]} Risk Identity가 없습니다.</div>
      )}

      {data && data.risks.length > 0 && (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>심각도</th><th>유형</th><th>처음 발생</th><th>관측</th><th>브랜치</th>
              </tr>
            </thead>
            <tbody>
              {data.risks.map((r) => (
                <tr key={r.fingerprint} className="risk-row"
                    onClick={() => navigate(
                      `/projects/${activeProject.id}/detail/security/risks/${r.fingerprint}?repo=${repositoryId}`)}>
                  <td><span className={`badge ${SEV_BADGE[r.worst_severity] || ""}`}>{r.worst_severity}</span></td>
                  <td>
                    {r.category}
                    <div className="muted" style={{ fontSize: "0.72rem" }}>
                      {r.sample?.sink_symbol_key?.split("#").pop() || r.fingerprint}
                    </div>
                  </td>
                  <td className="muted">
                    {r.first_seen_commit?.slice(0, 8)} @ {r.first_seen_branch || "?"}
                    {r.first_seen_at && (
                      <div style={{ fontSize: "0.72rem" }}>{new Date(r.first_seen_at).toLocaleDateString()}</div>
                    )}
                  </td>
                  <td className="muted">{r.n_observations}회</td>
                  <td className="muted">{r.n_branches}개</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
