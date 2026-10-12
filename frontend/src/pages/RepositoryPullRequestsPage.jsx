import { useEffect, useState } from "react";
import { fetchPullRequests, syncPullRequests } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

export default function RepositoryPullRequestsPage() {
  const { activeProject, hasProjectRole } = useProject();
  const [repositoryId, setRepositoryId] = useState(null);
  const [prs, setPrs] = useState(null);
  const [error, setError] = useState(null);
  const [syncing, setSyncing] = useState(false);

  const load = () => {
    if (!repositoryId) return;
    setError(null);
    fetchPullRequests(repositoryId).then(setPrs).catch((err) => setError(err.message));
  };

  useEffect(() => {
    setPrs(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repositoryId]);

  const sync = async () => {
    setSyncing(true);
    setError(null);
    try {
      await syncPullRequests(repositoryId);
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
        <h1>Pull Requests</h1>
        {hasProjectRole("manager") && repositoryId && (
          <button className="btn-primary" onClick={sync} disabled={syncing}>
            {syncing ? "동기화 중..." : "GitHub에서 새로고침"}
          </button>
        )}
      </div>
      <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
      {error && <div className="alert-error">{error}</div>}
      {prs === null ? (
        <p className="muted">불러오는 중...</p>
      ) : prs.length === 0 ? (
        <div className="dashboard-empty">
          아직 기록된 PR이 없습니다 — GitHub App에 "Pull requests" 읽기 권한이 있는지, 위 "새로고침"을
          눌러보세요.
        </div>
      ) : (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>#</th>
                <th>제목</th>
                <th>상태</th>
                <th>브랜치</th>
                <th>작성자</th>
              </tr>
            </thead>
            <tbody>
              {prs.map((pr) => (
                <tr key={pr.id}>
                  <td className="muted">#{pr.number}</td>
                  <td>
                    {pr.html_url ? (
                      <a href={pr.html_url} target="_blank" rel="noreferrer">
                        {pr.title}
                      </a>
                    ) : (
                      pr.title
                    )}
                  </td>
                  <td>
                    <span className="badge">{pr.merged ? "merged" : pr.state}</span>
                  </td>
                  <td className="muted">
                    {pr.head_ref} → {pr.base_ref}
                  </td>
                  <td className="muted">{pr.author_login}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
