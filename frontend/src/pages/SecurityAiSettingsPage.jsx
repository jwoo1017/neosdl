import { useEffect, useState } from "react";
import { fetchAiUsage, fetchModels, fetchProjectAiSettings, updateProjectAiSettings } from "../api";
import { useProject } from "../ProjectContext";

export default function SecurityAiSettingsPage() {
  const { activeProject, hasProjectRole } = useProject();
  const [settings, setSettings] = useState(null);
  const [usage, setUsage] = useState(null);
  const [models, setModels] = useState({ default: "", models: [] });
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!activeProject) return;
    fetchProjectAiSettings(activeProject.id).then(setSettings).catch((err) => setError(err.message));
    fetchAiUsage(activeProject.id).then(setUsage).catch(() => {});
    fetchModels().then(setModels).catch(() => {});
  }, [activeProject?.id]);

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await updateProjectAiSettings(activeProject.id, settings);
      setSettings(updated);
      setSaved(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;
  if (settings === null) return <div className="dashboard">불러오는 중...</div>;

  return (
    <div className="dashboard">
      <h1>AI</h1>
      {error && <div className="alert-error">{error}</div>}

      <section className="dashboard-section">
        <h2>모델 선택</h2>
        <p className="muted">
          모든 호출은 LiteLLM 게이트웨이를 통과합니다. 비워두면 전역 기본값
          {models.default ? ` (${models.default})` : ""} 또는 채팅에서 고른 모델이 쓰입니다.
        </p>
        <form className="inline-form" onSubmit={save}>
          {[
            ["ci_model_name", "CI/CD 자동 분석용"],
            ["qa_model_name", "질문 응답용"],
          ].map(([field, label]) => (
            <label className="ai-settings-field" key={field}>
              {label}
              <select
                value={settings[field] || ""}
                disabled={!hasProjectRole("manager")}
                onChange={(e) => setSettings((s) => ({ ...s, [field]: e.target.value }))}
              >
                <option value="">전역 기본값</option>
                {(models.models || []).map((m) => (
                  <option key={m.id} value={m.id} disabled={m.available === false}>
                    {m.label}
                    {m.available === false ? " (키 없음)" : ""}
                  </option>
                ))}
                {settings[field] && !(models.models || []).some((m) => m.id === settings[field]) && (
                  <option value={settings[field]}>{settings[field]} (게이트웨이 미서빙)</option>
                )}
              </select>
            </label>
          ))}
          {hasProjectRole("manager") && (
            <button className="btn-primary" type="submit" disabled={saving}>
              {saving ? "저장 중..." : "저장"}
            </button>
          )}
          {saved && <span className="muted">저장됨</span>}
        </form>
      </section>

      <section className="dashboard-section">
        <h2>호출 통계</h2>
        {usage === null ? (
          <p className="muted">불러오는 중...</p>
        ) : usage.length === 0 ? (
          <p className="muted">아직 기록된 AI 호출이 없습니다.</p>
        ) : (
          <div className="table-scroll">
            <table className="repo-table">
              <thead>
                <tr>
                  <th>용도</th>
                  <th>Provider</th>
                  <th>모델</th>
                  <th>호출 수</th>
                  <th>성공</th>
                  <th>평균 지연(ms)</th>
                  <th>마지막 호출</th>
                </tr>
              </thead>
              <tbody>
                {usage.map((u, i) => (
                  <tr key={i}>
                    <td>{u.purpose === "ci_annotate" ? "CI/CD" : "질문"}</td>
                    <td className="muted">{u.provider}</td>
                    <td className="muted">{u.model}</td>
                    <td>{u.calls}</td>
                    <td>
                      {u.successes}/{u.calls}
                    </td>
                    <td className="muted">{u.avg_latency_ms}</td>
                    <td className="muted">{u.last_called_at ? new Date(u.last_called_at).toLocaleString() : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
