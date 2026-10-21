import { useId } from "react";

// 채팅의 AI 답변 쪽 아바타 — 둥근 사각형 배경 + 반짝임(별) 두 개.
export default function AiAvatarIcon({ size = 32 }) {
  const gid = useId();
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" role="img" aria-label="NeoSDL AI">
      <title>NeoSDL AI</title>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#3fb8a9" />
          <stop offset="100%" stopColor="#2563eb" />
        </linearGradient>
      </defs>
      <rect x="0" y="0" width="40" height="40" rx="11" fill={`url(#${gid})`} />
      {/* 큰 별 */}
      <path
        d="M18 9 C19 15 21 17 27 18 C21 19 19 21 18 27 C17 21 15 19 9 18 C15 17 17 15 18 9 Z"
        fill="#ffffff"
      />
      {/* 작은 별 */}
      <path
        d="M29 23 C29.5 26 30.5 27 33 27.5 C30.5 28 29.5 29 29 32 C28.5 29 27.5 28 25 27.5 C27.5 27 28.5 26 29 23 Z"
        fill="#ffffff"
        opacity="0.85"
      />
    </svg>
  );
}
