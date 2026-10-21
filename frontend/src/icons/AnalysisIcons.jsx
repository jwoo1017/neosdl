// 분석 상태·알림용 아이콘(자체 제작). currentColor 를 따라 색이 바뀐다.

export function CheckCircleIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="10" cy="10" r="8.25" stroke="currentColor" strokeWidth="1.5" />
      <path d="M6.4 10.3l2.4 2.4 4.8-5" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function AlertCircleIcon({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="10" cy="10" r="8.25" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10 5.8v5.2" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <circle cx="10" cy="13.9" r="1" fill="currentColor" />
    </svg>
  );
}

// 코드 분석 중 — 돋보기 안에 코드 괄호
export function CodeScanIcon({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="10.5" cy="10.5" r="6.75" stroke="currentColor" strokeWidth="1.5" />
      <path d="M15.4 15.4L20 20" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
      <path d="M8.6 8.4l-2 2.1 2 2.1M12.4 8.4l2 2.1-2 2.1" stroke="currentColor" strokeWidth="1.4"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// 저장소 연결 — 사슬 고리 대신 저장소 상자 + 플러스
export function RepoPlusIcon({ size = 22 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M5 5.5A2.5 2.5 0 017.5 3H18v13.5H7.5A2.5 2.5 0 005 19V5.5z" stroke="currentColor" strokeWidth="1.5"
        strokeLinejoin="round" />
      <path d="M5 19a2.5 2.5 0 002.5 2.5H18v-5" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M11.5 7v6M8.5 10h6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

// 다시 시도 — 원형 화살표
export function RetryIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path d="M13 8a5 5 0 11-1.6-3.67" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M13.2 2.6v2.9h-2.9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// 진행 중 단계 표시용 회전 고리(CSS 로 돌린다)
export function SpinnerRing({ size = 14 }) {
  return (
    <svg className="spinner-ring" width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
      <path d="M14 8a6 6 0 00-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
