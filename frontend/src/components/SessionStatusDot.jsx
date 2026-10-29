// 채팅 세션 상태 점 — Claude 세션 목록처럼 제목 앞 작은 원 하나로 지금 상태를 보여준다.
//   running  보안 점검 진행 중 / AI 가 답하는 중   청록 + 회전하는 호
//   approval 명령 승인 대기(사람 차례)             주황 + 맥박(눈에 띄어야 한다)
//   waiting  대기열·일시정지                      노랑 빈 원
//   done     점검 완료                            초록
//   failed   점검 실패                            빨강
//   idle     점검 없음                            흐린 회색
// engagement_status(sql/035 engagement_status) → 점 상태.
const FROM_ENGAGEMENT = {
  planning: "running",
  running: "running",
  awaiting_approval: "approval",
  queued: "waiting",
  paused: "waiting",
  succeeded: "done",
  failed: "failed",
  cancelled: "idle",
};

export const SESSION_STATUS_LABELS = {
  running: "보안 점검 진행 중",
  answering: "답변 생성 중",
  approval: "승인 대기 — 실행할 명령을 확인해 주세요",
  waiting: "대기 중",
  done: "보안 점검 완료",
  failed: "보안 점검 실패",
  idle: "",
};

export function sessionDotStatus(session, { answering = false } = {}) {
  const fromEng = FROM_ENGAGEMENT[session?.engagement_status] || "idle";
  // 사람 손이 필요한 상태(승인 대기)가 가장 우선, 그다음 진행 중인 일.
  if (fromEng === "approval" || fromEng === "running") return fromEng;
  if (answering) return "answering";
  return fromEng;
}

export function isActiveSessionStatus(status) {
  return status === "running" || status === "approval" || status === "waiting";
}

export default function SessionStatusDot({ status = "idle", size = 10 }) {
  const label = SESSION_STATUS_LABELS[status] || "";
  const kind = status === "answering" ? "running" : status;
  return (
    <svg width={size} height={size} viewBox="0 0 10 10" className={`session-dot session-dot-${kind}`}
         role={label ? "img" : undefined} aria-label={label || undefined} aria-hidden={label ? undefined : true}>
      {label && <title>{label}</title>}
      {kind === "running" && (
        <>
          <circle cx="5" cy="5" r="3.6" className="session-dot-track" />
          <path d="M5 1.4 A3.6 3.6 0 0 1 8.6 5" className="session-dot-arc" />
          <circle cx="5" cy="5" r="1.6" className="session-dot-core" />
        </>
      )}
      {kind === "approval" && (
        <>
          <circle cx="5" cy="5" r="4.2" className="session-dot-halo" />
          <circle cx="5" cy="5" r="2.8" className="session-dot-core" />
        </>
      )}
      {kind === "waiting" && <circle cx="5" cy="5" r="3.2" className="session-dot-ring" />}
      {(kind === "done" || kind === "failed" || kind === "idle") && (
        <circle cx="5" cy="5" r={kind === "idle" ? 2.6 : 3.2} className="session-dot-core" />
      )}
    </svg>
  );
}
