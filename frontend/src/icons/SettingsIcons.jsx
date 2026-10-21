// 마이페이지 탭/행 아이콘(자체 제작 SVG). 색은 currentColor — 탭 활성 상태 색을 따라간다.
const base = { fill: "none", stroke: "currentColor", strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" };

export function UserTabIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="5.5" r="2.8" {...base} />
      <path d="M2.8 14 C2.8 10.9 5.1 9.2 8 9.2 C10.9 9.2 13.2 10.9 13.2 14" {...base} />
    </svg>
  );
}

export function KeyTabIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="5.2" cy="10.8" r="3" {...base} />
      <path d="M7.4 8.6 L13.5 2.5 M11.2 4.8 L12.8 6.4 M9.6 6.4 L10.8 7.6" {...base} />
    </svg>
  );
}

export function LinkTabIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M6.8 9.2 L9.2 6.8" {...base} />
      <path d="M7.6 4.4 L8.9 3.1 C10.1 1.9 12.1 1.9 13.3 3.1 C14.5 4.3 14.5 6.3 13.3 7.5 L12 8.8" {...base} />
      <path d="M8.4 11.6 L7.1 12.9 C5.9 14.1 3.9 14.1 2.7 12.9 C1.5 11.7 1.5 9.7 2.7 8.5 L4 7.2" {...base} />
    </svg>
  );
}

export function DangerTabIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 1.8 L14.6 13.6 H1.4 Z" {...base} />
      <path d="M8 6.2 V9.4" {...base} />
      <circle cx="8" cy="11.6" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ChevronDownIcon({ size = 12 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden="true">
      <path d="M3 4.5 L6 7.5 L9 4.5" {...base} />
    </svg>
  );
}

export function GithubMarkIcon({ size = 16 }) {
  // GitHub 상표 로고 대신 "저장소 연결"을 뜻하는 일반 브랜치 기호를 쓴다.
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="4.5" cy="3.5" r="1.8" {...base} />
      <circle cx="4.5" cy="12.5" r="1.8" {...base} />
      <circle cx="11.5" cy="6" r="1.8" {...base} />
      <path d="M4.5 5.3 V10.7 M11.5 7.8 C11.5 10 9 10.5 6.2 11.6" {...base} />
    </svg>
  );
}
