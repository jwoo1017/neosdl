import { useEffect, useMemo, useState } from "react";
import { deleteProject, deleteWorkspace, fetchWorkspace, joinTeamspace, moveProject } from "../api";
import { useWorkspace } from "../WorkspaceContext";
import { useTeamspace } from "../TeamspaceContext";
import { useProject } from "../ProjectContext";
import { useTabs } from "../TabsContext";
import { pushRecentProject, removeRecentProject, removeRecentProjectsByWorkspace } from "../recentProjects";
import { PROJECT_DRAG_MIME } from "../dragTypes";
import TrashIcon from "../icons/TrashIcon";
import TagIcon from "../icons/TagIcon";
import PersonIcon from "../icons/PersonIcon";
import BuildingIcon from "../icons/BuildingIcon";
import CreateProjectModal from "../components/CreateProjectModal";
import CreateTeamspaceModal from "../components/CreateTeamspaceModal";
import InviteMemberModal from "../components/InviteMemberModal";
import WorkspaceInviteList from "../components/WorkspaceInviteList";

const VISIBILITY_LABEL = { open: "공개", closed: "참가 제한", private: "비공개" };

export default function WorkspaceHomePage() {
  const { openChatTab } = useTabs();
  const {
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    setActiveWorkspaceId,
    reload: reloadWorkspaces,
  } = useWorkspace();
  const { projects, setActiveProjectId, reload: reloadProjects } = useProject();
  const { teamspaces, reload: reloadTeamspaces } = useTeamspace();

  const [members, setMembers] = useState(null);
  const [error, setError] = useState(null);
  const [createProjectIn, setCreateProjectIn] = useState(null); // teamspace 객체 또는 null
  const [showCreateTeamspace, setShowCreateTeamspace] = useState(false);
  const [showInviteMember, setShowInviteMember] = useState(false);
  const [inviteListVersion, setInviteListVersion] = useState(0);
  const [confirmDeleteProject, setConfirmDeleteProject] = useState(null); // project id
  const [confirmDeleteWorkspace, setConfirmDeleteWorkspace] = useState(null); // workspace id
  const [joiningId, setJoiningId] = useState(null); // teamspace id
  const [dragOverTeamspaceId, setDragOverTeamspaceId] = useState(null);

  const projectsByTeamspace = useMemo(() => {
    const map = {};
    (projects || []).forEach((p) => {
      (map[p.teamspace_id] ||= []).push(p);
    });
    return map;
  }, [projects]);

  useEffect(() => {
    if (!activeWorkspaceId || !activeWorkspace) {
      setMembers(null);
      return;
    }
    fetchWorkspace(activeWorkspaceId)
      .then((w) => {
        setMembers(w.members);
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, [activeWorkspaceId, activeWorkspace]);

  const openProject = (project) => {
    setActiveProjectId(project.id);
    pushRecentProject({
      id: project.id,
      name: project.name,
      workspaceId: activeWorkspaceId,
      teamspaceId: project.teamspace_id,
    });
    openChatTab(project);
  };

  const doJoinTeamspace = async (teamspaceId) => {
    setJoiningId(teamspaceId);
    try {
      await joinTeamspace(teamspaceId);
      await reloadTeamspaces();
    } catch (err) {
      setError(err.message);
    } finally {
      setJoiningId(null);
    }
  };

  const moveProjectToTeamspace = async (projectId, teamspace) => {
    const current = (projects || []).find((p) => p.id === projectId);
    if (current?.teamspace_id === teamspace.id) return; // 같은 teamspace로 드롭 — 할 일 없음
    setError(null);
    try {
      await moveProject(projectId, teamspace.id);
      await reloadProjects();
    } catch (err) {
      setError(err.message);
    }
  };

  const doDeleteProject = async (projectId) => {
    try {
      await deleteProject(projectId);
      removeRecentProject(projectId);
      await reloadProjects();
      setConfirmDeleteProject(null);
    } catch (err) {
      setError(err.message);
    }
  };

  const doDeleteWorkspace = async (workspaceId) => {
    try {
      await deleteWorkspace(workspaceId);
      removeRecentProjectsByWorkspace(workspaceId);
      await reloadWorkspaces();
      setConfirmDeleteWorkspace(null);
    } catch (err) {
      setError(err.message);
    }
  };

  if (workspaces === null) {
    return <div className="dashboard">불러오는 중...</div>;
  }

  return (
    <div className="dashboard">
      <h1>워크스페이스 홈{activeWorkspace ? ` · ${activeWorkspace.name}` : ""}</h1>
      {error && <div className="alert-error">{error}</div>}

      <section className="dashboard-section">
        <div className="dashboard-section-header">
          <h2>팀스페이스 · 프로젝트</h2>
          {activeWorkspaceId && (
            <button className="btn-primary" onClick={() => setShowCreateTeamspace(true)}>
              + 새 팀스페이스
            </button>
          )}
        </div>

        {!activeWorkspaceId ? (
          <p className="muted">워크스페이스를 먼저 선택하세요.</p>
        ) : teamspaces === null || projects === null ? (
          <p className="muted">불러오는 중...</p>
        ) : teamspaces.length === 0 ? (
          <p className="muted">이 워크스페이스엔 아직 팀스페이스가 없습니다.</p>
        ) : (
          teamspaces.map((ts) => {
            const tsProjects = projectsByTeamspace[ts.id] || [];
            const isMember = ts.my_role != null;
            return (
              <div
                className={`teamspace-block ${dragOverTeamspaceId === ts.id ? "teamspace-block-drag-over" : ""}`}
                key={ts.id}
                onDragOver={(e) => {
                  if (!isMember) return; // 가입하지 않은 teamspace는 드롭 대상이 아니다.
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  setDragOverTeamspaceId(ts.id);
                }}
                onDragLeave={() => setDragOverTeamspaceId((id) => (id === ts.id ? null : id))}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOverTeamspaceId(null);
                  if (!isMember) return;
                  const projectId = e.dataTransfer.getData(PROJECT_DRAG_MIME);
                  if (projectId) moveProjectToTeamspace(projectId, ts);
                }}
              >
                <div className="dashboard-section-header teamspace-block-header">
                  <h3>
                    {ts.name}
                    {ts.visibility !== "open" && <span className="badge">{VISIBILITY_LABEL[ts.visibility]}</span>}
                  </h3>
                  {isMember ? (
                    <button className="btn-primary" onClick={() => setCreateProjectIn(ts)}>
                      + 새 프로젝트
                    </button>
                  ) : ts.visibility === "open" ? (
                    <button className="btn-primary" disabled={joiningId === ts.id} onClick={() => doJoinTeamspace(ts.id)}>
                      {joiningId === ts.id ? "가입하는 중..." : "가입하기"}
                    </button>
                  ) : (
                    <span className="muted">가입하려면 초대가 필요합니다</span>
                  )}
                </div>
                {ts.description && <p className="muted">{ts.description}</p>}

                {tsProjects.length === 0 ? (
                  <p className="muted">아직 프로젝트가 없습니다.</p>
                ) : (
                  <div className="project-grid">
                    {tsProjects.map((p) => (
                      <div
                        className="project-card"
                        key={p.id}
                        draggable
                        onDragStart={(e) => {
                          e.dataTransfer.setData(PROJECT_DRAG_MIME, p.id);
                          e.dataTransfer.effectAllowed = "move";
                        }}
                      >
                        <div className="project-card-header">
                          <button className="project-card-name" onClick={() => openProject(p)}>
                            {p.name}
                          </button>
                          {p.role === "owner" && (
                            confirmDeleteProject === p.id ? (
                              <button className="icon-btn icon-btn-danger" onClick={() => doDeleteProject(p.id)} title="정말 삭제">
                                <TrashIcon />
                              </button>
                            ) : (
                              <button
                                className="icon-btn icon-btn-danger"
                                onClick={() => setConfirmDeleteProject(p.id)}
                                onBlur={() => setConfirmDeleteProject(null)}
                                title="프로젝트 삭제"
                              >
                                <TrashIcon />
                              </button>
                            )
                          )}
                        </div>
                        {p.role && <span className="badge">{p.role}</span>}
                        {p.spec_tags?.length > 0 && (
                          <div className="tag-list">
                            {p.spec_tags.map((tag) => (
                              <span className="badge spec-tag-badge" key={tag}>
                                <TagIcon /> {tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </section>

      {activeWorkspaceId && (
        <section className="dashboard-section">
          <div className="dashboard-section-header">
            <h2>멤버</h2>
            {activeWorkspace?.role === "admin" && (
              <button className="btn-primary" onClick={() => setShowInviteMember(true)}>
                + 멤버 추가
              </button>
            )}
          </div>
          {members === null ? (
            <p className="muted">불러오는 중...</p>
          ) : (
            <div className="table-scroll">
              <table className="repo-table">
                <thead>
                  <tr>
                    <th>계정</th>
                    <th>역할</th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((m) => (
                    <tr key={m.member_id}>
                      <td>{m.nickname}</td>
                      <td>{m.role}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {activeWorkspace?.role === "admin" && (
            <WorkspaceInviteList workspaceId={activeWorkspaceId} version={inviteListVersion} filter="pending" />
          )}
        </section>
      )}

      <section className="dashboard-section">
        <h2>내 워크스페이스</h2>
        <ul className="workspace-list">
          {workspaces.map((w) => (
            <li className={`workspace-list-item ${w.id === activeWorkspaceId ? "active" : ""}`} key={w.id}>
              {w.type === "personal" ? <PersonIcon size={16} /> : <BuildingIcon size={16} />}
              <button className="workspace-list-name" onClick={() => setActiveWorkspaceId(w.id)}>
                {w.name}
              </button>
              <span className="badge">{w.role}</span>
              {w.role === "admin" && (
                confirmDeleteWorkspace === w.id ? (
                  <button className="icon-btn icon-btn-danger" onClick={() => doDeleteWorkspace(w.id)} title="정말 삭제">
                    <TrashIcon />
                  </button>
                ) : (
                  <button
                    className="icon-btn icon-btn-danger"
                    onClick={() => setConfirmDeleteWorkspace(w.id)}
                    onBlur={() => setConfirmDeleteWorkspace(null)}
                    title="워크스페이스 삭제"
                  >
                    <TrashIcon />
                  </button>
                )
              )}
            </li>
          ))}
        </ul>
      </section>

      {createProjectIn && (
        <CreateProjectModal
          teamspaceId={createProjectIn.id}
          onCreated={reloadProjects}
          onClose={() => setCreateProjectIn(null)}
        />
      )}

      {showCreateTeamspace && (
        <CreateTeamspaceModal
          workspaceId={activeWorkspaceId}
          onCreated={() => reloadTeamspaces()}
          onClose={() => setShowCreateTeamspace(false)}
        />
      )}

      {showInviteMember && (
        <InviteMemberModal
          workspaceId={activeWorkspaceId}
          onInvited={() => setInviteListVersion((v) => v + 1)}
          onClose={() => setShowInviteMember(false)}
        />
      )}
    </div>
  );
}
