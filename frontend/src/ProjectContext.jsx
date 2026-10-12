import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchProjects } from "./api";
import { useWorkspace } from "./WorkspaceContext";

const ProjectContext = createContext(null);

const ACTIVE_PROJECT_KEY_PREFIX = "neosdl_active_project_id_";
const PROJECT_ROLE_RANK = { viewer: 0, developer: 1, manager: 2, owner: 3 };

// 초대 수락 페이지처럼 아직 활성화되지 않은 workspace로 전환하면서 특정
// project를 곧바로 활성화하고 싶을 때, ProjectProvider의 자동 로드가 읽는
// 것과 같은 키에 미리 써 둘 수 있게 내보낸다.
export function activeProjectStorageKey(workspaceId) {
  return ACTIVE_PROJECT_KEY_PREFIX + workspaceId;
}

export function ProjectProvider({ children }) {
  const { activeWorkspaceId } = useWorkspace();
  const [projects, setProjects] = useState(null); // null = 로딩 중, [] = 프로젝트 없음
  const [activeProjectId, setActiveProjectId] = useState(null);

  const reload = useCallback(() => {
    if (!activeWorkspaceId) {
      setProjects([]);
      return Promise.resolve([]);
    }
    return fetchProjects(activeWorkspaceId).then((list) => {
      setProjects(list);
      const saved = localStorage.getItem(ACTIVE_PROJECT_KEY_PREFIX + activeWorkspaceId);
      setActiveProjectId(list.find((p) => p.id === saved)?.id ?? list[0]?.id ?? null);
      return list;
    });
  }, [activeWorkspaceId]);

  useEffect(() => {
    setProjects(null);
    setActiveProjectId(null);
    reload().catch(() => setProjects([]));
  }, [activeWorkspaceId, reload]);

  useEffect(() => {
    if (activeWorkspaceId && activeProjectId) {
      localStorage.setItem(ACTIVE_PROJECT_KEY_PREFIX + activeWorkspaceId, activeProjectId);
    }
  }, [activeWorkspaceId, activeProjectId]);

  const activeProject = useMemo(
    () => (projects || []).find((p) => p.id === activeProjectId) || null,
    [projects, activeProjectId]
  );

  // activeProject.role은 같은 workspace 소속이면 최소 viewer로 채워져 온다
  // (백엔드 [[_effective_project_role]]) — project_member로 명시 초대된
  // 적 없어도 목록엔 항상 role이 있다는 뜻이라, viewer보다 높은 역할만
  // 여기서 게이트하면 된다.
  const hasProjectRole = useCallback(
    (minRole) =>
      !!activeProject?.role && PROJECT_ROLE_RANK[activeProject.role] >= PROJECT_ROLE_RANK[minRole],
    [activeProject]
  );

  const value = useMemo(
    () => ({
      projects,
      activeProject,
      activeProjectId,
      setActiveProjectId,
      reload,
      hasProjectRole,
    }),
    [projects, activeProject, activeProjectId, reload, hasProjectRole]
  );

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
}

export function useProject() {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error("useProject must be used within a ProjectProvider");
  return ctx;
}
