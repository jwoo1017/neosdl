// 자체 제작 SVG 아이콘 — engagement 스텝/도구/상태용. currentColor 로 그려 라이트/다크 자동 적응.
// 외부 아이콘 라이브러리 의존 없음(인라인 SVG). EngageTimeline·EngagementDetailPage·채팅(A)에서 공용.

function Svg({ size = 18, title, children }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden={!title}
         role={title ? "img" : undefined} style={{ flex: "none", display: "block" }}>
      {title && <title>{title}</title>}
      {children}
    </svg>
  );
}

// 컨테이너 기동(도커 박스 + 전원)
export const IconBoot = (p) => (
  <Svg {...p} title="기동">
    <rect x="3" y="7" width="18" height="12" rx="2" />
    <path d="M7 7V5h10v2M9 12h6" />
  </Svg>
);

// 포트 스캔(nmap) — 레이더: 원 + 십자 + 스윕 + 표적점
export const IconScan = (p) => (
  <Svg {...p} title="스캔">
    <circle cx="12" cy="12" r="8" />
    <path d="M12 4v4M12 16v4M4 12h4M16 12h4" />
    <path d="M12 12l5-3" />
    <circle cx="15.5" cy="9.5" r="1.1" fill="currentColor" stroke="none" />
  </Svg>
);

// 웹 점검(nikto/curl) — 지구본
export const IconWeb = (p) => (
  <Svg {...p} title="웹">
    <circle cx="12" cy="12" r="8" />
    <path d="M4 12h16M12 4c2.5 2.3 2.5 13.7 0 16M12 4c-2.5 2.3-2.5 13.7 0 16" />
  </Svg>
);

// SQL 주입(sqlmap) — DB 실린더 + 주입 화살표
export const IconInject = (p) => (
  <Svg {...p} title="주입">
    <ellipse cx="9" cy="6" rx="5" ry="2.2" />
    <path d="M4 6v8c0 1.2 2.2 2.2 5 2.2s5-1 5-2.2V6" />
    <path d="M18 9v8M18 17l-2-2M18 17l2-2" />
  </Svg>
);

// 발견/취약점 — 방패 + 느낌표
export const IconFinding = (p) => (
  <Svg {...p} title="발견">
    <path d="M12 3l7 3v5c0 4.2-2.9 7.4-7 9-4.1-1.6-7-4.8-7-9V6z" />
    <path d="M12 8v4M12 15v.5" />
  </Svg>
);

// 승인 대기 — 일시정지
export const IconPause = (p) => (
  <Svg {...p} title="승인 대기">
    <rect x="7" y="5" width="3.5" height="14" rx="1" />
    <rect x="13.5" y="5" width="3.5" height="14" rx="1" />
  </Svg>
);

// 리포트 — 문서
export const IconReport = (p) => (
  <Svg {...p} title="리포트">
    <path d="M6 3h8l4 4v14H6z" />
    <path d="M14 3v4h4M9 12h6M9 16h6" />
  </Svg>
);

// 엔드포인트/경로 — 노드-링크
export const IconEndpoint = (p) => (
  <Svg {...p} title="엔드포인트">
    <circle cx="5" cy="12" r="2.2" />
    <circle cx="19" cy="6" r="2.2" />
    <circle cx="19" cy="18" r="2.2" />
    <path d="M7 11l10-4M7 13l10 4" />
  </Svg>
);

// 터미널(기타 명령) — 프롬프트
export const IconTerminal = (p) => (
  <Svg {...p} title="명령">
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M7 9l3 3-3 3M13 15h4" />
  </Svg>
);

export const IconCheck = (p) => (<Svg {...p} title="성공"><path d="M4 12l5 5L20 6" /></Svg>);
export const IconX = (p) => (<Svg {...p} title="실패"><path d="M6 6l12 12M18 6L6 18" /></Svg>);

// 에이전트 추론(말풍선)
export const IconThought = (p) => (
  <Svg {...p} title="추론">
    <path d="M5 5h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H9l-4 3v-3H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2z" />
    <path d="M8 10h.01M12 10h.01M16 10h.01" />
  </Svg>
);

// 인증 필요(자물쇠)
export const IconLock = (p) => (
  <Svg {...p} title="인증 필요">
    <rect x="5" y="11" width="14" height="9" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </Svg>
);

// 명령어 → 도구 아이콘 매핑(소문자 접두 기준).
export function toolIcon(command) {
  const c = (command || "").trim().toLowerCase();
  if (c.startsWith("nmap")) return IconScan;
  if (c.startsWith("nikto")) return IconWeb;
  if (c.startsWith("sqlmap")) return IconInject;
  if (c.startsWith("curl") || c.startsWith("wget") || c.startsWith("nc ") || c.startsWith("whatweb")) return IconWeb;
  return IconTerminal;
}
