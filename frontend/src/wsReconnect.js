// WebSocket 재연결 정책(채팅 ProjectChatPage, 마스코트 mascotSocket 공용).
//  - 지수 백오프 1s → 2s → 4s … 최대 30s, ±30% 지터: 서버 재시작 직후 모든 탭이
//    같은 순간에 몰려드는 것(thundering herd)을 막는다. 연결에 성공하면 처음부터 다시.
//  - 서버가 이유 코드로 닫으면 재시도하지 않는다(app/chat.py, app/repo_activity.py):
//      4401 = 로그인 만료/세션 무효화 → 로그인 화면으로(HTTP 401 처리와 같게)
//      4403 = 권한 없음/허용되지 않은 Origin → 다시 해도 똑같으니 멈춤

export const WS_CLOSE_UNAUTHORIZED = 4401;
export const WS_CLOSE_FORBIDDEN = 4403;

export function createBackoff({ baseMs = 1000, maxMs = 30000, jitter = 0.3 } = {}) {
  let attempt = 0;
  return {
    next() {
      const raw = Math.min(maxMs, baseMs * 2 ** attempt);
      attempt += 1;
      const spread = raw * jitter * (Math.random() * 2 - 1);
      return Math.max(250, Math.round(raw + spread));
    },
    reset() {
      attempt = 0;
    },
  };
}

// 재시도하면 안 되는 종료면 true. 4401 이면 로그인 화면으로 보낸다.
export function isTerminalClose(code) {
  if (code === WS_CLOSE_UNAUTHORIZED) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    return true;
  }
  return code === WS_CLOSE_FORBIDDEN;
}
