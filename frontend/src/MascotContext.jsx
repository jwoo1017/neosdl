import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { useRepoActivitySockets } from "./mascot/mascotSocket";

const MascotContext = createContext(null);

const HIGH_URGENCY_TYPES = new Set(["fix_ready", "pr_created"]);
const MAX_EVENTS_PER_REPO = 50;

const BUBBLE_LABELS = {
  push_received: "새 커밋이 푸시됐어요",
  scan_started: "코드 분석을 시작할게요",
  scan_completed: (e) => (e.findings_count > 0 ? `취약점 ${e.findings_count}건을 발견했어요` : "분석 완료 — 취약점 없음"),
  fix_ready: "수정 코드 제안이 준비됐어요",
  pr_created: "수정 PR을 만들었어요",
};

function bubbleMessage(event) {
  const label = BUBBLE_LABELS[event.type];
  return typeof label === "function" ? label(event) : label || null;
}

// 알림/뱃지/사이드바 상태는 특정 pane의 생명주기를 벗어나 유지돼야 해서
// ProjectContext/TabsContext와 별개의 작은 Context로 둔다(이 프로젝트 전반의
// "Context 여러 개, 각자 plain useState" 패턴을 그대로 따른다 — Redux/Zustand 없음).
export function MascotProvider({ children }) {
  const [watchedRepos, setWatchedRepos] = useState({}); // { [repoId]: { projectId, refCount } }
  const [repoLabels, setRepoLabels] = useState({}); // { [repoId]: "namespace/name" } — 패널의 저장소 스위처 표시용
  const [activeRepoId, setActiveRepoId] = useState(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [unreadByRepo, setUnreadByRepo] = useState({});
  const [eventsByRepo, setEventsByRepo] = useState({});
  const [scanningRepos, setScanningRepos] = useState({}); // repoId -> true 인 동안 "생각 중" 표정
  const [bubble, setBubble] = useState(null); // { repoId, message } | null

  // WS 메시지 핸들러(handleEvent)가 매 렌더 새로 만들어져도 mascotSocket의
  // 소켓 자체는 repoId 목록이 바뀔 때만 재연결된다(onEventRef로 최신 콜백만 갱신) —
  // 여기서는 "지금 보고 있는 저장소/패널"을 stale closure 없이 읽으려고 ref를 둔다.
  const panelOpenRef = useRef(panelOpen);
  panelOpenRef.current = panelOpen;
  const activeRepoIdRef = useRef(activeRepoId);
  activeRepoIdRef.current = activeRepoId;

  const trackRepoView = useCallback((projectId, repoId) => {
    if (!repoId) return;
    setActiveRepoId(repoId);
    setWatchedRepos((prev) => {
      const existing = prev[repoId];
      return { ...prev, [repoId]: { projectId, refCount: (existing?.refCount || 0) + 1 } };
    });
  }, []);

  const untrackRepoView = useCallback((repoId) => {
    if (!repoId) return;
    setWatchedRepos((prev) => {
      const existing = prev[repoId];
      if (!existing) return prev;
      if (existing.refCount <= 1) {
        const next = { ...prev };
        delete next[repoId];
        return next;
      }
      return { ...prev, [repoId]: { ...existing, refCount: existing.refCount - 1 } };
    });
  }, []);

  const setRepoLabel = useCallback((repoId, label) => {
    if (!repoId || !label) return;
    setRepoLabels((prev) => (prev[repoId] === label ? prev : { ...prev, [repoId]: label }));
  }, []);

  const markRepoSeen = useCallback((repoId) => {
    if (!repoId) return;
    setUnreadByRepo((prev) => (prev[repoId] ? { ...prev, [repoId]: 0 } : prev));
  }, []);

  const openPanel = useCallback((repoId) => {
    const target = repoId || activeRepoIdRef.current;
    setPanelOpen(true);
    setBubble(null);
    if (target) setActiveRepoId(target);
    markRepoSeen(target);
  }, [markRepoSeen]);

  const closePanel = useCallback(() => setPanelOpen(false), []);

  const handleEvent = useCallback((repoId, event) => {
    setEventsByRepo((prev) => {
      const list = prev[repoId] || [];
      return { ...prev, [repoId]: [event, ...list].slice(0, MAX_EVENTS_PER_REPO) };
    });

    if (event.type === "scan_started") {
      setScanningRepos((prev) => ({ ...prev, [repoId]: true }));
    } else if (event.type === "scan_completed" || event.type === "fix_ready") {
      setScanningRepos((prev) => {
        if (!prev[repoId]) return prev;
        const next = { ...prev };
        delete next[repoId];
        return next;
      });
    }

    const isViewing = panelOpenRef.current && activeRepoIdRef.current === repoId;
    if (isViewing) return;

    setUnreadByRepo((prev) => ({ ...prev, [repoId]: (prev[repoId] || 0) + 1 }));
    if (HIGH_URGENCY_TYPES.has(event.type)) {
      const message = bubbleMessage(event);
      if (message) setBubble({ repoId, message });
    }
  }, []);

  const repoIds = useMemo(() => Object.keys(watchedRepos), [watchedRepos]);
  useRepoActivitySockets(repoIds, handleEvent);

  const totalUnread = useMemo(
    () => Object.values(unreadByRepo).reduce((sum, n) => sum + n, 0),
    [unreadByRepo],
  );
  const isThinking = useMemo(() => Object.keys(scanningRepos).length > 0, [scanningRepos]);
  const dismissBubble = useCallback(() => setBubble(null), []);

  const value = useMemo(() => ({
    watchedRepos, repoLabels, activeRepoId, panelOpen, unreadByRepo, eventsByRepo, totalUnread, isThinking,
    bubble, trackRepoView, untrackRepoView, markRepoSeen, openPanel, closePanel, dismissBubble, setRepoLabel,
    setActiveRepoId,
  }), [watchedRepos, repoLabels, activeRepoId, panelOpen, unreadByRepo, eventsByRepo, totalUnread, isThinking,
      bubble, trackRepoView, untrackRepoView, markRepoSeen, openPanel, closePanel, dismissBubble, setRepoLabel]);

  return <MascotContext.Provider value={value}>{children}</MascotContext.Provider>;
}

export function useMascot() {
  const ctx = useContext(MascotContext);
  if (!ctx) throw new Error("useMascot은 MascotProvider 안에서만 쓸 수 있습니다");
  return ctx;
}
