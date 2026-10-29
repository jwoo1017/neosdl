// 검증 run 실행 모달 — 검증 유형/권한 모드 카드 아이콘. currentColor 로 그려 선택 상태 색을 따른다.
// 장식용이라 aria-hidden, <title> 없음(브라우저 기본 툴팁을 띄우지 않는다).

function Svg({ size = 22, children }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
         focusable="false" style={{ flex: "none", display: "block" }}>
      {children}
    </svg>
  );
}

// 화이트박스 — 코드 문서 + 돋보기(소스의 finding 을 들여다본다)
export const WhiteboxIcon = (p) => (
  <Svg {...p}>
    <path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h5" />
    <path d="M13 3l5 5h-5z" />
    <path d="M7.5 10.5l-1.5 1.5 1.5 1.5M10.5 10.5l1.5 1.5-1.5 1.5" />
    <circle cx="17" cy="16" r="3" />
    <path d="M19.2 18.2L21 20" />
  </Svg>
);

// 레포 부팅 블랙박스 — 컨테이너 큐브(레포를 띄운다)
export const RepoBootIcon = (p) => (
  <Svg {...p}>
    <path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z" />
    <path d="M4 7.5l8 4.5 8-4.5M12 12v9" />
  </Svg>
);

// 블랙박스 — 외부 표적 조준선
export const BlackboxIcon = (p) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="8" />
    <circle cx="12" cy="12" r="3.5" />
    <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
    <circle cx="12" cy="12" r="0.9" fill="currentColor" stroke="none" />
  </Svg>
);

// ask — 방패 + 체크(사람 승인)
export const AskModeIcon = (p) => (
  <Svg {...p}>
    <path d="M12 3l7 3v5.5c0 4.2-2.9 7.9-7 9.5-4.1-1.6-7-5.3-7-9.5V6z" />
    <path d="M8.8 12.2l2.2 2.2 4.2-4.4" />
  </Svg>
);

// plan — 설계도(자 + 연필)
export const PlanModeIcon = (p) => (
  <Svg {...p}>
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <path d="M4 9h16M9 9v11" />
    <path d="M12.5 13h4.5M12.5 16.5h3" />
  </Svg>
);

// auto — 번개(사전 승인 후 자동 진행)
export const AutoModeIcon = (p) => (
  <Svg {...p}>
    <path d="M13 3L5 13.5h6L10 21l8-10.5h-6z" />
  </Svg>
);

// 선택 표시 — 원 안 체크
export const RadioCheckIcon = ({ size = 16 }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden="true" focusable="false"
       style={{ flex: "none", display: "block" }}>
    <circle cx="8" cy="8" r="7" fill="currentColor" />
    <path d="M4.8 8.2l2.1 2.1 4.3-4.5" fill="none" stroke="var(--surface, #131a22)" strokeWidth="1.8"
          strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
