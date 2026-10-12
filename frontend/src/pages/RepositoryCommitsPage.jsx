import { Fragment, useEffect, useState } from "react";
import { fetchBranches, fetchCommitFiles, fetchRepositoryCommits } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

const CHANGE_LABEL = { added: "추가", modified: "수정", removed: "삭제", renamed: "이름변경" };

function CommitFiles({ repositoryId, sha }) {
  const [files, setFiles] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetchCommitFiles(repositoryId, sha).then(setFiles).catch((err) => setError(err.message));
  }, [repositoryId, sha]);

  if (error) return <div className="alert-error">{error}</div>;
  if (files === null) return <p className="muted">불러오는 중...</p>;
  if (files.length === 0) return <p className="muted">변경된 파일 정보가 없습니다.</p>;

  return (
    <ul className="commit-file-list">
      {files.map((f) => (
        <li key={f.path}>
          <span className="badge">{CHANGE_LABEL[f.change_type] || f.change_type}</span> {f.path}{" "}
          <span className="muted">+{f.additions} -{f.deletions}</span>
        </li>
      ))}
    </ul>
  );
}

export default function RepositoryCommitsPage() {
  const { activeProject } = useProject();
  const [repositoryId, setRepositoryId] = useState(null);
  const [branches, setBranches] = useState(null);
  const [branch, setBranch] = useState("");
  const [commits, setCommits] = useState(null);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(null);

  useEffect(() => {
    if (!repositoryId) return;
    setBranch("");
    fetchBranches(repositoryId).then(setBranches).catch(() => setBranches([]));
  }, [repositoryId]);

  useEffect(() => {
    if (!repositoryId) return;
    setCommits(null);
    setError(null);
    fetchRepositoryCommits(repositoryId, branch).then(setCommits).catch((err) => setError(err.message));
  }, [repositoryId, branch]);

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>커밋</h1>
      <div className="dashboard-section-header">
        <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
        {branches && branches.length > 0 && (
          <select className="repo-select" value={branch} onChange={(e) => setBranch(e.target.value)}>
            {branches.map((b) => (
              <option key={b.name} value={b.name}>
                {b.name}
                {b.is_default ? " (기본)" : ""}
              </option>
            ))}
          </select>
        )}
      </div>
      {error && <div className="alert-error">{error}</div>}
      {commits === null ? (
        <p className="muted">불러오는 중...</p>
      ) : commits.length === 0 ? (
        <div className="dashboard-empty">커밋이 없습니다.</div>
      ) : (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>커밋</th>
                <th>메시지</th>
                <th>작성자</th>
                <th>시각</th>
              </tr>
            </thead>
            <tbody>
              {commits.map((c) => (
                <Fragment key={c.commit_sha}>
                  <tr
                    className="commit-row"
                    onClick={() => setExpanded(expanded === c.commit_sha ? null : c.commit_sha)}
                  >
                    <td className="muted">{c.commit_sha.slice(0, 8)}</td>
                    <td>{c.message?.split("\n")[0]}</td>
                    <td className="muted">{c.author_name}</td>
                    <td className="muted">{c.committed_at ? new Date(c.committed_at).toLocaleString() : ""}</td>
                  </tr>
                  {expanded === c.commit_sha && (
                    <tr>
                      <td colSpan={4}>
                        <CommitFiles repositoryId={repositoryId} sha={c.commit_sha} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
