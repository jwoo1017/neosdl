import { useEffect, useState } from "react";
import {
  IconActivity, IconClock, IconSignalOff, IconPause, IconFlag, IconX, describeCommand,
} from "./EngageIcons";

// Engagement 라이브 상태 스트립 — "진행 중인지 멈췄는지" 를 한눈에 보이게 한다.
// 실시간 경과 타이머 + 마지막 활동 경과 + 현재 활동 해석 + 연결/응답 상태 구분.
// EngagementDetailPage(BlackboxDetail) 전용이지만 props 만 맞으면 어디서든 재사용 가능.

const TERMINAL = ["succeeded", "failed", "cancelled"];
const STALL_MS = 90_000;   // 이보다 오래 활동 없으면 "응답 대기"(멈춤 아님 — 긴 명령일 수 있음)

function fmtDur(ms) {
  if (ms == null || ms < 0) return "-";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return rs ? `${m}분 ${rs}초` : `${m}분`;
  const h = Math.floor(m / 60);
  return `${h}시간 ${m % 60}분`;
}

// 1초마다 리렌더(실행 중일 때만) — 경과 타이머가 살아있음을 보여준다.
function useTick(active) {
  const [, setN] = useState(0);
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => setN((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
}

export default function EngageLiveStatus({ status, connected, steps = [], lastActivityAt, startedAt }) {
  const terminal = TERMINAL.includes(status);
  useTick(!terminal);

  const now = Date.now();
  const lastCmd = steps.length ? steps[steps.length - 1].command : null;
  const sinceActivity = lastActivityAt ? now - lastActivityAt : null;

  // 모드 판정 — 화면 색/아이콘/문구를 결정한다.
  let mode, Icon, title, caption;
  if (status === "succeeded") {
    mode = "done"; Icon = IconFlag; title = "점검 완료";
    caption = `총 ${steps.length}개 스텝 실행 · 결과는 아래에서 확인`;
  } else if (status === "failed") {
    mode = "fail"; Icon = IconX; title = "점검 실패";
    caption = "오류로 중단됨 — 사유는 상단 오류 메시지를 확인하세요";
  } else if (status === "cancelled") {
    mode = "idle"; Icon = IconX; title = "취소됨"; caption = "사용자가 점검을 취소했습니다";
  } else if (status === "awaiting_approval") {
    mode = "pause"; Icon = IconPause; title = "승인 대기 — 멈춰 있어요";
    caption = "침투성 명령 앞에서 멈췄습니다. 아래에서 승인하면 이어서 진행합니다";
  } else if (!connected) {
    mode = "warn"; Icon = IconSignalOff; title = "실시간 연결 끊김 — 재연결 중";
    caption = "점검은 서버에서 계속 진행 중일 수 있습니다. 연결이 돌아오면 자동 갱신됩니다";
  } else if (steps.length === 0) {
    mode = "active"; Icon = IconActivity; title = "점검 준비 중";
    caption = "샌드박스에 표적 앱을 기동하고 도구를 준비하고 있습니다…";
  } else if (sinceActivity != null && sinceActivity > STALL_MS) {
    mode = "warn"; Icon = IconClock; title = "응답 대기 중";
    caption = `마지막 활동 ${fmtDur(sinceActivity)} 전 — 오래 걸리는 명령(전체 포트 스캔 등)일 수 있습니다`;
  } else {
    mode = "active"; Icon = IconActivity; title = "작업 중";
    caption = lastCmd ? `방금: ${describeCommand(lastCmd)}` : "에이전트가 점검을 진행하고 있습니다";
  }

  const elapsed = startedAt ? now - startedAt : null;

  return (
    <div className={`eng-live eng-live--${mode}`} role="status" aria-live="polite">
      <div className="eng-live-head">
        <span className="eng-live-icon" aria-hidden="true">
          {mode === "active" ? <span className="eng-live-pulse" /> : null}
          <Icon size={18} />
        </span>
        <strong className="eng-live-title">{title}</strong>
        <span className="eng-live-metrics">
          <span><IconActivity size={13} /> {steps.length} 스텝</span>
          {elapsed != null && !terminal && (
            <span><IconClock size={13} /> 경과 {fmtDur(elapsed)}</span>
          )}
          {sinceActivity != null && mode === "active" && (
            <span className="muted">마지막 활동 {fmtDur(sinceActivity)} 전</span>
          )}
        </span>
      </div>
      <div className="eng-live-caption">{caption}</div>
      {mode === "active" && <div className="eng-live-bar" aria-hidden="true"><span /></div>}
    </div>
  );
}
