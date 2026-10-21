import { useEffect, useRef } from "react";
import { repoActivitySocketUrl } from "../api";
import { createBackoff, isTerminalClose } from "../wsReconnect";

// 저장소당 소켓 1개(app/repo_activity.py는 저장소 단위 엔드포인트라 여러
// 저장소를 동시에 볼 때 소켓도 그만큼 열린다 — ProjectChatPage.jsx의
// 재연결/정리 패턴을 그대로 복제하되, N개를 각자 독립적으로 관리한다).
export function useRepoActivitySockets(repoIds, onEvent) {
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  }, [onEvent]);

  const socketsRef = useRef(new Map()); // repoId -> { ws, reconnectTimer, closed, backoff }
  const key = repoIds.join("|");

  useEffect(() => {
    const sockets = socketsRef.current;
    const wanted = new Set(repoIds);

    for (const [repoId, entry] of sockets) {
      if (!wanted.has(repoId)) {
        entry.closed = true;
        clearTimeout(entry.reconnectTimer);
        entry.ws?.close();
        sockets.delete(repoId);
      }
    }

    function connect(repoId, entry) {
      if (entry.closed) return;
      entry.reconnectTimer = null;
      const ws = new WebSocket(repoActivitySocketUrl(repoId));
      entry.ws = ws;
      ws.onopen = () => entry.backoff.reset();
      ws.onmessage = (msg) => {
        try {
          onEventRef.current?.(repoId, JSON.parse(msg.data));
        } catch {
          // 파싱 실패한 프레임은 무시
        }
      };
      ws.onerror = () => ws.close();
      ws.onclose = (evt) => {
        if (entry.closed) return;
        if (isTerminalClose(evt.code)) {
          entry.closed = true; // 4401/4403 — 이 저장소 소켓은 더 시도하지 않는다
          return;
        }
        entry.reconnectTimer = setTimeout(() => connect(repoId, entry), entry.backoff.next());
      };
    }

    for (const repoId of wanted) {
      if (sockets.has(repoId)) continue;
      const entry = { ws: null, reconnectTimer: null, closed: false, backoff: createBackoff() };
      sockets.set(repoId, entry);
      connect(repoId, entry);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => {
    const sockets = socketsRef.current;
    return () => {
      for (const entry of sockets.values()) {
        entry.closed = true;
        clearTimeout(entry.reconnectTimer);
        entry.ws?.close();
      }
      sockets.clear();
    };
  }, []);
}
