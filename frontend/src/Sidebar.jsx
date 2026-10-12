import { useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { joinTeamspace, logout, moveProject } from "./api";
import { useWorkspace } from "./WorkspaceContext";
import { useTeamspace } from "./TeamspaceContext";
import { useProject } from "./ProjectContext";
import { useSidebar } from "./SidebarContext";
import { useTabs } from "./TabsContext";
import { getRecentProjects, pruneRecentProjectsByValidWorkspaces, pushRecentProject } from "./recentProjects";
import { PROJECT_DRAG_MIME } from "./dragTypes";
import CreateWorkspaceModal from "./components/CreateWorkspaceModal";
import CreateTeamspaceModal from "./components/CreateTeamspaceModal";
import CreateProjectModal from "./components/CreateProjectModal";
import WorkspaceSettingsModal from "./components/WorkspaceSettingsModal";
import TeamspaceSettingsModal from "./components/TeamspaceSettingsModal";
import ProjectQuickMenuModal from "./components/ProjectQuickMenuModal";
import PlusIcon from "./icons/PlusIcon";
import MoreIcon from "./icons/MoreIcon";
import PanelCollapseIcon from "./icons/PanelCollapseIcon";
import logoUrl from "./assets/logo.svg?url";

const VISIBILITY_BADGE = { open: "공개", closed: "참가 제한", private: "비공개" };

function TeamspaceNode({ teamspace, projects, activeProjectId, onOpenProject, onCreateProject, onJoin, onMoveProject, onManage, onQuickMenu }) {
  const [expanded, setExpanded] = useState(true);
  const [joining, setJoining] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const isMember = teamspace.my_role != null;

  const join = async () => {
    setJoining(true);
    try {
      await onJoin(teamspace);
    } finally {
      setJoining(false);
    }
  };

  const handleDragOver = (e) => {
    if (!isMember) return; // 가입하지 않은 teamspace는 드롭 대상이 아니다.
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOver(true);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (!isMember) return;
    const projectId = e.dataTransfer.getData(PROJECT_DRAG_MIME);
    if (projectId) onMoveProject(projectId, teamspace);
  };

  return (
    <div
      className={`sidebar-teamspace ${dragOver ? "sidebar-teamspace-drag-over" : ""}`}
      onDragOver={handleDragOver}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      <div className="sidebar-teamspace-header">
        <button type="button" className="sidebar-teamspace-toggle" onClick={() => setExpanded((v) => !v)}>
          <span className="sidebar-teamspace-caret">{expanded ? "▾" : "▸"}</span> {teamspace.name}
          {teamspace.visibility !== "open" && (
            <span className="badge sidebar-teamspace-badge">{VISIBILITY_BADGE[teamspace.visibility]}</span>
          )}
        </button>
        {isMember && (
          <button
            type="button"
            className="icon-btn"
            onClick={() => onCreateProject(teamspace)}
            title="새 프로젝트"
          >
            <PlusIcon size={14} />
          </button>
        )}
        <button type="button" className="icon-btn" onClick={() => onManage(teamspace)} title="팀스페이스 관리">
          ⚙
        </button>
      </div>
      {expanded && (
        <div className="sidebar-teamspace-projects">
          {projects.length === 0 && <span className="muted sidebar-project-empty">프로젝트 없음</span>}
          {projects.map((p) => (
            <div key={p.id} className="sidebar-project-row">
              <button
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData(PROJECT_DRAG_MIME, p.id);
                  e.dataTransfer.effectAllowed = "move";
                }}
                className={`sidebar-project-link ${p.id === activeProjectId ? "active" : ""}`}
                onClick={() => onOpenProject(p, teamspace)}
              >
                {p.name}
              </button>
              <button
                type="button"
                className="icon-btn sidebar-project-menu-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onQuickMenu(p);
                }}
                title="프로젝트 메뉴"
              >
                <MoreIcon size={14} />
              </button>
            </div>
          ))}
          {!isMember && (
            teamspace.visibility === "open" ? (
              <button type="button" className="sidebar-project-add" onClick={join} disabled={joining}>
                {joining ? "가입하는 중..." : "가입하기"}
              </button>
            ) : (
              <span className="muted sidebar-project-empty">가입하려면 초대가 필요합니다</span>
            )
          )}
        </div>
      )}
    </div>
  );
}

export default function Sidebar({ me }) {
  const navigate = useNavigate();
  const { workspaces, activeWorkspaceId, setActiveWorkspaceId, reload: reloadWorkspaces } = useWorkspace();
  const { teamspaces, reload: reloadTeamspaces } = useTeamspace();
  const { projects, activeProjectId, setActiveProjectId, reload: reloadProjects } = useProject();
  const { collapsed, setCollapsed } = useSidebar();
  const { openChatTab, openDetailTab } = useTabs();

  const [showCreateWorkspace, setShowCreateWorkspace] = useState(false);
  const [showCreateTeamspace, setShowCreateTeamspace] = useState(false);
  const [createProjectIn, setCreateProjectIn] = useState(null); // teamspace 객체 또는 null
  const [showWorkspaceSettings, setShowWorkspaceSettings] = useState(false);
  const [manageTeamspace, setManageTeamspace] = useState(null); // teamspace 객체 또는 null
  const [quickMenuProject, setQuickMenuProject] = useState(null); // project 객체 또는 null
  const [recent, setRecent] = useState(() => getRecentProjects());
  const [dragError, setDragError] = useState(null);
  const location = useLocation();

  // teamspace/workspace/project 삭제 시 다른 컴포넌트(TeamspaceSettingsModal,
  // WorkspaceHomePage)가 localStorage의 최근 항목을 직접 정리하는데, 이
  // Sidebar 인스턴스는 그 변화를 모르니 페이지 이동이 있거나(삭제 후
  // /workspace로 이동) workspaces/projects 목록이 갱신될 때마다(같은 화면에
  // 머무른 채 삭제한 경우) localStorage를 다시 읽어 동기화한다.
  // 그와 별개로, 다른 기기에서 워크스페이스가 지워지거나 멤버가 제외된
  // 경우처럼 이 브라우저가 직접 지운 게 아닌 경우도 있으므로, 서버가 내려준
  // 최신 워크스페이스 목록에 없는 workspaceId를 가리키는 항목은 여기서
  // 같이 걸러낸다. workspaces가 아직 null(로딩 중)일 땐 건드리지 않는다.
  useEffect(() => {
    if (workspaces === null) return;
    setRecent(pruneRecentProjectsByValidWorkspaces(workspaces.map((w) => w.id)));
  }, [location.pathname, workspaces, projects]);

  const projectsByTeamspace = useMemo(() => {
    const map = {};
    (projects || []).forEach((p) => {
      (map[p.teamspace_id] ||= []).push(p);
    });
    return map;
  }, [projects]);

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  const openProject = (project) => {
    setActiveProjectId(project.id);
    pushRecentProject({
      id: project.id,
      name: project.name,
      workspaceId: activeWorkspaceId,
      teamspaceId: project.teamspace_id,
    });
    setRecent(getRecentProjects());
    openChatTab(project);
  };

  const openRecent = (item) => {
    if (item.workspaceId && item.workspaceId !== activeWorkspaceId) setActiveWorkspaceId(item.workspaceId);
    setActiveProjectId(item.id);
    openChatTab(item);
  };

  const moveProjectToTeamspace = async (projectId, teamspace) => {
    const current = (projects || []).find((p) => p.id === projectId);
    if (current?.teamspace_id === teamspace.id) return; // 같은 teamspace로 드롭 — 할 일 없음
    setDragError(null);
    try {
      await moveProject(projectId, teamspace.id);
      await reloadProjects();
    } catch (err) {
      setDragError(err.message);
    }
  };

  // 워크스페이스가 하나도 없을 땐(온보딩 화면) 상단 네비바가 없어서 접힌
  // 사이드바를 다시 펼 방법이 없다 — 이 경우엔 접기를 막고 항상 펼쳐 둔다.
  const canCollapse = workspaces?.length > 0;
  const effectiveCollapsed = collapsed && canCollapse;

  return (
    <aside className={`sidebar ${effectiveCollapsed ? "sidebar-collapsed" : ""}`}>
      {!effectiveCollapsed && canCollapse && (
        <button
          type="button"
          className="sidebar-collapse-toggle"
          onClick={() => setCollapsed(true)}
          aria-label="사이드바 접기"
          title="사이드바 접기"
        >
          <PanelCollapseIcon />
        </button>
      )}

      <div className="sidebar-brand">
        <img src={logoUrl} alt="NeoSDL" className="sidebar-brand-logo" />
      </div>

      <div className="team-switcher">
        {workspaces === null ? (
          <span className="muted">워크스페이스 불러오는 중...</span>
        ) : (
          <select
            className="team-switcher-select"
            value={activeWorkspaceId || ""}
            onChange={(e) => setActiveWorkspaceId(e.target.value)}
          >
            {workspaces.length === 0 && <option value="">워크스페이스 없음</option>}
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        )}
        <button type="button" className="team-switcher-new-btn" onClick={() => setShowCreateWorkspace(true)}>
          + 새 워크스페이스
        </button>
      </div>

      <div className="sidebar-scroll">
        {recent.length > 0 && (
          <div className="sidebar-section">
            <div className="sidebar-section-label">최근 항목</div>
            {recent.map((item) => (
              <button key={item.id} type="button" className="sidebar-project-link" onClick={() => openRecent(item)}>
                {item.name}
              </button>
            ))}
          </div>
        )}

        {activeWorkspaceId && (
          <div className="sidebar-section">
            <div className="sidebar-section-label">
              <span>팀스페이스</span>
              <button
                type="button"
                className="team-switcher-new-btn"
                onClick={() => setShowCreateTeamspace(true)}
                title="새 팀스페이스"
              >
                + 새 팀스페이스
              </button>
            </div>
            {teamspaces === null ? (
              <span className="muted">불러오는 중...</span>
            ) : teamspaces.length === 0 ? (
              <span className="muted">팀스페이스가 없습니다.</span>
            ) : (
              teamspaces.map((t) => (
                <TeamspaceNode
                  key={t.id}
                  teamspace={t}
                  projects={projectsByTeamspace[t.id] || []}
                  activeProjectId={activeProjectId}
                  onOpenProject={openProject}
                  onCreateProject={setCreateProjectIn}
                  onJoin={async (ts) => {
                    await joinTeamspace(ts.id);
                    await reloadTeamspaces();
                  }}
                  onMoveProject={moveProjectToTeamspace}
                  onManage={setManageTeamspace}
                  onQuickMenu={setQuickMenuProject}
                />
              ))
            )}
            {dragError && <div className="alert-error sidebar-drag-error">{dragError}</div>}
          </div>
        )}
      </div>

      <nav className="sidebar-nav">
        <NavLink to="/workspace" className={({ isActive }) => (isActive ? "active" : "")}>
          워크스페이스 홈
        </NavLink>
        {activeWorkspaceId && (
          <button type="button" className="sidebar-nav-btn" onClick={() => setShowWorkspaceSettings(true)}>
            워크스페이스 관리
          </button>
        )}
      </nav>
      {me && (
        <div className="sidebar-account">
          {me.avatar_url && <img src={me.avatar_url} alt="" />}
          <span className="login">{me.nickname}</span>
          <button onClick={handleLogout}>로그아웃</button>
        </div>
      )}

      {showCreateWorkspace && (
        <CreateWorkspaceModal
          onCreated={async (workspace) => {
            await reloadWorkspaces();
            setActiveWorkspaceId(workspace.id);
          }}
          onClose={() => setShowCreateWorkspace(false)}
        />
      )}

      {showCreateTeamspace && (
        <CreateTeamspaceModal
          workspaceId={activeWorkspaceId}
          onCreated={() => reloadTeamspaces()}
          onClose={() => setShowCreateTeamspace(false)}
        />
      )}

      {createProjectIn && (
        <CreateProjectModal
          teamspaceId={createProjectIn.id}
          onCreated={reloadProjects}
          onClose={() => setCreateProjectIn(null)}
        />
      )}

      {showWorkspaceSettings && (
        <WorkspaceSettingsModal onClose={() => setShowWorkspaceSettings(false)} />
      )}

      {manageTeamspace && (
        <TeamspaceSettingsModal teamspaceId={manageTeamspace.id} onClose={() => setManageTeamspace(null)} />
      )}

      {quickMenuProject && (
        <ProjectQuickMenuModal
          project={quickMenuProject}
          onOpenDetail={openDetailTab}
          onClose={() => setQuickMenuProject(null)}
        />
      )}
    </aside>
  );
}
