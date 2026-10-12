import { useEffect, useState } from "react";
import { createWhitelistEntry, fetchScans, fetchSecurityFindings } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";

const SEVERITY_BADGE = { critical: "badge-status-failed", high: "badge-status-failed",
  medium: "badge-status-running", low: "" };

export default function SecurityFindingsPage() {
  const { activeProject, hasProjectRole } = useProject();
  const [repositoryId, setRepositoryId] = useState(null);
  const [scanId, setScanId] = useState(null);
  const [findings, setFindings] = useState(null);
  const [error, setError] = useState(null);
  const [actionState, setActionState] = useState({});

  useEffect(() => {
    if (!repositoryId || !activeProject) return;
    setScanId(null);
    setFindings(null);
    setError(null);
    fetchScans(activeProject.id, repositoryId)
      .then((scans) => {
        if (scans.length === 0) {
          setError("이 레포지토리에 아직 스캔이 없습니다 — 대시보드에서 먼저 스캔을 실행하세요.");
          return;
        }
        setScanId(scans[0].id);
      })
      .catch((err) => setError(err.message));
  }, [repositoryId, activeProject]);

  useEffect(() => {
    if (!scanId) return;
    fetchSecurityFindings(scanId).then(setFindings).catch((err) => setError(err.message));
  }, [scanId]);

  const whitelist = async (finding) => {
    setActionState((s) => ({ ...s, [finding.id]: "running" }));
    try {
      await createWhitelistEntry(activeProject.id, {
        repository_id: repositoryId,
        category: finding.category,
        sink_symbol_key: finding.sink_symbol_key,
        reason: "대시보드에서 승인 처리",
      });
      setFindings((list) => list.filter((f) => f.id !== finding.id));
    } catch (err) {
      setActionState((s) => ({ ...s, [finding.id]: "error" }));
      setError(err.message);
    }
  };

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>Findings</h1>
      <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
      {error && <div className="alert-error">{error}</div>}
      {scanId && findings === null && <p className="muted">불러오는 중...</p>}
      {findings !== null && findings.length === 0 && (
        <div className="dashboard-empty">탐지된 findings가 없습니다.</div>
      )}
      {findings !== null && findings.length > 0 && (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>심각도</th>
                <th>카테고리</th>
                <th>위치</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {findings.map((f) => (
                <tr key={f.id}>
                  <td>
                    <span className={`badge ${SEVERITY_BADGE[f.severity] || ""}`}>{f.severity}</span>
                  </td>
                  <td>{f.category}</td>
                  <td className="muted">
                    {f.sink_symbol_key} : L{f.sink_line}
                  </td>
                  <td>
                    {hasProjectRole("manager") && (
                      <button onClick={() => whitelist(f)} disabled={actionState[f.id] === "running"}>
                        화이트리스트 추가
                      </button>
                    )}
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
