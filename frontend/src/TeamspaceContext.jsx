import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchWorkspaceTeamspaces } from "./api";
import { useWorkspace } from "./WorkspaceContext";

const TeamspaceContext = createContext(null);

export function TeamspaceProvider({ children }) {
  const { activeWorkspaceId } = useWorkspace();
  const [teamspaces, setTeamspaces] = useState(null); // null = 로딩 중, [] = 없음

  const reload = useCallback(() => {
    if (!activeWorkspaceId) {
      setTeamspaces([]);
      return Promise.resolve([]);
    }
    return fetchWorkspaceTeamspaces(activeWorkspaceId).then((list) => {
      setTeamspaces(list);
      return list;
    });
  }, [activeWorkspaceId]);

  useEffect(() => {
    setTeamspaces(null);
    reload().catch(() => setTeamspaces([]));
  }, [activeWorkspaceId, reload]);

  const value = useMemo(() => ({ teamspaces, reload }), [teamspaces, reload]);

  return <TeamspaceContext.Provider value={value}>{children}</TeamspaceContext.Provider>;
}

export function useTeamspace() {
  const ctx = useContext(TeamspaceContext);
  if (!ctx) throw new Error("useTeamspace must be used within a TeamspaceProvider");
  return ctx;
}
