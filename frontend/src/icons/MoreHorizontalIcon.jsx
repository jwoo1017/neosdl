// "더보기" 아이콘 — 가로로 놓인 점 3개. 세로 배치인 icons/MoreIcon.jsx와는
// 다른 자리(문서/대화 헤더의 가로 툴바)에 쓴다.
export default function MoreHorizontalIcon({ size = 16, ...props }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" stroke="none" {...props}>
      <circle cx="5" cy="12" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="19" cy="12" r="1.8" />
    </svg>
  );
}
