// "새 채팅 만들기" 아이콘 — 말풍선 아웃라인 + 안에 "+"를 직접 그린 SVG.
export default function NewChatIcon({ size = 16, ...props }) {
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
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9A1.5 1.5 0 0 1 18.5 16H9.8L6 19.5V16H5.5A1.5 1.5 0 0 1 4 14.5z" />
      <path d="M12 7.7v5.6" />
      <path d="M9.2 10.5h5.6" />
    </svg>
  );
}
