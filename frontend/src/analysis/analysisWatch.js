// 완료 알림을 기다리는 프로젝트 목록(저장소를 연결한 사람의 브라우저에만 둔다).
// 연결 모달이 넣고, 전역 AnalysisWatcher 가 완료/실패를 확인하면 뺀다.
// localStorage 라 새로고침해도 이어지고, 같은 탭 안에서는 이벤트로 바로 알린다.

const KEY = "neosdl.analysis.watch";
const EVENT = "neosdl:analysis-watch";
const MAX_AGE_MS = 24 * 60 * 60 * 1000; // 하루 넘게 안 끝난 건 알림 대상에서 놓아준다

export function readWatchList() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY) || "[]");
    const now = Date.now();
    return Array.isArray(list) ? list.filter((w) => w?.projectId && now - (w.since || 0) < MAX_AGE_MS) : [];
  } catch {
    return [];
  }
}

function writeWatchList(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // 저장소가 막힌 환경(사생활 보호 모드 등) — 이번 탭의 이벤트로만 알린다
  }
  window.dispatchEvent(new CustomEvent(EVENT));
}

export function watchProjectAnalysis(projectId, name) {
  const list = readWatchList().filter((w) => w.projectId !== projectId);
  writeWatchList([...list, { projectId, name: name || "프로젝트", since: Date.now() }]);
}

export function unwatchProjectAnalysis(projectId) {
  writeWatchList(readWatchList().filter((w) => w.projectId !== projectId));
}

export function onWatchListChange(handler) {
  const onStorage = (e) => {
    if (e.key === KEY) handler();
  };
  window.addEventListener(EVENT, handler);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(EVENT, handler);
    window.removeEventListener("storage", onStorage);
  };
}

// 브라우저 알림 권한은 사용자 동작(클릭) 안에서만 물을 수 있다 — 연결 버튼을 누를 때 부른다.
export function requestNotificationPermission() {
  try {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  } catch {
    // 지원하지 않는 브라우저 — 토스트만 쓴다
  }
}

// 사용자에게 보이는 분석 단계(app/analysis.py STAGES 와 같은 순서)
export const ANALYSIS_STAGES = [
  { key: "fetch", label: "저장소 가져오기" },
  { key: "parse", label: "코드 구조 분석" },
  { key: "annotate", label: "의미 분석" },
  { key: "risk", label: "보안·위험 분석" },
];
