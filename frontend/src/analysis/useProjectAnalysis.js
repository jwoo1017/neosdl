import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchProjectAnalysis } from "../api";
import { useRepoActivitySockets } from "../mascot/mascotSocket";

const POLL_MS = 15000;
const SCAN_EVENTS = new Set(["scan_started", "scan_stage", "scan_completed", "scan_failed"]);

// 프로젝트 분석 상태(GET /projects/{id}/analysis). 저장소 실시간 소켓의 스캔 이벤트가 오면
// 다시 조회하고, 분석 중에는 소켓이 끊겨도 맞도록 15초마다 한 번 더 확인한다.
// 반환: { analysis (null = 불러오는 중), refresh }
export function useProjectAnalysis(projectId) {
  const [analysis, setAnalysis] = useState(null);

  const refresh = useCallback(() => {
    if (!projectId) return Promise.resolve();
    return fetchProjectAnalysis(projectId)
      .then(setAnalysis)
      .catch(() => {}); // 일시적 실패 — 다음 이벤트/주기에 다시 맞춘다
  }, [projectId]);

  useEffect(() => {
    setAnalysis(null);
    refresh();
  }, [refresh]);

  const repoId = analysis?.latest?.repository?.id;
  const repoIds = useMemo(() => (repoId ? [repoId] : []), [repoId]);
  const onEvent = useCallback((_repoId, event) => {
    if (SCAN_EVENTS.has(event?.type)) refresh();
  }, [refresh]);
  useRepoActivitySockets(repoIds, onEvent);

  const inProgress = analysis?.state === "analyzing" || analysis?.state === "ready_reanalyzing";
  useEffect(() => {
    if (!inProgress) return undefined;
    const timer = setInterval(() => {
      if (!document.hidden) refresh();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [inProgress, refresh]);

  return { analysis, refresh };
}
