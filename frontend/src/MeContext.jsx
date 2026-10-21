import { createContext, useContext } from "react";

// 로그인한 내 정보(GET /api/me) — 사이드바 하단 프로필, 마이페이지, 채팅의 "내 메시지" 구분,
// 상단 접속 표시가 같이 쓴다. 마이페이지에서 바꾸면 setMe 로 즉시 전체에 반영한다.
export const MeContext = createContext({ me: null, setMe: () => {} });

export function useMe() {
  return useContext(MeContext);
}
