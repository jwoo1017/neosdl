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

// ---- 팀스페이스 공개 범위
// 공개 — 지구본
export const GlobeIcon = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5c2.4 2.3 3.6 5.1 3.6 8.5s-1.2 6.2-3.6 8.5c-2.4-2.3-3.6-5.1-3.6-8.5S9.6 5.8 12 3.5z" />
  </Svg>
);

// 참가 제한 — 열쇠구멍 있는 문(보이지만 초대로만 들어온다)
export const DoorKeyIcon = (p) => (
  <Svg {...p}>
    <path d="M6 20.5V5a1.5 1.5 0 0 1 1.5-1.5h9A1.5 1.5 0 0 1 18 5v15.5M4 20.5h16" />
    <circle cx="12" cy="11" r="1.6" />
    <path d="M12 12.6v2.6" />
  </Svg>
);

// 비공개 — 가려진 눈
export const EyeOffIcon = (p) => (
  <Svg {...p}>
    <path d="M3 12s3.2-6 9-6c1.6 0 3 .4 4.2 1M21 12s-3.2 6-9 6c-1.6 0-3-.4-4.2-1" />
    <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
    <path d="M4 20L20 4" />
  </Svg>
);

// ---- 직무
// PM — 체크리스트 보드
export const PmIcon = (p) => (
  <Svg {...p}>
    <rect x="5" y="4.5" width="14" height="16" rx="2" />
    <path d="M9 3.5h6v2.5H9z" />
    <path d="M8.5 11l1.4 1.4 2.4-2.6M8.5 16l1.4 1.4 2.4-2.6M14 11.2h2M14 16.2h2" />
  </Svg>
);

// 프론트엔드 — 브라우저 창
export const FrontendIcon = (p) => (
  <Svg {...p}>
    <rect x="3.5" y="4.5" width="17" height="15" rx="2" />
    <path d="M3.5 8.5h17" />
    <path d="M10 12.5l-2 2 2 2M14 12.5l2 2-2 2" />
  </Svg>
);

// 백엔드 — 서버 랙
export const BackendIcon = (p) => (
  <Svg {...p}>
    <rect x="4" y="4" width="16" height="7" rx="1.5" />
    <rect x="4" y="13" width="16" height="7" rx="1.5" />
    <path d="M7.5 7.5h.01M7.5 16.5h.01M11 7.5h5M11 16.5h5" />
  </Svg>
);
