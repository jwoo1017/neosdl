// 개인 페이지 아이콘 — 사람 실루엣 + 오른쪽 아래 자물쇠(나만 보임).
export default function PersonalSpaceIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <circle cx="6.5" cy="4.8" r="2.6" stroke="currentColor" strokeWidth="1.4" />
      <path d="M1.8 14 C1.8 10.8 3.9 9 6.5 9 C7.4 9 8.2 9.2 8.9 9.6" stroke="currentColor" strokeWidth="1.4"
            strokeLinecap="round" />
      <rect x="9.6" y="11" width="5" height="3.8" rx="0.9" fill="currentColor" />
      <path d="M10.7 11 V9.9 C10.7 9 11.4 8.4 12.1 8.4 C12.8 8.4 13.5 9 13.5 9.9 V11" stroke="currentColor"
            strokeWidth="1.2" />
    </svg>
  );
}
