// "자동" 모델 버튼 아이콘 — 속도/노력/비용을 조절하는 다이얼 느낌의 이퀄라이저.
// 아이콘 라이브러리 없이 직접 그린 SVG, stroke="currentColor"로 텍스트 색을 물려받는다.
export default function AutoIcon({ size = 16, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <line x1="5" y1="7" x2="5" y2="19" />
      <circle cx="5" cy="10.5" r="1.7" fill="currentColor" stroke="none" />
      <line x1="12" y1="5" x2="12" y2="19" />
      <circle cx="12" cy="15" r="1.7" fill="currentColor" stroke="none" />
      <line x1="19" y1="7" x2="19" y2="19" />
      <circle cx="19" cy="9" r="1.7" fill="currentColor" stroke="none" />
    </svg>
  );
}
