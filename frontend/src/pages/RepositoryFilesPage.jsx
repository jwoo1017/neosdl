import { useEffect, useState } from "react";
import { fetchBranches, fetchRepoFileContent, fetchRepoTree } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

export default function RepositoryFilesPage() {
  const { activeProject } = useProject();
  const [repositoryId, setRepositoryId] = useState(null);
  const [branches, setBranches] = useState(null);
  const [branch, setBranch] = useState("");
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);
  const [content, setContent] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!repositoryId) return;
    setBranch("");
    setPath("");
    setSelectedFile(null);
    fetchBranches(repositoryId).then(setBranches).catch(() => setBranches([]));
  }, [repositoryId]);

  useEffect(() => {
    if (!repositoryId) return;
    setEntries(null);
    setError(null);
    fetchRepoTree(repositoryId, path, branch).then(setEntries).catch((err) => setError(err.message));
  }, [repositoryId, path, branch]);

  useEffect(() => {
    if (!selectedFile) {
      setContent(null);
      return;
    }
    setContent(null);
    fetchRepoFileContent(repositoryId, selectedFile, branch)
      .then((r) => setContent(r.content))
      .catch((err) => setError(err.message));
  }, [repositoryId, selectedFile, branch]);

  const crumbs = path.split("/").filter(Boolean);

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>파일</h1>
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

      <nav className="breadcrumb file-tree-breadcrumb">
        <button className="breadcrumb-segment-btn" onClick={() => setPath("")}>
          /
        </button>
        {crumbs.map((seg, i) => (
          <span key={i}>
            <span className="breadcrumb-sep">/</span>
            <button
              className="breadcrumb-segment-btn"
              onClick={() => setPath(crumbs.slice(0, i + 1).join("/"))}
            >
              {seg}
            </button>
          </span>
        ))}
      </nav>

      <div className="file-tree-layout">
        <div className="file-tree-list">
          {entries === null ? (
            <p className="muted">불러오는 중...</p>
          ) : entries.length === 0 ? (
            <p className="muted">비어 있습니다.</p>
          ) : (
            entries.map((e) => (
              <button
                key={e.path}
                className="sidebar-project-link"
                onClick={() => (e.type === "dir" ? setPath(e.path) : setSelectedFile(e.path))}
              >
                {e.type === "dir" ? "📁" : "📄"} {e.name}
              </button>
            ))
          )}
        </div>
        {selectedFile && (
          <div className="code-viewer">
            <div className="code-viewer-header">
              <span className="code-viewer-path">{selectedFile}</span>
              <button className="graph-search-close" onClick={() => setSelectedFile(null)}>
                ×
              </button>
            </div>
            <div className="code-viewer-body">
              {content === null ? (
                <div className="graph-search-empty">불러오는 중...</div>
              ) : (
                <pre>
                  {content.split("\n").map((text, i) => (
                    <div key={i} className="code-line">
                      <span className="code-line-no">{i + 1}</span>
                      <span className="code-line-text">{text}</span>
                    </div>
                  ))}
                </pre>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
