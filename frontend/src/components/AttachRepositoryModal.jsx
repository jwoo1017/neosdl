import { useEffect, useState } from "react";
import {
  attachRepositoryToProject,
  fetchInstallationRepositories,
  fetchMyInstallations,
  githubAppInstallUrl,
  registerRepository,
  requestScan,
} from "../api";
import { requestNotificationPermission, watchProjectAnalysis } from "../analysis/analysisWatch";
import Modal from "./Modal";

function activeInstallationKey(memberScope) {
  return `neosdl_active_installation_${memberScope}`;
}

export default function AttachRepositoryModal({
  projectId, projectName, githubAppSlug, attachedRemoteIds, onAttached, onClose,
}) {
  const [installations, setInstallations] = useState(null);
  const [installationId, setInstallationId] = useState(null);
  const [repos, setRepos] = useState(null);
  const [error, setError] = useState(null);
  const [actionState, setActionState] = useState({}); // remote_id -> {status, error, repositoryId}

  useEffect(() => {
    fetchMyInstallations()
      .then((list) => {
        setInstallations(list);
        const saved = localStorage.getItem(activeInstallationKey("me"));
        setInstallationId(list.find((i) => i.id === saved)?.id || list[0]?.id || null);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    if (!installationId) {
      setRepos(null);
      return;
    }
    localStorage.setItem(activeInstallationKey("me"), installationId);
    fetchInstallationRepositories(installationId).then(setRepos).catch((err) => setError(err.message));
  }, [installationId]);

  const connectInstallation = () => {
    if (!githubAppSlug) return;
    window.location.href = githubAppInstallUrl(githubAppSlug);
  };

  const registerAndAttach = async (repo) => {
    // 분석이 끝났을 때 다른 탭을 보고 있어도 알 수 있게 — 권한 요청은 클릭 안에서만 된다.
    requestNotificationPermission();
    setActionState((s) => ({ ...s, [repo.remote_id]: { status: "running" } }));
    try {
      // 이미 등록된 레포라도 다시 등록한다 — installation_id를 항상 이
      // installationId로 갱신해서, 예전에 연결이 끊긴 채 남아있던 레포도
      // (예: 소유 installation이 재설치된 경우) 다시 붙을 때 자동으로 복구된다.
      const registered = await registerRepository(installationId, repo);
      // 연결하면 서버가 곧바로 첫 분석을 시작한다(app/projects.py). 시작에 실패해도 연결은 된다.
      const result = await attachRepositoryToProject(projectId, registered.id);
      if (result.scan) watchProjectAnalysis(projectId, projectName);
      setActionState((s) => ({
        ...s,
        [repo.remote_id]: result.scan_error
          ? { status: "scan_error", repositoryId: registered.id }
          : { status: "done" },
      }));
      onAttached();
      fetchInstallationRepositories(installationId).then(setRepos).catch(() => {});
    } catch (err) {
      setActionState((s) => ({ ...s, [repo.remote_id]: { status: "error", error: err.message } }));
    }
  };

  const retryScan = async (repo, repositoryId) => {
    setActionState((s) => ({ ...s, [repo.remote_id]: { status: "retrying", repositoryId } }));
    try {
      await requestScan(repositoryId);
      watchProjectAnalysis(projectId, projectName);
      setActionState((s) => ({ ...s, [repo.remote_id]: { status: "done" } }));
    } catch {
      setActionState((s) => ({ ...s, [repo.remote_id]: { status: "scan_error", repositoryId } }));
    }
  };

  return (
    <Modal title="레포지토리 연결" onClose={onClose}>
      {error && <div className="alert-error">{error}</div>}

      {installations === null ? (
        <p className="muted">불러오는 중...</p>
      ) : (
        <div className="install-tab-strip">
          {installations.map((inst) => (
            <button
              key={inst.id}
              className={`install-tab ${inst.id === installationId ? "active" : ""}`}
              onClick={() => setInstallationId(inst.id)}
            >
              {inst.account_login}
            </button>
          ))}
          {githubAppSlug && (
            <button className="install-tab install-tab-add" onClick={connectInstallation}>
              + GitHub App 설치 연결하기
            </button>
          )}
        </div>
      )}

      {installations !== null && installations.length === 0 && (
        <div className="dashboard-empty">
          <p>연결된 GitHub App 설치가 없습니다.</p>
          {githubAppSlug && (
            <button className="btn-primary" onClick={connectInstallation}>
              GitHub App 설치하기
            </button>
          )}
        </div>
      )}

      {installationId && (
        repos === null ? (
          <p className="muted">레포지토리 불러오는 중...</p>
        ) : repos.length === 0 ? (
          <div className="dashboard-empty">
            접근 가능한 레포지토리가 없습니다. GitHub App 설치 시 레포지토리를 선택했는지 확인하세요.
          </div>
        ) : (
          <table className="repo-table">
            <thead>
              <tr>
                <th>레포지토리</th>
                <th>기본 브랜치</th>
                <th></th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {repos.map((repo) => {
                const state = actionState[repo.remote_id];
                const attached = attachedRemoteIds.has(repo.remote_id);
                return (
                  <tr key={repo.remote_id}>
                    <td>
                      {repo.full_name} <span className="badge">{repo.private ? "private" : "public"}</span>
                    </td>
                    <td className="muted">{repo.default_branch}</td>
                    <td>
                      {attached ? (
                        <span className="muted">이미 연결됨</span>
                      ) : (
                        <button onClick={() => registerAndAttach(repo)} disabled={state?.status === "running"}>
                          {state?.status === "running"
                            ? "처리 중..."
                            : repo.already_registered
                              ? "프로젝트에 연결"
                              : "등록 + 연결"}
                        </button>
                      )}
                    </td>
                    <td>
                      {state?.status === "error" && <span className="alert-error">{state.error}</span>}
                      {state?.status === "done" && (
                        <span className="attach-scan-note">분석을 시작했어요. 끝나면 알려드릴게요.</span>
                      )}
                      {(state?.status === "scan_error" || state?.status === "retrying") && (
                        <span className="attach-scan-note attach-scan-note-error">
                          연결했지만 분석을 시작하지 못했어요.{" "}
                          <button
                            type="button"
                            className="link-btn"
                            disabled={state.status === "retrying"}
                            onClick={() => retryScan(repo, state.repositoryId)}
                          >
                            {state.status === "retrying" ? "요청 중..." : "다시 분석"}
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )
      )}
    </Modal>
  );
}
