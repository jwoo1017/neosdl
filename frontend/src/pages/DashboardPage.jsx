import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  fetchConfig, fetchProjectRepositories, requestScan, updateRepositoryPrFix, updateRepositoryWebhook,
} from "../api";
import { useWorkspace } from "../WorkspaceContext";
import { useProject } from "../ProjectContext";
import AttachRepositoryModal from "../components/AttachRepositoryModal";
import LanguageBar from "../components/LanguageBar";

const ERROR_MESSAGES = {
  github_already_linked_elsewhere: "그 GitHub 계정은 이미 다른 회원 계정에 연결되어 있습니다.",
};

export default function DashboardPage() {
  const { workspaces } = useWorkspace();
  const { projects, activeProject, hasProjectRole } = useProject();
  const [searchParams] = useSearchParams();
  const [config, setConfig] = useState(null);
  const [projectRepos, setProjectRepos] = useState(null);
  const [error, setError] = useState(null);
  const [actionState, setActionState] = useState({}); // remote_id -> {status, scanId, error}
  const [showAttachModal, setShowAttachModal] = useState(false);

  const loadProjectRepos = () =>
    activeProject
      ? fetchProjectRepositories(activeProject.id).then(setProjectRepos).catch(() => {})
      : Promise.resolve();

  useEffect(() => {
    fetchConfig().then(setConfig).catch(() => {});
  }, []);

  useEffect(() => {
    setProjectRepos(null);
    loadProjectRepos();
  }, [activeProject?.id]);

  const runScan = async (repo) => {
    setActionState((s) => ({ ...s, [repo.remote_id]: { status: "running" } }));
    try {
      const scan = await requestScan(repo.id);
      setActionState((s) => ({ ...s, [repo.remote_id]: { status: "done", scanId: scan.id } }));
    } catch (err) {
      setActionState((s) => ({ ...s, [repo.remote_id]: { status: "error", error: err.message } }));
    }
  };

  const toggleWebhook = async (repo, enabled) => {
    setProjectRepos((list) => list.map((r) => (r.id === repo.id ? { ...r, webhook_ci_enabled: enabled } : r)));
    try {
      await updateRepositoryWebhook(repo.id, enabled);
    } catch (err) {
      setError(err.message);
      loadProjectRepos();
    }
  };

  // 마스코트 사이드바의 "PR 생성" 액션 — 저장소별 2차 동의 게이트(sql/026).
  const togglePrFix = async (repo, enabled) => {
    setProjectRepos((list) => list.map((r) => (r.id === repo.id ? { ...r, pr_fix_enabled: enabled } : r)));
    try {
      await updateRepositoryPrFix(repo.id, repo.webhook_ci_enabled, enabled);
    } catch (err) {
      setError(err.message);
      loadProjectRepos();
    }
  };

  const urlError = searchParams.get("error");

  if (workspaces === null) {
    return <div className="dashboard">불러오는 중...</div>;
  }

  if (workspaces.length === 0) {
    return (
      <div className="dashboard">
        <h1>대시보드</h1>
        {urlError && <div className="alert-error">{ERROR_MESSAGES[urlError] || urlError}</div>}
        <div className="dashboard-empty">
          <p>회원님은 아직 워크스페이스가 없습니다.</p>
          <p className="muted">왼쪽에서 새 워크스페이스를 만들거나, 초대받은 링크를 열어 합류하세요.</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="dashboard">
        <div className="alert-error">불러오지 못했습니다: {error}</div>
      </div>
    );
  }

  if (projects !== null && projects.length === 0) {
    return (
      <div className="dashboard">
        <h1>대시보드</h1>
        {urlError && <div className="alert-error">{ERROR_MESSAGES[urlError] || urlError}</div>}
        <div className="dashboard-empty">
          <p>이 워크스페이스엔 아직 프로젝트가 없습니다.</p>
          <Link className="btn-primary" to="/workspace">워크스페이스 홈에서 만들기</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard">
      <h1>Overview · {activeProject?.name}</h1>
      {urlError && <div className="alert-error">{ERROR_MESSAGES[urlError] || urlError}</div>}

      <section className="dashboard-section">
        <div className="dashboard-section-header">
          <h2>이 프로젝트에 연결된 레포지토리</h2>
          {hasProjectRole("manager") && (
            <button className="btn-primary" onClick={() => setShowAttachModal(true)}>
              + 레포지토리 연결
            </button>
          )}
        </div>
        {projectRepos === null ? (
          <p className="muted">불러오는 중...</p>
        ) : projectRepos.length === 0 ? (
          <p className="muted">아직 연결된 레포지토리가 없습니다 — 아래에서 등록/연결하세요.</p>
        ) : (
          <div className="table-scroll">
            <table className="repo-table">
              <thead>
                <tr>
                  <th>레포지토리</th>
                  <th>기본 브랜치</th>
                  <th>웹훅 자동 스캔</th>
                  <th>마스코트 PR 자동 생성</th>
                  <th></th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {projectRepos.map((repo) => {
                  const state = actionState[repo.remote_id];
                  return (
                    <tr key={repo.id}>
                      <td>
                        <div>{repo.namespace}/{repo.name}</div>
                        <LanguageBar repositoryId={repo.id} />
                      </td>
                      <td className="muted">{repo.default_branch}</td>
                      <td>
                        {hasProjectRole("manager") ? (
                          <label className="graph-toggle">
                            <input
                              type="checkbox"
                              checked={repo.webhook_ci_enabled}
                              onChange={(e) => toggleWebhook(repo, e.target.checked)}
                            />
                            push/PR 시 자동 스캔
                          </label>
                        ) : (
                          <span className="muted">{repo.webhook_ci_enabled ? "켜짐" : "꺼짐"}</span>
                        )}
                      </td>
                      <td>
                        {hasProjectRole("manager") ? (
                          <label className="graph-toggle">
                            <input
                              type="checkbox"
                              checked={repo.pr_fix_enabled}
                              onChange={(e) => togglePrFix(repo, e.target.checked)}
                            />
                            취약점 수정 PR 자동 생성
                          </label>
                        ) : (
                          <span className="muted">{repo.pr_fix_enabled ? "켜짐" : "꺼짐"}</span>
                        )}
                      </td>
                      <td>
                        {hasProjectRole("developer") && (
                          <button onClick={() => runScan(repo)} disabled={state?.status === "running"}>
                            {state?.status === "running" ? "실행 중..." : "스캔 실행"}
                          </button>
                        )}
                      </td>
                      <td>
                        {state?.status === "done" && state.scanId && (
                          <Link className="btn-primary" to={`/projects/${activeProject.id}/detail/graph/${state.scanId}`}>
                            그래프 보기
                          </Link>
                        )}
                        {state?.status === "error" && <span className="alert-error">{state.error}</span>}
                        {!state && (
                          <Link className="btn-primary" to={`/projects/${activeProject.id}/detail/graph`}>
                            최근 스캔 보기
                          </Link>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {showAttachModal && (
        <AttachRepositoryModal
          projectId={activeProject.id}
          projectName={activeProject.name}
          githubAppSlug={config?.github_app_slug}
          attachedRemoteIds={new Set((projectRepos || []).map((r) => r.remote_id))}
          onAttached={loadProjectRepos}
          onClose={() => setShowAttachModal(false)}
        />
      )}
    </div>
  );
}
