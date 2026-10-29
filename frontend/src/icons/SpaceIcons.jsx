// 사이드바(팀스페이스/개인 페이지)·워크스페이스 관리·시작 페이지에서 쓰는 자체 SVG 아이콘.
// currentColor 로 그려 주변 텍스트 색을 따른다. 장식용이라 aria-hidden.

function Svg({ size = 16, strokeWidth = 1.6, children, className }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round"
         aria-hidden="true" focusable="false" className={className} style={{ flex: "none", display: "block" }}>
      {children}
    </svg>
  );
}

// 팀스페이스 — 두 사람(앞뒤로 겹침)
export const TeamspaceIcon = (p) => (
  <Svg {...p}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M3 19.5c0-3.3 2.7-5.6 6-5.6s6 2.3 6 5.6" />
    <path d="M15.5 4.9a3.2 3.2 0 0 1 0 6.2M17.6 14.2c2 .7 3.4 2.6 3.4 5.3" />
  </Svg>
);

// 관리(톱니)
export const GearIcon = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7" />
  </Svg>
);

// 접기/펼치기 화살표 — 펼치면 className 으로 90° 회전
export const ChevronRightIcon = (p) => (
  <Svg strokeWidth={2} {...p}>
    <path d="M9.5 6l6 6-6 6" />
  </Svg>
);

export const ChevronDownIcon = (p) => (
  <Svg strokeWidth={2} {...p}>
    <path d="M6 9.5l6 6 6-6" />
  </Svg>
);

export const CheckIcon = (p) => (
  <Svg strokeWidth={2} {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Svg>
);

// 채팅 말풍선
export const ChatIcon = (p) => (
  <Svg {...p}>
    <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5z" />
    <path d="M8 9h8M8 12h5" />
  </Svg>
);

// 방패 + 체크 — 검증
export const ShieldCheckIcon = (p) => (
  <Svg {...p}>
    <path d="M12 3l7 3v5.5c0 4.2-2.9 7.9-7 9.5-4.1-1.6-7-5.3-7-9.5V6z" />
    <path d="M8.8 12.2l2.2 2.2 4.2-4.4" />
  </Svg>
);

// 레포(브랜치)
export const RepoIcon = (p) => (
  <Svg {...p}>
    <circle cx="6.5" cy="5.5" r="2" />
    <circle cx="6.5" cy="18.5" r="2" />
    <circle cx="17.5" cy="8.5" r="2" />
    <path d="M6.5 7.5v9M17.5 10.5c0 4-4 4-9.5 6.5" />
  </Svg>
);

// 복사
export const CopyIcon = (p) => (
  <Svg {...p}>
    <rect x="8" y="8" width="12" height="12" rx="2" />
    <path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" />
  </Svg>
);

// 링크
export const InviteLinkIcon = (p) => (
  <Svg {...p}>
    <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
    <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
  </Svg>
);
