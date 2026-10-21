import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchPersonalSpace, fetchWorkspaceTeamspaces } from "./api";
import { useWorkspace } from "./WorkspaceContext";

const TeamspaceContext = createContext(null);

// teamspaces: 일반 팀스페이스 목록(개인 페이지 제외)
// personalSpace: 이 워크스페이스에서의 내 개인 페이지(나만 보는 팀스페이스, sql/031)
export function TeamspaceProvider({ children }) {
  const { activeWorkspaceId } = useWorkspace();
  const [teamspaces, setTeamspaces] = useState(null); // null = 로딩 중, [] = 없음
  const [personalSpace, setPersonalSpace] = useState(null);

  const reload = useCallback(() => {
    if (!activeWorkspaceId) {
      setTeamspaces([]);
      setPersonalSpace(null);
      return Promise.resolve([]);
    }
    // 개인 페이지 조회가 실패해도 팀스페이스 목록은 보여야 한다(마이그레이션 전 서버 등).
    fetchPersonalSpace(activeWorkspaceId).then(setPersonalSpace).catch(() => setPersonalSpace(null));
    return fetchWorkspaceTeamspaces(activeWorkspaceId).then((list) => {
      setTeamspaces(list);
      return list;
    });
  }, [activeWorkspaceId]);

  useEffect(() => {
    setTeamspaces(null);
    reload().catch(() => setTeamspaces([]));
  }, [activeWorkspaceId, reload]);

  const value = useMemo(() => ({ teamspaces, personalSpace, reload }), [teamspaces, personalSpace, reload]);

  return <TeamspaceContext.Provider value={value}>{children}</TeamspaceContext.Provider>;
}

export function useTeamspace() {
  const ctx = useContext(TeamspaceContext);
  if (!ctx) throw new Error("useTeamspace must be used within a TeamspaceProvider");
  return ctx;
}
