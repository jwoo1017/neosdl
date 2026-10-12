import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  createProjectInvite,
  deleteProject,
  detachRepositoryFromProject,
  fetchProject,
  fetchProjectRepositories,
  removeProjectMember,
  updateProject,
  updateProjectMemberRole,
} from "../api";
import { useProject } from "../ProjectContext";
import ProjectInviteList from "../components/ProjectInviteList";

const ROLES = ["owner", "manager", "developer", "viewer"];

export default function ProjectSettingsPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();
  const { activeProjectId, setActiveProjectId, hasProjectRole, reload: reloadProjects } = useProject();

  const [project, setProject] = useState(null);
  const [repos, setRepos] = useState(null);
  const [error, setError] = useState(null);
  const [nameDraft, setNameDraft] = useState("");
  const [inviteRole, setInviteRole] = useState("developer");
  const [inviteEmail, setInviteEmail] = useState("");
  const [lastInvite, setLastInvite] = useState(null);
  const [inviteListVersion, setInviteListVersion] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    if (projectId && projectId !== activeProjectId) setActiveProjectId(projectId);
  }, [projectId, activeProjectId, setActiveProjectId]);

  const load = () => {
    fetchProject(projectId)
      .then((p) => {
        setProject(p);
        setNameDraft(p.name);
      })
      .catch((err) => setError(err.message));
    fetchProjectRepositories(projectId).then(setRepos).catch(() => {});
  };

  useEffect(load, [projectId]);

  const canManage = hasProjectRole("manager");
  const isOwner = hasProjectRole("owner");

  const saveName = async (e) => {
    e.preventDefault();
    try {
      await updateProject(projectId, nameDraft);
      await reloadProjects();
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const submitInvite = async (e) => {
    e.preventDefault();
    try {
      const invite = await createProjectInvite(projectId, inviteRole, inviteEmail.trim() || undefined);
      setLastInvite(invite);
      setInviteEmail("");
      setInviteListVersion((v) => v + 1);
    } catch (err) {
      setError(err.message);
    }
  };

  const changeMemberRole = async (memberId, role) => {
    try {
      await updateProjectMemberRole(projectId, memberId, role);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const doRemoveMember = async (memberId) => {
    try {
      await removeProjectMember(projectId, memberId);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const doDetachRepo = async (repositoryId) => {
    try {
      await detachRepositoryFromProject(projectId, repositoryId);
      load();
    } catch (err) {
      setError(err.message);
    }
  };

  const doDeleteProject = async () => {
    try {
      await deleteProject(projectId);
      await reloadProjects();
      navigate("/workspace");
    } catch (err) {
      setError(err.message);
    }
  };

  if (!project) {
    return <div className="dashboard">{error ? <div className="alert-error">{error}</div> : "불러오는 중..."}</div>;
  }

  return (
    <div className="dashboard">
      <h1>프로젝트 관리 · {project.name}</h1>
      {error && <div className="alert-error">{error}</div>}

      <section className="dashboard-section">
        <h2>프로젝트 정보</h2>
        {canManage ? (
          <form className="inline-form" onSubmit={saveName}>
            <input value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} />
            <button type="submit">저장</button>
          </form>
        ) : (
          <p className="muted">{project.name}</p>
        )}
      </section>

      <section className="dashboard-section">
        <h2>연결된 레포지토리</h2>
        {repos === null ? (
          <p className="muted">불러오는 중...</p>
        ) : repos.length === 0 ? (
          <p className="muted">아직 연결된 레포지토리가 없습니다. 대시보드에서 연결하세요.</p>
        ) : (
          <div className="table-scroll">
            <table className="repo-table">
              <thead>
                <tr>
                  <th>레포지토리</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {repos.map((r) => (
                  <tr key={r.id}>
                    <td>{r.namespace}/{r.name}</td>
                    <td>{canManage && <button onClick={() => doDetachRepo(r.id)}>연결 해제</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="dashboard-section">
        <h2>멤버</h2>
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>계정</th>
                <th>역할</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {project.members.map((m) => {
                const canEditThisRow = canManage && (m.role !== "owner" || isOwner);
                return (
                  <tr key={m.member_id}>
                    <td>{m.nickname}</td>
                    <td>
                      {canEditThisRow ? (
                        <select
                          value={m.role}
                          onChange={(e) => changeMemberRole(m.member_id, e.target.value)}
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r} disabled={r === "owner" && !isOwner}>
                              {r}
                            </option>
                          ))}
                        </select>
                      ) : (
                        m.role
                      )}
                    </td>
                    <td>
                      {canEditThisRow && <button onClick={() => doRemoveMember(m.member_id)}>제거</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {canManage && (
        <section className="dashboard-section">
          <h2>팀원 초대</h2>
          <form className="inline-form" onSubmit={submitInvite}>
            <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
              {ROLES.map((r) => (
                <option key={r} value={r} disabled={r === "owner" && !isOwner}>
                  {r}
                </option>
              ))}
            </select>
            <input
              placeholder="이메일 (선택, 비우면 링크 소지자 누구나)"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
            />
            <button type="submit">초대 링크 생성</button>
          </form>
          {lastInvite && (
            <p className="muted invite-url">
              {lastInvite.target_email
                ? lastInvite.email_sent
                  ? "메일을 발송했습니다: "
                  : "메일 발송 실패 — 아래 링크를 직접 전달하세요: "
                : "생성됨: "}
              <code>{lastInvite.invite_url}</code>
            </p>
          )}
          <ProjectInviteList projectId={projectId} version={inviteListVersion} />
        </section>
      )}

      {isOwner && (
        <section className="dashboard-section danger-zone">
          <h2>프로젝트 삭제</h2>
          <p className="muted">
            프로젝트를 삭제하면 연결된 레포지토리/스캔은 감사 로그에는 남지만 더 이상 어떤 프로젝트에서도
            조회되지 않습니다. 되돌릴 수 없습니다.
          </p>
          {confirmDelete ? (
            <div className="inline-form">
              <button className="btn-danger" onClick={doDeleteProject}>정말 삭제합니다</button>
              <button onClick={() => setConfirmDelete(false)}>취소</button>
            </div>
          ) : (
            <button className="btn-danger" onClick={() => setConfirmDelete(true)}>프로젝트 삭제</button>
          )}
        </section>
      )}
    </div>
  );
}
