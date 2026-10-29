import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { fetchProjectStatuses, joinTeamspace, logout, moveProject } from "./api";
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
import PopoverMenu from "./components/PopoverMenu";
import SessionStatusDot, { isActiveSessionStatus, sessionDotStatus } from "./components/SessionStatusDot";
import PlusIcon from "./icons/PlusIcon";
import MoreIcon from "./icons/MoreIcon";
import PanelCollapseIcon from "./icons/PanelCollapseIcon";
import PersonalSpaceIcon from "./icons/PersonalSpaceIcon";
import { ChevronRightIcon, GearIcon, TeamspaceIcon } from "./icons/SpaceIcons";
import Avatar from "./components/Avatar";
import Select from "./components/Select";
import { useMe } from "./MeContext";
import { useDialog } from "./dialog/DialogContext";
import logoUrl from "./assets/logo.svg?url";

const VISIBILITY_BADGE = { open: "공개", closed: "참가 제한", private: "비공개" };

function TeamspaceNode({ teamspace, projects, activeProjectId, statusByProject, onOpenProject, onCreateProject, onJoin, onMoveProject, onManage, onQuickMenu }) {
  const [expanded, setExpanded] = useState(true);
  const [joining, setJoining] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const isMember = teamspace.my_role != null;
  const personal = !!teamspace.personal; // 개인 페이지 — 관리/가입 UI 없음

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
      {/* 팀스페이스와 개인 페이지가 같은 행 구조를 쓴다: 화살표 · 아이콘 · 이름 · (hover) 동작 */}
      <div className="sidebar-teamspace-header">
        <button type="button" className="sidebar-teamspace-toggle" onClick={() => setExpanded((v) => !v)}
                aria-expanded={expanded}>
          <ChevronRightIcon size={12} className={`sidebar-teamspace-caret${expanded ? " is-open" : ""}`} />
          <span className="sidebar-teamspace-icon">
            {personal ? <PersonalSpaceIcon size={15} /> : <TeamspaceIcon size={15} />}
          </span>
          <span className="sidebar-teamspace-name">{personal ? "내 프로젝트" : teamspace.name}</span>
          {!personal && teamspace.visibility !== "open" && (
            <span className="badge sidebar-teamspace-badge">{VISIBILITY_BADGE[teamspace.visibility]}</span>
          )}
        </button>
        <span className="sidebar-teamspace-actions">
          {isMember && (
            <button type="button" className="icon-btn" onClick={() => onCreateProject(teamspace)} aria-label="새 프로젝트">
              <PlusIcon size={14} />
            </button>
          )}
          {!personal && (
            <button type="button" className="icon-btn" onClick={() => onManage(teamspace)} aria-label="팀스페이스 관리">
              <GearIcon size={14} />
            </button>
          )}
        </span>
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
                {statusByProject?.[p.id] && (
                  <SessionStatusDot status={sessionDotStatus({ engagement_status: statusByProject[p.id] })} />
                )}
                <span className="sidebar-project-name">{p.name}</span>
              </button>
              <button
                type="button"
                className="icon-btn sidebar-project-menu-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  onQuickMenu({ project: p, teamspace, anchor: e.currentTarget.getBoundingClientRect() });
                }}
                aria-label="프로젝트 메뉴"
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

export default function Sidebar() {
  const navigate = useNavigate();
  const { me } = useMe();
  const { workspaces, activeWorkspaceId, setActiveWorkspaceId, reload: reloadWorkspaces } = useWorkspace();
  const { teamspaces, personalSpace, reload: reloadTeamspaces } = useTeamspace();
  const { projects, activeProjectId, setActiveProjectId, reload: reloadProjects } = useProject();
  const { collapsed, setCollapsed } = useSidebar();
  const { openChatTab, openDetailTab, openWelcomeTab } = useTabs();
  const dialog = useDialog();

  const [showCreateWorkspace, setShowCreateWorkspace] = useState(false);
  const [showCreateTeamspace, setShowCreateTeamspace] = useState(false);
  const [createProjectIn, setCreateProjectIn] = useState(null); // teamspace 객체 또는 null
  const [showWorkspaceSettings, setShowWorkspaceSettings] = useState(false);
  const [manageTeamspace, setManageTeamspace] = useState(null); // teamspace 객체 또는 null
  const [quickMenu, setQuickMenu] = useState(null); // { project, teamspace, anchor } 또는 null
  const closeQuickMenu = useCallback(() => setQuickMenu(null), []);
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

  // 팀스페이스가 하나도 없으면 사이드바에 빈 문구 대신 NeoSDL 시작 탭을 띄운다.
  // 워크스페이스마다 이 브라우저 세션에서 한 번만 — 닫으면 다시 강제로 열지 않는다.
  // 마이페이지·회원 관리처럼 다른 화면을 보고 있을 땐 끼어들지 않는다.
  useEffect(() => {
    if (!activeWorkspaceId || teamspaces === null || teamspaces.length > 0) return;
    if (!["/", "/workspace", "/panes"].includes(location.pathname)) return;
    const key = `neosdl_welcome_shown_${activeWorkspaceId}`;
    try {
      if (sessionStorage.getItem(key)) return;
      sessionStorage.setItem(key, "1");
    } catch {
      /* 저장소 차단 — 그래도 한 번 연다 */
    }
    openWelcomeTab();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkspaceId, teamspaces]);

  // 프로젝트 옆 보안 점검 상태 점 — 점검은 워커에서 돌아 알림이 없으므로 폴링한다.
  // 진행/대기 중인 프로젝트가 있으면 5초, 없으면 30초(새로 시작한 점검 감지용).
  const [statusByProject, setStatusByProject] = useState({});
  const hasActiveCheck = Object.values(statusByProject).some((st) =>
    isActiveSessionStatus(sessionDotStatus({ engagement_status: st })));
  useEffect(() => {
    if (!activeWorkspaceId) {
      setStatusByProject({});
      return undefined;
    }
    let cancelled = false;
    const load = () =>
      fetchProjectStatuses(activeWorkspaceId)
        .then((rows) => {
          if (cancelled) return;
          const next = Object.fromEntries(rows.map((r) => [r.project_id, r.engagement_status]));
          setStatusByProject((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
        })
        .catch(() => {});
    load();
    const timer = setInterval(load, hasActiveCheck ? 5000 : 30000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [activeWorkspaceId, hasActiveCheck]);

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

  // "⋯" 메뉴 — 모달 없이 바로 원하는 화면(상세 하위 경로)으로 연다.
  const openProjectDetail = (project, subPath) => {
    setActiveProjectId(project.id);
    openDetailTab(project, subPath);
  };

  const quickMenuItems = (project) => [
    { key: "chat", label: "채팅 열기", onSelect: () => openProject(project) },
    { key: "overview", label: "상세 보기", hint: "레포지토리 · 스캔 설정", onSelect: () => openProjectDetail(project, "overview") },
    { key: "graph", label: "코드 지도", hint: "함수·모듈 연결 관계 그래프", onSelect: () => openProjectDetail(project, "graph") },
    { key: "security", label: "보안 분석", hint: "스캔 결과 · 위험", onSelect: () => openProjectDetail(project, "security/scan") },
  ];

  const openRecent = (item) => {
    if (item.workspaceId && item.workspaceId !== activeWorkspaceId) setActiveWorkspaceId(item.workspaceId);
    setActiveProjectId(item.id);
    openChatTab(item);
  };

  const moveProjectToTeamspace = async (projectId, teamspace) => {
    const current = (projects || []).find((p) => p.id === projectId);
    if (current?.teamspace_id === teamspace.id) return; // 같은 teamspace로 드롭 — 할 일 없음
    const fromPersonal = personalSpace && current?.teamspace_id === personalSpace.id;
    // 개인 페이지 ↔ 팀스페이스 이동은 "공개 범위" 가 바뀌므로 한 번 확인받는다.
    if (fromPersonal && !teamspace.personal && !(await dialog.confirm({
      title: "팀스페이스로 옮기기",
      message: `'${current.name}'을(를) '${teamspace.name}' 팀스페이스로 옮겨 팀원과 공유합니다. 계속할까요?`,
      confirmLabel: "옮기기",
    }))) {
      return;
    }
    if (teamspace.personal && !fromPersonal && !(await dialog.confirm({
      title: "개인 페이지로 옮기기",
      message: `'${current?.name}'을(를) 개인 페이지로 옮기면 팀원에게 더 이상 보이지 않습니다. 계속할까요?`,
      confirmLabel: "옮기기",
    }))) {
      return;
    }
    setDragError(null);
    try {
      await moveProject(projectId, teamspace.id);
      await reloadProjects();
    } catch (err) {
      setDragError(err.message === "cannot_make_shared_project_personal"
        ? "다른 멤버가 참여 중인 프로젝트는 개인 페이지로 옮길 수 없습니다."
        : err.message);
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
          <Select
            className="team-switcher-select"
            ariaLabel="워크스페이스 선택"
            value={activeWorkspaceId || ""}
            onChange={setActiveWorkspaceId}
            placeholder="워크스페이스 없음"
            disabled={workspaces.length === 0}
            options={workspaces.map((w) => ({ value: w.id, label: w.name }))}
          />
        )}
        {/* 게스트(회사 도메인 밖, 초대로 가입)는 워크스페이스를 만들 수 없다(app/workspaces.py) */}
        {!me?.is_guest && (
          <button type="button" className="team-switcher-new-btn" onClick={() => setShowCreateWorkspace(true)}>
            + 새 워크스페이스
          </button>
        )}
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
              >
                + 새 팀스페이스
              </button>
            </div>
            {teamspaces === null ? (
              <span className="sidebar-project-empty">불러오는 중...</span>
            ) : (
              teamspaces.map((t) => (
                <TeamspaceNode
                  key={t.id}
                  teamspace={t}
                  projects={projectsByTeamspace[t.id] || []}
                  activeProjectId={activeProjectId}
                  statusByProject={statusByProject}
                  onOpenProject={openProject}
                  onCreateProject={setCreateProjectIn}
                  onJoin={async (ts) => {
                    await joinTeamspace(ts.id);
                    await reloadTeamspaces();
                  }}
                  onMoveProject={moveProjectToTeamspace}
                  onManage={setManageTeamspace}
                  onQuickMenu={setQuickMenu}
                />
              ))
            )}
            {dragError && <div className="alert-error sidebar-drag-error">{dragError}</div>}
          </div>
        )}

        {/* 개인 페이지 — 나만 보는 공간. 프로젝트를 위 팀스페이스로 끌어다 놓으면 공유된다. */}
        {activeWorkspaceId && personalSpace && (
          <div className="sidebar-section">
            <div className="sidebar-section-label">
              <span>개인 페이지</span>
            </div>
            <TeamspaceNode
              teamspace={personalSpace}
              projects={projectsByTeamspace[personalSpace.id] || []}
              activeProjectId={activeProjectId}
              statusByProject={statusByProject}
              onOpenProject={openProject}
              onCreateProject={setCreateProjectIn}
              onJoin={async () => {}}
              onMoveProject={moveProjectToTeamspace}
              onManage={() => {}}
              onQuickMenu={setQuickMenu}
            />
            <p className="sidebar-personal-hint">나만 볼 수 있어요. 팀스페이스로 끌어다 놓으면 공유됩니다.</p>
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
        {me?.is_system_admin && (
          <NavLink to="/admin/members" className={({ isActive }) => (isActive ? "active" : "")}>
            회원 관리
          </NavLink>
        )}
      </nav>
      {me && (
        <div className="sidebar-account">
          {/* 프로필을 누르면 마이페이지 — 프로필·계정 설정 */}
          <button
            type="button"
            className={`sidebar-account-profile ${location.pathname === "/me" ? "active" : ""}`}
            onClick={() => navigate("/me")}
            aria-label="마이페이지"
          >
            <Avatar profile={me} size={30} />
            <span className="sidebar-account-names">
              <span className="login">{me.nickname}</span>
              <span className="sidebar-account-realname">{me.name}</span>
            </span>
          </button>
          <button type="button" className="sidebar-account-logout" onClick={handleLogout}>로그아웃</button>
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

      {quickMenu && (
        <PopoverMenu
          anchor={quickMenu.anchor}
          ariaLabel={`${quickMenu.project.name} 메뉴`}
          items={quickMenuItems(quickMenu.project)}
          onClose={closeQuickMenu}
        />
      )}
    </aside>
  );
}
