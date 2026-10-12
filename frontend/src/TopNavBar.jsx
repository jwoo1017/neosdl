import { useWorkspace } from "./WorkspaceContext";
import { useTeamspace } from "./TeamspaceContext";
import { useProject } from "./ProjectContext";
import { useSidebar } from "./SidebarContext";
import InviteInbox from "./InviteInbox";
import HamburgerIcon from "./icons/HamburgerIcon";

export default function TopNavBar() {
  const { activeWorkspace } = useWorkspace();
  const { teamspaces } = useTeamspace();
  const { activeProject } = useProject();
  const { collapsed, setCollapsed } = useSidebar();

  const activeTeamspace = (teamspaces || []).find((t) => t.id === activeProject?.teamspace_id) || null;

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
      <InviteInbox />
    </header>
  );
}
