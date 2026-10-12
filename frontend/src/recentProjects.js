// "최근 항목" — 서버에 접근 이력을 남기는 대신, 이 브라우저에서 최근 연
// project만 기억하는 가벼운 클라이언트 전용 MRU. 여러 기기/브라우저 간
// 동기화는 안 되지만, 사이드바 내비게이션 용도로는 이 정도로 충분하다.
const KEY = "neosdl_recent_projects";
const MAX = 5;

export function getRecentProjects() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function pushRecentProject(project) {
  try {
    const list = getRecentProjects().filter((p) => p.id !== project.id);
    list.unshift({
      id: project.id,
      name: project.name,
      workspaceId: project.workspaceId,
      teamspaceId: project.teamspaceId,
    });
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX)));
  } catch {
    // localStorage가 막혀 있어도(프라이빗 모드 등) 내비게이션 자체는 계속돼야 한다.
  }
}

export function removeRecentProject(projectId) {
  try {
    const list = getRecentProjects().filter((p) => p.id !== projectId);
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // 조용히 무시 — 다음에 pushRecentProject가 성공하면 어차피 다시 정리된다.
  }
}

// teamspace를 삭제하면 그 안의 project도 함께 사라지므로(서버가 cascade
// soft-delete), 거기 있던 project를 가리키던 최근 항목도 같이 지워야
// 클릭했을 때 404로 이어지는 죽은 링크가 안 남는다.
export function removeRecentProjectsByTeamspace(teamspaceId) {
  try {
    const list = getRecentProjects().filter((p) => p.teamspaceId !== teamspaceId);
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // 조용히 무시 — 다음에 pushRecentProject가 성공하면 어차피 다시 정리된다.
  }
}

// workspace 삭제는 그 안의 teamspace/project를 전부 cascade soft-delete하므로
// (workspace.delete_workspace), 같은 이유로 최근 항목도 통째로 지워야 한다.
export function removeRecentProjectsByWorkspace(workspaceId) {
  try {
    const list = getRecentProjects().filter((p) => p.workspaceId !== workspaceId);
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // 조용히 무시 — 다음에 pushRecentProject가 성공하면 어차피 다시 정리된다.
  }
}

// 위 removeRecentProjectsBy*는 "이 브라우저에서 직접 삭제/탈퇴한" 경우에만
// 불린다 — 다른 기기에서 워크스페이스가 지워지거나 관리자가 멤버를
// 내보낸 경우엔 아무도 이 브라우저의 localStorage를 정리해주지 않는다.
// 그래서 서버가 내려준 최신 워크스페이스 목록(현재 멤버인 워크스페이스만
// 포함)을 받을 때마다 거기 없는 workspaceId를 가리키는 최근 항목은 통째로
// 걸러낸다.
export function pruneRecentProjectsByValidWorkspaces(validWorkspaceIds) {
  try {
    const validSet = new Set(validWorkspaceIds);
    const list = getRecentProjects().filter((p) => !p.workspaceId || validSet.has(p.workspaceId));
    localStorage.setItem(KEY, JSON.stringify(list));
    return list;
  } catch {
    return getRecentProjects();
  }
}
