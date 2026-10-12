// Google Gemini 로고 마크 — path data 출처: Simple Icons(simple-icons/simple-icons, CC0).
// Gemini 계열 모델 아이콘. 실제 브랜드처럼 파랑→보라 그라디언트를 입힌다.
import { useId } from "react";

export default function GeminiMark({ size = 16, ...props }) {
  const gradientId = `gemini-mark-gradient-${useId()}`;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" {...props}>
      <defs>
        <linearGradient id={gradientId} x1="0" y1="24" x2="24" y2="0">
          <stop offset="0%" stopColor="#4285F4" />
          <stop offset="50%" stopColor="#9168C0" />
          <stop offset="100%" stopColor="#D96570" />
        </linearGradient>
      </defs>
      <path
        fill={`url(#${gradientId})`}
        d="M11.04 19.32Q12 21.51 12 24q0-2.49.93-4.68.96-2.19 2.58-3.81t3.81-2.55Q21.51 12 24 12q-2.49 0-4.68-.93a12.3 12.3 0 0 1-3.81-2.58 12.3 12.3 0 0 1-2.58-3.81Q12 2.49 12 0q0 2.49-.96 4.68-.93 2.19-2.55 3.81a12.3 12.3 0 0 1-3.81 2.58Q2.49 12 0 12q2.49 0 4.68.96 2.19.93 3.81 2.55t2.55 3.81"
      />
    </svg>
  );
}
