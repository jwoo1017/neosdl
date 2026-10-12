import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import GraphView from "../GraphView";
import { fetchScans } from "../api";
import { useProject } from "../ProjectContext";

export default function GraphViewerPage() {
  const { scanId: routeScanId } = useParams();
  const navigate = useNavigate();
  const { activeProject } = useProject();
  const [scanId, setScanId] = useState(routeScanId || "");
  const [recentScans, setRecentScans] = useState([]);

  useEffect(() => {
    if (!activeProject) return;
    fetchScans(activeProject.id).then(setRecentScans).catch(() => {});
  }, [activeProject?.id]);

  useEffect(() => {
    if (routeScanId) setScanId(routeScanId);
  }, [routeScanId]);

  const load = (id) => {
    if (!id || !activeProject) return;
    navigate(`/projects/${activeProject.id}/detail/graph/${id}`);
  };

  return (
    <div className="page">
      <header className="graph-page-header">
        <h1>그래프 뷰어</h1>
        <div className="app-controls">
          <input
            placeholder="scan_id 입력 (디버깅용)"
            value={scanId}
            onChange={(e) => setScanId(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load(scanId)}
          />
          <button onClick={() => load(scanId)}>불러오기</button>
          {recentScans.length > 0 && (
            <select
              defaultValue=""
              onChange={(e) => {
                if (e.target.value) load(e.target.value);
              }}
            >
              <option value="" disabled>
                최근 스캔에서 선택
              </option>
              {recentScans.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.commit_sha.slice(0, 8)} · {s.status} · {new Date(s.created_at).toLocaleString()}
                </option>
              ))}
            </select>
          )}
        </div>
      </header>
      <main className="app-main">
        {routeScanId ? (
          <GraphView scanId={routeScanId} />
        ) : (
          <div className="app-empty">scan_id를 입력하거나 최근 스캔을 선택하세요.</div>
        )}
      </main>
    </div>
  );
}
