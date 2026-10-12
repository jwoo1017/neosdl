import { useEffect, useState } from "react";
import { fetchBranches } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

export default function RepositoryBranchesPage() {
  const { activeProject } = useProject();
  const [repositoryId, setRepositoryId] = useState(null);
  const [branches, setBranches] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!repositoryId) return;
    setBranches(null);
    setError(null);
    fetchBranches(repositoryId).then(setBranches).catch((err) => setError(err.message));
  }, [repositoryId]);

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>브랜치</h1>
      <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
      {error && <div className="alert-error">{error}</div>}
      {branches === null ? (
        <p className="muted">불러오는 중...</p>
      ) : branches.length === 0 ? (
        <div className="dashboard-empty">브랜치가 없습니다.</div>
      ) : (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>브랜치</th>
                <th>최신 커밋</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {branches.map((b) => (
                <tr key={b.name}>
                  <td>{b.name}</td>
                  <td className="muted">{b.commit_sha.slice(0, 8)}</td>
                  <td>
                    {b.is_default && <span className="badge">기본</span>}
                    {b.protected && <span className="badge">보호됨</span>}
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
