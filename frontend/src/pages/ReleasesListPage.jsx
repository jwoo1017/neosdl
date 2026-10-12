import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchReleases, syncReleases } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

export default function ReleasesListPage() {
  const { activeProject, hasProjectRole } = useProject();
  const navigate = useNavigate();
  const [repositoryId, setRepositoryId] = useState(null);
  const [releases, setReleases] = useState(null);
  const [error, setError] = useState(null);
  const [syncing, setSyncing] = useState(false);

  const load = () => {
    if (!repositoryId) return;
    setError(null);
    fetchReleases(repositoryId).then(setReleases).catch((err) => setError(err.message));
  };

  useEffect(() => {
    setReleases(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repositoryId]);

  const sync = async () => {
    setSyncing(true);
    setError(null);
    try {
      await syncReleases(repositoryId);
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setSyncing(false);
    }
  };

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <div className="dashboard-section-header">
        <h1>Releases</h1>
        {hasProjectRole("manager") && repositoryId && (
          <button className="btn-primary" onClick={sync} disabled={syncing}>
            {syncing ? "동기화 중..." : "GitHub에서 동기화"}
          </button>
        )}
      </div>
      <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
      {error && <div className="alert-error">{error}</div>}
      {releases === null ? (
        <p className="muted">불러오는 중...</p>
      ) : releases.length === 0 ? (
        <div className="dashboard-empty">
          아직 동기화된 릴리즈가 없습니다 — 위 "GitHub에서 동기화"를 눌러보세요.
        </div>
      ) : (
        <div className="release-card-list">
          {releases.map((r) => (
            <button
              key={r.id}
              className="release-card"
              onClick={() => navigate(`/projects/${activeProject.id}/detail/releases/${r.id}?repository_id=${repositoryId}`)}
            >
              <div className="release-card-title">
                {r.name || r.tag_name}
                {r.is_prerelease && <span className="badge">prerelease</span>}
                {r.is_draft && <span className="badge">draft</span>}
              </div>
              <div className="muted">
                {r.tag_name} · {r.published_at ? new Date(r.published_at).toLocaleDateString() : "미발행"}
              </div>
              {!r.scan_id && <div className="muted">아직 이 릴리즈 시점 스캔이 없습니다</div>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
