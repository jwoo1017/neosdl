import { useWorkspace } from "./WorkspaceContext";
import { useTeamspace } from "./TeamspaceContext";
import { useProject } from "./ProjectContext";
import { useSidebar } from "./SidebarContext";
import InviteInbox from "./InviteInbox";
import PresenceStack from "./components/PresenceStack";
import HamburgerIcon from "./icons/HamburgerIcon";

export default function TopNavBar() {
  const { activeWorkspace } = useWorkspace();
  const { teamspaces, personalSpace } = useTeamspace();
  const { activeProject } = useProject();
  const { collapsed, setCollapsed } = useSidebar();

  const activeTeamspace = [...(teamspaces || []), ...(personalSpace ? [personalSpace] : [])]
    .find((t) => t.id === activeProject?.teamspace_id) || null;

  const crumbs = [activeWorkspace?.name, activeTeamspace?.name, activeProject?.name].filter(Boolean);

  return (
    <header className="top-navbar">
      <div className="top-navbar-left">
        {collapsed && (
          <button
            type="button"
            className="navbar-expand-toggle"
            onClick={() => setCollapsed(false)}
            aria-label="사이드바 펼치기"
            title="사이드바 펼치기"
          >
            <HamburgerIcon />
          </button>
        )}
        <nav className="breadcrumb" aria-label="현재 위치">
          {crumbs.length === 0 ? (
            <span className="muted">NeoSDL</span>
          ) : (
            crumbs.map((crumb, i) => (
              <span key={i} className="breadcrumb-segment">
                {i > 0 && <span className="breadcrumb-sep">/</span>}
                {crumb}
              </span>
            ))
          )}
        </nav>
      </div>
      <div className="top-navbar-right">
        {/* 지금 이 팀스페이스에서 활동 중인 멤버 — 우편함 왼쪽 */}
        {/* 개인 페이지는 나 혼자라 표시하지 않는다 */}
        <PresenceStack teamspaceId={activeTeamspace && !activeTeamspace.personal ? activeTeamspace.id : null} />
        <InviteInbox />
      </div>
    </header>
  );
}
