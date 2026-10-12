import { useEffect, useState } from "react";
import { createWhitelistEntry, deleteWhitelistEntry, fetchProjectRepositories, fetchWhitelist } from "../api";
import { useProject } from "../ProjectContext";

export default function SecurityWhitelistPage() {
  const { activeProject, hasProjectRole } = useProject();
  const [repos, setRepos] = useState(null);
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ repository_id: "", category: "", sink_symbol_key: "", reason: "" });
  const [submitting, setSubmitting] = useState(false);

  const load = () => {
    if (!activeProject) return;
    fetchWhitelist(activeProject.id).then(setEntries).catch((err) => setError(err.message));
  };

  useEffect(() => {
    if (!activeProject) return;
    fetchProjectRepositories(activeProject.id)
      .then((list) => {
        setRepos(list);
        setForm((f) => ({ ...f, repository_id: f.repository_id || list[0]?.id || "" }));
      })
      .catch(() => setRepos([]));
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject?.id]);

  const submit = async (e) => {
    e.preventDefault();
    if (!form.repository_id || !form.category) return;
    setSubmitting(true);
    setError(null);
    try {
      await createWhitelistEntry(activeProject.id, {
        repository_id: form.repository_id,
        category: form.category,
        sink_symbol_key: form.sink_symbol_key || null,
        reason: form.reason || null,
      });
      setForm((f) => ({ ...f, category: "", sink_symbol_key: "", reason: "" }));
      load();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (id) => {
    try {
      await deleteWhitelistEntry(activeProject.id, id);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>Whitelist</h1>
      <p className="muted">오탐이거나 이미 검토해 승인한 위험을 카테고리 단위로(또는 특정 sink만) 억제합니다.</p>
      {error && <div className="alert-error">{error}</div>}

      {hasProjectRole("manager") && (
        <form className="inline-form" onSubmit={submit}>
          <select
            value={form.repository_id}
            onChange={(e) => setForm((f) => ({ ...f, repository_id: e.target.value }))}
          >
            {(repos || []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.namespace}/{r.name}
              </option>
            ))}
          </select>
          <input
            placeholder="category (예: sql_injection)"
            value={form.category}
            onChange={(e) => setForm((f) => ({ ...f, category: e.target.value }))}
          />
          <input
            placeholder="sink_symbol_key (비우면 카테고리 전체 억제)"
            value={form.sink_symbol_key}
            onChange={(e) => setForm((f) => ({ ...f, sink_symbol_key: e.target.value }))}
          />
          <input
            placeholder="사유"
            value={form.reason}
            onChange={(e) => setForm((f) => ({ ...f, reason: e.target.value }))}
          />
          <button className="btn-primary" type="submit" disabled={submitting}>
            추가
          </button>
        </form>
      )}

      {entries === null ? (
        <p className="muted">불러오는 중...</p>
      ) : entries.length === 0 ? (
        <p className="muted">등록된 화이트리스트 항목이 없습니다.</p>
      ) : (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>카테고리</th>
                <th>Sink</th>
                <th>사유</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id}>
                  <td>{e.category}</td>
                  <td className="muted">{e.sink_symbol_key || "(전체)"}</td>
                  <td className="muted">{e.reason}</td>
                  <td>
                    {hasProjectRole("manager") && <button onClick={() => remove(e.id)}>삭제</button>}
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
