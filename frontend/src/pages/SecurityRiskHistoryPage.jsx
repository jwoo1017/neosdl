import { useEffect, useState } from "react";
import { fetchRiskHistory } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

const SEVERITY_ORDER = ["critical", "high", "medium", "low"];
const SEVERITY_COLOR = { critical: "#d1242f", high: "#d1242f", medium: "#bf8700", low: "#1a7f37" };

const CHART_HEIGHT = 160;
const BAR_WIDTH = 28;
const BAR_GAP = 14;

function RiskHistoryChart({ history }) {
  const totals = history.map((h) => SEVERITY_ORDER.reduce((sum, s) => sum + (h.counts[s] || 0), 0));
  const maxTotal = Math.max(1, ...totals);
  const width = history.length * (BAR_WIDTH + BAR_GAP) + BAR_GAP;

  return (
    <div className="table-scroll">
      <svg width={width} height={CHART_HEIGHT + 30} className="risk-history-chart">
        {history.map((h, i) => {
          const x = BAR_GAP + i * (BAR_WIDTH + BAR_GAP);
          let yCursor = CHART_HEIGHT;
          const segments = SEVERITY_ORDER.map((sev) => {
            const n = h.counts[sev] || 0;
            const segHeight = (n / maxTotal) * CHART_HEIGHT;
            const y = yCursor - segHeight;
            yCursor = y;
            return n > 0 ? (
              <rect key={sev} x={x} y={y} width={BAR_WIDTH} height={segHeight} fill={SEVERITY_COLOR[sev]}>
                <title>{`${sev}: ${n}`}</title>
              </rect>
            ) : null;
          });
          return (
            <g key={h.scan_id}>
              {segments}
              <text x={x + BAR_WIDTH / 2} y={CHART_HEIGHT + 16} textAnchor="middle" className="risk-history-label">
                {h.commit_sha.slice(0, 6)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export default function SecurityRiskHistoryPage() {
  const { activeProject } = useProject();
  const [repositoryId, setRepositoryId] = useState(null);
  const [history, setHistory] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!repositoryId) return;
    setHistory(null);
    setError(null);
    fetchRiskHistory(repositoryId).then(setHistory).catch((err) => setError(err.message));
  }, [repositoryId]);

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>Risk History</h1>
      <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
      {error && <div className="alert-error">{error}</div>}
      {history === null ? (
        <p className="muted">불러오는 중...</p>
      ) : history.length === 0 ? (
        <div className="dashboard-empty">
          아직 보안 분석이 끝난 스캔이 없습니다 — Findings 탭에서 먼저 조회해 보세요.
        </div>
      ) : (
        <RiskHistoryChart history={history} />
      )}
    </div>
  );
}
