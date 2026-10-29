import { useId } from "react";

// AI 작업 중 표시 — AI 아바타(청록→파랑 그라데이션 + 반짝임 별)를 그대로 살린 스피너.
// 바깥 고리 두 개가 서로 반대로 돌고, 가운데 별이 맥동하고, 작은 별이 궤도를 돈다.
// label 을 주면 옆에 반짝이는(shimmer) 문구를 붙인다. 애니메이션은 App.css(.ai-spinner*).
export default function AiSpinner({ size = 28, label }) {
  const gid = useId();
  const star = "M18 9 C19 15 21 17 27 18 C21 19 19 21 18 27 C17 21 15 19 9 18 C15 17 17 15 18 9 Z";
  return (
    <span className="ai-spinner" role="status" aria-label={label || "AI 작업 중"}>
      <svg width={size} height={size} viewBox="0 0 40 40" fill="none" aria-hidden="true">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor="#3fb8a9" />
            <stop offset="100%" stopColor="#2563eb" />
          </linearGradient>
        </defs>
        <circle cx="20" cy="20" r="17" stroke={`url(#${gid})`} strokeOpacity="0.15" strokeWidth="2.5" />
        <circle className="ai-spinner-arc" cx="20" cy="20" r="17" stroke={`url(#${gid})`}
          strokeWidth="2.5" strokeLinecap="round" strokeDasharray="34 73" />
        <circle className="ai-spinner-arc-inner" cx="20" cy="20" r="12.5" stroke={`url(#${gid})`}
          strokeOpacity="0.55" strokeWidth="1.5" strokeLinecap="round" strokeDasharray="14 65" />
        <g className="ai-spinner-core">
          <path d={star} fill={`url(#${gid})`} transform="translate(20 20) scale(0.62) translate(-18 -18)" />
        </g>
        <g className="ai-spinner-orbit">
          <path d={star} fill="#3fb8a9" transform="translate(20 3) scale(0.22) translate(-18 -18)" />
        </g>
      </svg>
      {label && <span className="ai-spinner-label">{label}</span>}
    </span>
  );
}
