import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchWorkspaces } from "./api";

const WorkspaceContext = createContext(null);

const ACTIVE_WORKSPACE_KEY = "neosdl_active_workspace_id";
const WORKSPACE_ROLE_RANK = { member: 0, admin: 1 };

export function WorkspaceProvider({ children }) {
  const [workspaces, setWorkspaces] = useState(null); // null = 로딩 중, [] = 워크스페이스 없음
  const [activeWorkspaceId, setActiveWorkspaceId] = useState(
    () => localStorage.getItem(ACTIVE_WORKSPACE_KEY) || null
  );

  const reload = useCallback(() => {
    return fetchWorkspaces().then((list) => {
      setWorkspaces(list);
      setActiveWorkspaceId((current) => {
        if (current && list.some((w) => w.id === current)) return current;
        return list[0]?.id ?? null;
      });
      return list;
    });
  }, []);

  useEffect(() => {
    reload().catch(() => setWorkspaces([]));
  }, [reload]);

  useEffect(() => {
    if (activeWorkspaceId) localStorage.setItem(ACTIVE_WORKSPACE_KEY, activeWorkspaceId);
  }, [activeWorkspaceId]);

  const activeWorkspace = useMemo(
    () => (workspaces || []).find((w) => w.id === activeWorkspaceId) || null,
    [workspaces, activeWorkspaceId]
  );

  const hasWorkspaceRole = useCallback(
    (minRole) =>
      !!activeWorkspace && WORKSPACE_ROLE_RANK[activeWorkspace.role] >= WORKSPACE_ROLE_RANK[minRole],
    [activeWorkspace]
  );

  const value = useMemo(
    () => ({
      workspaces,
      activeWorkspace,
      activeWorkspaceId,
      setActiveWorkspaceId,
      reload,
      isWorkspaceAdmin: activeWorkspace?.role === "admin",
      hasWorkspaceRole,
    }),
    [workspaces, activeWorkspace, activeWorkspaceId, reload, hasWorkspaceRole]
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace() {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used within a WorkspaceProvider");
  return ctx;
}
