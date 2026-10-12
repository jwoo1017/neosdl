// 채팅 UI(진행 상황 타임라인 + 입력창)에서 쓰는 아이콘 — 아이콘 라이브러리 없이
// 직접 그린 SVG. stroke="currentColor"로 텍스트 색을 그대로 물려받는다.

export function IconSearch(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" {...props}>
      <circle cx="8.3" cy="8.3" r="5.3" />
      <line x1="16.2" y1="16.2" x2="12.4" y2="12.4" />
    </svg>
  );
}

export function IconWrench(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M13.6 3.4a4 4 0 0 0-5.3 4.9L2.9 13.7a1.8 1.8 0 0 0 2.6 2.6l5.4-5.4a4 4 0 0 0 4.9-5.3l-2.6 2.6-2-2 2.6-2.6z" />
    </svg>
  );
}

export function IconCheck(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <polyline points="4,10.5 8,14.5 16,5.5" />
    </svg>
  );
}

export function IconWarn(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" {...props}>
      <circle cx="10" cy="10" r="7.3" />
      <line x1="7.3" y1="7.3" x2="12.7" y2="12.7" />
      <line x1="12.7" y1="7.3" x2="7.3" y2="12.7" />
    </svg>
  );
}

export function IconPencil(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M12.9 3.6l3.5 3.5L6.2 17.3l-4 .5.5-4L12.9 3.6z" />
      <line x1="11.2" y1="5.3" x2="14.7" y2="8.8" />
    </svg>
  );
}

export function IconList(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" {...props}>
      <line x1="7.2" y1="5.5" x2="17" y2="5.5" />
      <line x1="7.2" y1="10" x2="17" y2="10" />
      <line x1="7.2" y1="14.5" x2="17" y2="14.5" />
      <circle cx="3.3" cy="5.5" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="3.3" cy="10" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="3.3" cy="14.5" r="0.9" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function IconChevronDown(props) {
  return (
    <svg viewBox="0 0 20 20" width="12" height="12" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <polyline points="5,7.5 10,12.5 15,7.5" />
    </svg>
  );
}

export function IconBack(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <polyline points="12.5,4.5 6.5,10 12.5,15.5" />
    </svg>
  );
}

export function IconSend(props) {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <line x1="10" y1="15.5" x2="10" y2="4.5" />
      <polyline points="5,9.5 10,4.5 15,9.5" />
    </svg>
  );
}

export function IconPaperclip(props) {
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M14.5 7.6 8.4 13.7a2.6 2.6 0 0 1-3.7-3.7l6.5-6.5a1.8 1.8 0 0 1 2.6 2.6L7.3 12.6a1 1 0 0 1-1.4-1.4l5.6-5.6" />
    </svg>
  );
}

export function IconStar(props) {
  return (
    <svg viewBox="0 0 20 20" width="11" height="11" fill="currentColor" {...props}>
      <path d="M10 2.2l2.24 4.86 5.26.6-3.9 3.68 1.06 5.26L10 13.9l-4.66 2.7 1.06-5.26-3.9-3.68 5.26-.6z" />
    </svg>
  );
}

export function IconCopy(props) {
  return (
    <svg viewBox="0 0 20 20" width="13" height="13" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <rect x="7" y="7" width="10" height="10" rx="1.8" />
      <path d="M13 7V4.8A1.8 1.8 0 0 0 11.2 3H4.8A1.8 1.8 0 0 0 3 4.8v6.4A1.8 1.8 0 0 0 4.8 13H7" />
    </svg>
  );
}

export function IconDownload(props) {
  return (
    <svg viewBox="0 0 20 20" width="13" height="13" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <line x1="10" y1="3.5" x2="10" y2="12.5" />
      <polyline points="6,9 10,13 14,9" />
      <line x1="4.5" y1="16.3" x2="15.5" y2="16.3" />
    </svg>
  );
}

export function IconGitCommit(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" {...props}>
      <circle cx="10" cy="10" r="3" />
      <line x1="1.5" y1="10" x2="7" y2="10" />
      <line x1="13" y1="10" x2="18.5" y2="10" />
    </svg>
  );
}

export function IconGitPullRequest(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="6" cy="5" r="2" />
      <circle cx="6" cy="15" r="2" />
      <circle cx="14" cy="5" r="2" />
      <line x1="6" y1="7" x2="6" y2="13" />
      <path d="M14 7v3a3 3 0 0 1-3 3h-3" />
    </svg>
  );
}

export function IconSpinner(props) {
  return (
    <svg viewBox="0 0 20 20" width="14" height="14" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" className="chat-step-spin" {...props}>
      <path d="M17 10a7 7 0 1 1-2.1-5" />
    </svg>
  );
}
