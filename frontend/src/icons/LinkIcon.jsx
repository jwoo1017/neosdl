// "링크 복사" 메뉴 항목용 체인링크 아이콘 — 직접 그린 SVG.
export default function LinkIcon({ size = 16, ...props }) {
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
      <path d="M9.5 14.5 14.5 9.5" />
      <path d="M11 6.2 12.8 4.4a3.4 3.4 0 0 1 4.8 4.8L15.8 11" />
      <path d="M13 17.8 11.2 19.6a3.4 3.4 0 0 1-4.8-4.8L8.2 13" />
    </svg>
  );
}
