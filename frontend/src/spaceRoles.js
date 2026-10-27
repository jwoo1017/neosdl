// 워크스페이스·팀스페이스 공통 역할 정책 — 백엔드 app/auth.py assert_can_manage_member 와 같은 규칙.
//  owner  : 공간 삭제, admin·owner 강등/제거, owner 지정. 최소 1명 유지.
//  admin  : 설정·초대·member 관리(member→admin 승격까지).
//  member : 이용만.
export const SPACE_ROLE_RANK = { member: 0, admin: 1, owner: 2 };
export const SPACE_ROLE_LABEL = { owner: "소유자", admin: "관리자", member: "멤버" };

export function isSpaceAdmin(role) {
  return role === "admin" || role === "owner";
}

// actor 가 target 의 역할을 바꾸거나 제거할 수 있는가(본인은 '나가기'로 따로 처리).
export function canManageMember(actorRole, targetRole) {
  if (actorRole === "owner") return true;
  return actorRole === "admin" && targetRole === "member";
}

// actor 가 고를 수 있는 역할 목록.
export function assignableRoles(actorRole) {
  return actorRole === "owner" ? ["owner", "admin", "member"] : ["admin", "member"];
}

const ERROR_MESSAGES = {
  last_owner: "마지막 소유자는 나가거나 강등될 수 없습니다. 다른 멤버를 소유자로 지정하거나 공간을 삭제하세요.",
  owner_required: "관리자·소유자의 역할 변경과 제거는 소유자만 할 수 있습니다.",
  use_leave_endpoint: "본인은 제거할 수 없습니다. '나가기'를 사용하세요.",
  "insufficient workspace role": "권한이 없습니다.",
  "insufficient teamspace role": "권한이 없습니다.",
};

export function describeSpaceError(err) {
  return ERROR_MESSAGES[err?.detail] || err?.message || "요청을 처리하지 못했습니다.";
}
