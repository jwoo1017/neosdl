import { useEffect, useMemo, useState } from "react";
import { fetchSecurityDiff, fetchSecurityTimeline } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

// 브랜치별 색 — 기본 브랜치는 accent, 나머지는 순환 팔레트
const BRANCH_PALETTE = ["#3fb8ac", "#f0b429", "#f0616a", "#6fd9cd", "#a78bfa", "#60a5fa"];
const SEVS = ["critical", "high", "medium", "low"];

const H = 200;
const PAD = { l: 44, r: 16, t: 16, b: 28 };

function TimelineChart({ series, branchColors, onPick }) {
  const w = Math.max(560, series.length * 46 + PAD.l + PAD.r);
  const maxScore = Math.max(10, ...series.map((s) => Number(s.score) || 0));
  const x = (i) => PAD.l + (series.length <= 1 ? 0 : i * ((w - PAD.l - PAD.r) / (series.length - 1)));
  const y = (v) => PAD.t + (1 - (Number(v) || 0) / maxScore) * (H - PAD.t - PAD.b);

  const byBranch = {};
  series.forEach((s, i) => {
    (byBranch[s.branch || "?"] ||= []).push({ ...s, i });
  });

  return (
    <div className="table-scroll">
      <svg width={w} height={H + 14} className="risk-history-chart">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={PAD.l} x2={w - PAD.r} y1={y(maxScore * f)} y2={y(maxScore * f)}
                  stroke="var(--border)" strokeDasharray="2 3" />
            <text x={PAD.l - 6} y={y(maxScore * f) + 3} textAnchor="end" className="risk-history-label">
              {Math.round(maxScore * f)}
            </text>
          </g>
        ))}
        {Object.entries(byBranch).map(([b, pts]) => (
          <g key={b}>
            <polyline
              fill="none" stroke={branchColors[b] || "#93a4b3"} strokeWidth="1.5"
              points={pts.map((p) => `${x(p.i)},${y(p.score)}`).join(" ")}
            />
            {pts.map((p) => (
              <circle key={p.scan_id} cx={x(p.i)} cy={y(p.score)} r="4"
                      fill={branchColors[b] || "#93a4b3"} className="risk-timeline-dot"
                      onClick={() => onPick(p)}>
                <title>
                  {`${p.commit_sha.slice(0, 8)} @ ${b}\nscore ${p.score}\n`
                    + SEVS.map((s) => `${s}: ${p[`n_${s}`]}`).join("  ")}
                </title>
              </circle>
            ))}
          </g>
        ))}
      </svg>
    </div>
  );
}

function DiffPanel({ repositoryId, head, onClose }) {
  const [diff, setDiff] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    setDiff(null); setError(null);
    fetchSecurityDiff(repositoryId, head.scan_id).then(setDiff).catch((e) => setError(e.message));
  }, [repositoryId, head.scan_id]);

  return (
    <div className="risk-exposure-card" style={{ marginTop: "1rem" }}>
      <div className="dashboard-section-header">
        <h2 style={{ margin: 0 }}>Commit Diff — {head.commit_sha.slice(0, 8)} @ {head.branch}</h2>
        <button onClick={onClose}>닫기</button>
      </div>
      {error && <div className="alert-error">{error}</div>}
      {!diff && !error && <p className="muted">불러오는 중...</p>}
      {diff && (
        <>
          <div className="risk-summary">
            <span className="badge">score Δ {diff.score_delta > 0 ? "+" : ""}{diff.score_delta}</span>
            {SEVS.map((s) => diff.severity_delta[s] !== 0 && (
              <span key={s} className={`badge ${diff.severity_delta[s] > 0 ? "badge-risk-high" : "badge-risk-low"}`}>
                {s} {diff.severity_delta[s] > 0 ? "+" : ""}{diff.severity_delta[s]}
              </span>
            ))}
          </div>
          <DiffGroup title={`NEW (${diff.new.length})`} rows={diff.new} tone="badge-risk-high" />
          <DiffGroup title={`RESOLVED (${diff.resolved.length})`} rows={diff.resolved} tone="badge-risk-low" />
          {diff.affected_paths.length > 0 && (
            <div className="muted" style={{ fontSize: "0.78rem", marginTop: "0.5rem" }}>
              Affected: {diff.affected_paths.join(", ")}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function DiffGroup({ title, rows, tone }) {
  if (rows.length === 0) return null;
  return (
    <div style={{ marginTop: "0.5rem" }}>
      <div className="muted" style={{ fontSize: "0.75rem" }}>{title}</div>
      <ul className="risk-rule-list">
        {rows.map((f) => (
          <li key={f.fingerprint}>
            <span className={`badge ${tone}`}>{f.severity}</span>{" "}
            {f.category} · <span className="muted">{f.sink_symbol_key?.split("#").pop()}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function SecurityTimelinePage() {
  const { activeProject } = useProject();
  const [repositoryId, setRepositoryId] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [branchFilter, setBranchFilter] = useState(null);
  const [picked, setPicked] = useState(null);

  useEffect(() => {
    if (!repositoryId) return;
    setData(null); setError(null); setPicked(null); setBranchFilter(null);
    fetchSecurityTimeline(repositoryId).then(setData).catch((e) => setError(e.message));
  }, [repositoryId]);

  const branchColors = useMemo(() => {
    const m = {};
    (data?.branches || []).forEach((b, i) => {
      m[b.name] = b.is_default ? BRANCH_PALETTE[0] : BRANCH_PALETTE[(i % (BRANCH_PALETTE.length - 1)) + 1];
    });
    (data?.series || []).forEach((s) => {
      if (s.branch && !m[s.branch]) m[s.branch] = "#93a4b3";
    });
    return m;
  }, [data]);

  const series = useMemo(
    () => (data?.series || []).filter((s) => !branchFilter || s.branch === branchFilter),
    [data, branchFilter],
  );

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>Security Timeline</h1>
      <p className="muted">커밋 시각에 따른 브랜치별 보안 상태(합성 점수 = 10·critical + 5·high + 2·medium + 1·low).</p>
      <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
      {error && <div className="alert-error">{error}</div>}
      {repositoryId && data === null && <p className="muted">불러오는 중...</p>}

      {data && (data.series || []).length === 0 && (
        <div className="dashboard-empty">아직 보안 분석이 끝난 스캔이 없습니다.</div>
      )}

      {data && (data.branches || []).length > 0 && (
        <div className="risk-summary">
          <span className="pill" onClick={() => setBranchFilter(null)}
                style={{ cursor: "pointer", opacity: branchFilter ? 0.5 : 1 }}>전체</span>
          {data.branches.map((b) => (
            <span key={b.name} className="pill"
                  style={{ cursor: "pointer", borderColor: branchColors[b.name],
                           opacity: branchFilter && branchFilter !== b.name ? 0.4 : 1 }}
                  onClick={() => setBranchFilter(b.name)}>
              {b.is_default ? "★ " : ""}{b.name}
              {b.deleted_at ? " (삭제됨)" : ""} · {b.n_snapshots}
            </span>
          ))}
        </div>
      )}

      {series.length > 0 && (
        <TimelineChart series={series} branchColors={branchColors} onPick={setPicked} />
      )}

      {picked && (
        <DiffPanel repositoryId={repositoryId} head={picked} onClose={() => setPicked(null)} />
      )}
    </div>
  );
}
