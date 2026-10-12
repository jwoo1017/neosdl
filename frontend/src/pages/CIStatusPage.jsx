import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { fetchScans } from "../api";
import { useProject } from "../ProjectContext";


const TRIGGER_LABEL = {
  manual: "수동",
  webhook: "웹훅",
  schedule: "예약",
  backfill: "백필",
};

export default function CIStatusPage() {
  const { activeProject } = useProject();
  const [scans, setScans] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!activeProject) return;
    fetchScans(activeProject.id).then(setScans).catch((err) => setError(err.message));
  }, [activeProject?.id]);

  return (
    <div className="dashboard">
      <h1>웹훅 / CI 상태</h1>
      <p className="muted">
        대시보드에서 "웹훅 자동 스캔"을 켠 레포는 push/PR마다 여기 자동으로 스캔이 쌓입니다.
      </p>
      {error && <div className="alert-error">{error}</div>}
      {scans === null ? (
        <p className="muted">불러오는 중...</p>
      ) : scans.length === 0 ? (
        <div className="dashboard-empty">아직 스캔이 없습니다.</div>
      ) : (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>레포지토리</th>
                <th>커밋</th>
                <th>트리거</th>
                <th>상태</th>
                <th>시각</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {scans.map((s) => (
                <tr key={s.id}>
                  <td>
                    {s.repo_namespace}/{s.repo_name}
                  </td>
                  <td className="muted">{s.commit_sha.slice(0, 8)}</td>
                  <td>
                    <span className={`badge ${s.trigger === "webhook" ? "badge-webhook" : ""}`}>
                      {TRIGGER_LABEL[s.trigger] || s.trigger}
                    </span>
                  </td>
                  <td>
                    <span className={`badge badge-status-${s.status}`}>{s.status}</span>
                  </td>
                  <td className="muted">{new Date(s.created_at).toLocaleString()}</td>
                  <td>
                    <Link className="btn-primary" to={`/projects/${activeProject.id}/detail/graph/${s.id}`}>
                      그래프 보기
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
