import { useEffect, useState } from "react";
import { ANALYSIS_STAGES } from "./analysisWatch";
import { AlertCircleIcon, CheckCircleIcon, CodeScanIcon, RepoPlusIcon, RetryIcon, SpinnerRing } from "../icons/AnalysisIcons";

// 첫 분석이 끝나기 전 채팅 입력창 위에 보이는 상태 패널(ProjectChatPage).
//  - no_repository: 저장소 연결 안내 + 연결 화면으로
//  - analyzing: 4단계 진행 + 경과 시간
//  - failed: 실패 사유 + 다시 분석

function formatElapsed(ms) {
  const sec = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}분 ${String(s).padStart(2, "0")}초` : `${s}초`;
}

function useElapsed(startedAt) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!startedAt) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [startedAt]);
  return startedAt ? now - new Date(startedAt).getTime() : null;
}

function StageList({ current }) {
  const currentIndex = Math.max(0, ANALYSIS_STAGES.findIndex((s) => s.key === current));
  return (
    <ol className="analysis-stages">
      {ANALYSIS_STAGES.map((stage, i) => {
        const status = i < currentIndex ? "done" : i === currentIndex ? "active" : "todo";
        return (
          <li key={stage.key} className={`analysis-stage analysis-stage-${status}`}>
            <span className="analysis-stage-mark">
              {status === "done" ? <CheckCircleIcon size={16} /> : status === "active" ? <SpinnerRing size={16} />
                : <span className="analysis-stage-dot" />}
            </span>
            <span className="analysis-stage-label">{stage.label}</span>
          </li>
        );
      })}
    </ol>
  );
}

export default function AnalysisLockPanel({ analysis, onConnectRepository, onRetry }) {
  const [retrying, setRetrying] = useState(false);
  const latest = analysis?.latest;
  const elapsed = useElapsed(analysis?.state === "analyzing" ? latest?.started_at : null);

  if (!analysis) return null;

  if (analysis.state === "no_repository") {
    return (
      <div className="analysis-panel">
        <span className="analysis-panel-icon"><RepoPlusIcon /></span>
        <div className="analysis-panel-text">
          <div className="analysis-panel-title">저장소를 연결하면 질문할 수 있어요</div>
          <div className="analysis-panel-desc">연결하면 바로 코드를 분석하고, 끝나면 알려드려요.</div>
        </div>
        {onConnectRepository && (
          <button type="button" className="analysis-panel-btn" onClick={onConnectRepository}>
            저장소 연결
          </button>
        )}
      </div>
    );
  }

  if (analysis.state === "failed") {
    const retry = async () => {
      setRetrying(true);
      try {
        await onRetry();
      } finally {
        setRetrying(false);
      }
    };
    return (
      <div className="analysis-panel analysis-panel-error">
        <span className="analysis-panel-icon"><AlertCircleIcon size={22} /></span>
        <div className="analysis-panel-text">
          <div className="analysis-panel-title">
            {latest?.repository?.full_name ? `${latest.repository.full_name} 분석에 실패했어요` : "분석에 실패했어요"}
          </div>
          {latest?.error && <div className="analysis-panel-desc analysis-panel-error-text">{latest.error}</div>}
        </div>
        {onRetry && latest?.repository?.id && (
          <button type="button" className="analysis-panel-btn" onClick={retry} disabled={retrying}>
            <RetryIcon /> {retrying ? "요청 중..." : "다시 분석"}
          </button>
        )}
      </div>
    );
  }

  if (analysis.state === "analyzing") {
    return (
      <div className="analysis-panel analysis-panel-progress">
        <div className="analysis-panel-head">
          <span className="analysis-panel-icon"><CodeScanIcon /></span>
          <div className="analysis-panel-text">
            <div className="analysis-panel-title">
              {latest?.repository?.full_name || "저장소"} 코드를 분석하고 있어요
            </div>
            <div className="analysis-panel-desc">
              보통 몇 분 걸려요. 다른 작업을 하셔도 끝나면 알려드려요.
              {elapsed !== null && <span className="analysis-elapsed"> · {formatElapsed(elapsed)} 경과</span>}
            </div>
          </div>
        </div>
        <StageList current={latest?.stage || "fetch"} />
      </div>
    );
  }

  return null;
}
