import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { fetchRelease, requestScan } from "../api";
import { useProject } from "../ProjectContext";

const SEVERITY_EMOJI = { critical: "🔴", high: "🔴", medium: "🟡", low: "🟢" };

function deltaLabel(delta) {
  if (delta > 0) return `+${delta}`;
  if (delta < 0) return `${delta}`;
  return "변화없음";
}

export default function ReleaseDetailPage() {
  const { releaseId } = useParams();
  const [searchParams] = useSearchParams();
  const repositoryId = searchParams.get("repository_id");
  const navigate = useNavigate();
  const { activeProject } = useProject();

  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);
  const [scanning, setScanning] = useState(false);

  const load = () => {
    if (!repositoryId || !releaseId) return;
    setError(null);
    fetchRelease(repositoryId, releaseId).then(setDetail).catch((err) => setError(err.message));
  };

  useEffect(() => {
    setDetail(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repositoryId, releaseId]);

  const scanThisRelease = async () => {
    if (!detail?.commit_sha) return;
    setScanning(true);
    setError(null);
    try {
      await requestScan(repositoryId, { commit_sha: detail.commit_sha, ref: `refs/tags/${detail.release.tag_name}` });
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setScanning(false);
    }
  };

  if (!repositoryId) return <div className="dashboard">잘못된 접근입니다 — Releases 목록에서 다시 들어와 주세요.</div>;
  if (error) return <div className="dashboard"><div className="alert-error">{error}</div></div>;
  if (detail === null) return <div className="dashboard">불러오는 중...</div>;

  const r = detail.release;

  return (
    <div className="dashboard">
      <h1>{r.name || r.tag_name}</h1>
      <p className="muted">{r.tag_name}</p>

      <section className="dashboard-section">
        <h2>Changes</h2>
        <div className="release-stat-row">
          <div className="release-stat">
            <div className="release-stat-value">{detail.changes.commits ?? "—"}</div>
            <div className="muted">commits</div>
          </div>
          <div className="release-stat">
            <div className="release-stat-value">{detail.pull_request_count ?? "—"}</div>
            <div className="muted">PRs</div>
          </div>
          <div className="release-stat">
            <div className="release-stat-value">{detail.changes.files_changed ?? "—"}</div>
            <div className="muted">files changed</div>
          </div>
        </div>
      </section>

      <section className="dashboard-section">
        <h2>Security</h2>
        {detail.security === null ? (
          <div className="dashboard-empty">
            <p>이 릴리즈는 아직 스캔되지 않았습니다.</p>
            <button className="btn-primary" onClick={scanThisRelease} disabled={scanning}>
              {scanning ? "스캔 요청 중..." : "이 릴리즈 스캔하기"}
            </button>
          </div>
        ) : detail.security.length === 0 ? (
          <p className="muted">탐지된 보안 findings가 없습니다.</p>
        ) : (
          <ul className="release-security-list">
            {detail.security.map((s) => (
              <li key={s.category}>
                {SEVERITY_EMOJI[s.severity] || "⚪"} {s.category} ({s.count}) — {deltaLabel(s.delta)}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="dashboard-section">
        <h2>Business Impact</h2>
        {detail.business_impact.length === 0 ? (
          <p className="muted">감지된 도메인 변경이 없습니다.</p>
        ) : (
          <ul>
            {detail.business_impact.map((label, i) => (
              <li key={i}>{label}</li>
            ))}
          </ul>
        )}
      </section>

      <section className="dashboard-section">
        <h2>Code Graph</h2>
        {detail.scan_id ? (
          <button className="btn-primary" onClick={() => navigate(`/projects/${activeProject.id}/detail/graph/${detail.scan_id}`)}>
            이 릴리즈의 코드 그래프 보기
          </button>
        ) : (
          <p className="muted">스캔이 있어야 코드 그래프를 볼 수 있습니다.</p>
        )}
      </section>
    </div>
  );
}
