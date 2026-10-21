// 팀스페이스 역할 배지 — 아바타 위에 모자처럼 얹는다(components/Avatar.jsx 의 role prop).
//  admin  : 금색 왕관
//  member : 청록 캡 모자
// 색은 라이트/다크 테마 모두에서 아바타 위에 잘 보이도록 테두리(stroke)를 둔다.
export default function RoleHatIcon({ role, size = 16, className }) {
  const label = role === "admin" ? "관리자" : "멤버";
  return (
    <svg
      className={className}
      width={size}
      height={size * (2 / 3)}
      viewBox="0 0 24 16"
      role="img"
      aria-label={label}
    >
      <title>{label}</title>
      {role === "admin" ? (
        <g strokeLinejoin="round">
          {/* 왕관 몸통: 바닥 띠 + 세 개의 뾰족한 봉우리 */}
          <path
            d="M3 13 L2 4.5 L7.5 8.5 L12 2 L16.5 8.5 L22 4.5 L21 13 Z"
            fill="#f5b70a"
            stroke="#8a5a00"
            strokeWidth="1.2"
          />
          <rect x="3" y="12" width="18" height="3" rx="1" fill="#e09b00" stroke="#8a5a00" strokeWidth="1.2" />
          {/* 봉우리 끝 보석 */}
          <circle cx="2" cy="4.5" r="1.4" fill="#ffe27a" stroke="#8a5a00" strokeWidth="0.8" />
          <circle cx="12" cy="2" r="1.6" fill="#ff6b6b" stroke="#8a5a00" strokeWidth="0.8" />
          <circle cx="22" cy="4.5" r="1.4" fill="#ffe27a" stroke="#8a5a00" strokeWidth="0.8" />
        </g>
      ) : (
        <g strokeLinejoin="round">
          {/* 캡 모자: 둥근 정수리 + 앞챙 */}
          <path d="M4 12 C4 5.5 8 2.5 12 2.5 C16 2.5 20 5.5 20 12 Z" fill="#2f9c8f" stroke="#155e56" strokeWidth="1.2" />
          <path d="M12 12 L23 12 C23 14 21.5 15 19 15 L12 15 Z" fill="#23786e" stroke="#155e56" strokeWidth="1.2" />
          <line x1="12" y1="3" x2="12" y2="12" stroke="#155e56" strokeWidth="0.8" opacity="0.6" />
          <circle cx="12" cy="2.6" r="1.1" fill="#7fd6cb" stroke="#155e56" strokeWidth="0.6" />
        </g>
      )}
    </svg>
  );
}
