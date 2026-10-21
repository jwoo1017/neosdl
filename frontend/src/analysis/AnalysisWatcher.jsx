import { useCallback, useEffect, useRef } from "react";
import { fetchProjectAnalysis, requestScan } from "../api";
import { useTabs } from "../TabsContext";
import { useToast } from "../toast/ToastContext";
import { onWatchListChange, readWatchList, unwatchProjectAnalysis, watchProjectAnalysis } from "./analysisWatch";

const POLL_MS = 10000;

// 저장소를 연결한 프로젝트의 분석이 끝나면 어느 화면에 있든 알려준다(AppLayout 에 하나).
//  - 이 탭을 보고 있으면: 토스트 + [채팅 시작하기]
//  - 다른 탭/창을 보고 있으면: 브라우저 알림(허용한 경우) + 탭 제목 "(1) 분석 완료 · …"
// 감시 목록은 analysisWatch.js(localStorage). 실시간 소켓 대신 짧은 주기 확인을 쓴다 —
// 감시 대상이 있을 때만 돌고, 프로젝트 화면을 안 열어 둬도 동작해야 해서다.
export default function AnalysisWatcher() {
  const toast = useToast();
  const { openChatTab } = useTabs();
  const unseenRef = useRef(0);
  const baseTitleRef = useRef(null);
  const checkingRef = useRef(false);

  const markUnseen = useCallback((label) => {
    if (!document.hidden) return;
    if (baseTitleRef.current === null) baseTitleRef.current = document.title;
    unseenRef.current += 1;
    document.title = `(${unseenRef.current}) ${label} · ${baseTitleRef.current}`;
  }, []);

  const notifyBrowser = useCallback((title, body, onClick) => {
    if (!document.hidden) return;
    try {
      if ("Notification" in window && Notification.permission === "granted") {
        const n = new Notification(title, { body, tag: `neosdl-analysis-${title}` });
        n.onclick = () => {
          window.focus();
          onClick?.();
          n.close();
        };
      }
    } catch {
      // 알림을 못 띄우는 환경 — 탭 제목과 토스트로 충분하다
    }
  }, []);

  const check = useCallback(async () => {
    if (checkingRef.current) return;
    const list = readWatchList();
    if (list.length === 0) return;
    checkingRef.current = true;
    try {
      for (const w of list) {
        let a;
        try {
          a = await fetchProjectAnalysis(w.projectId);
        } catch {
          continue; // 권한이 사라졌거나 일시 오류 — 다음 주기에
        }
        const project = { id: w.projectId, name: w.name };
        if (a.state === "ready" || (a.chat_ready && a.state !== "no_repository")) {
          unwatchProjectAnalysis(w.projectId);
          const count = a.latest?.findings_count;
          const body = count == null ? "이제 코드에 대해 질문할 수 있어요." : `보안 이슈 ${count}건 · 이제 질문할 수 있어요.`;
          const openChat = () => openChatTab(project);
          toast.show({
            kind: "success",
            title: `‘${w.name}’ 분석 완료`,
            body,
            action: { label: "채팅 시작하기", onClick: openChat },
          });
          notifyBrowser(`‘${w.name}’ 분석 완료`, body, openChat);
          markUnseen("분석 완료");
        } else if (a.state === "failed" || a.state === "no_repository") {
          unwatchProjectAnalysis(w.projectId);
          if (a.state === "no_repository") continue; // 연결을 해제했다 — 알릴 것 없음
          const repoId = a.latest?.repository?.id;
          toast.show({
            kind: "error",
            title: `‘${w.name}’ 분석 실패`,
            body: a.latest?.error || "잠시 후 다시 시도해 주세요.",
            action: repoId
              ? {
                  label: "다시 분석",
                  onClick: () => {
                    requestScan(repoId).then(() => watchProjectAnalysis(w.projectId, w.name)).catch(() => {});
                  },
                }
              : undefined,
          });
          notifyBrowser(`‘${w.name}’ 분석 실패`, a.latest?.error || "", null);
          markUnseen("분석 실패");
        }
      }
    } finally {
      checkingRef.current = false;
    }
  }, [toast, openChatTab, notifyBrowser, markUnseen]);

  useEffect(() => {
    check();
    const timer = setInterval(check, POLL_MS);
    const onVisible = () => {
      if (document.hidden) return;
      // 돌아왔으니 탭 제목 표시는 원래대로
      if (baseTitleRef.current !== null) {
        document.title = baseTitleRef.current;
        baseTitleRef.current = null;
        unseenRef.current = 0;
      }
      check();
    };
    document.addEventListener("visibilitychange", onVisible);
    const offWatch = onWatchListChange(check);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      offWatch();
    };
  }, [check]);

  return null;
}
