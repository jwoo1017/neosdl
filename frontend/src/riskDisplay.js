// Risk UI 표시 헬퍼 — score→등급, representation/차원 라벨. 백엔드 app/risk/schema.py 와 맞춘다.

export const DIMENSION_LABELS = {
  business_ip: "Business/IP",
  source_sensitivity: "소스 민감도",
  security: "보안",
  pii: "PII",
  secret: "비밀정보",
};

// app/risk/schema.py REPRESENTATIONS 순서와 동일 (낮을수록 원문에 가까움)
export const REPRESENTATIONS = [
  "RAW", "SANITIZED", "MASKED", "PSEUDOCODE", "STRUCTURED", "SUMMARY", "INTERNAL_ONLY", "BLOCK",
];

export const REPRESENTATION_LABELS = {
  RAW: "원문 그대로",
  SANITIZED: "정리 후",
  MASKED: "마스킹 후",
  PSEUDOCODE: "의사코드",
  STRUCTURED: "구조만",
  SUMMARY: "요약만",
  INTERNAL_ONLY: "내부 전용",
  BLOCK: "전달 불가",
};

// 표현 사다리 → pill 색 클래스 (App.css). 3구간: 느슨/중간/제한.
export function exposurePillClass(rep) {
  const rank = REPRESENTATIONS.indexOf(rep);
  if (rank <= 0) return "pill-exposure-raw";
  if (rank <= 3) return "pill-exposure-sanitized";
  if (rank <= 5) return "pill-exposure-structured";
  return "pill-exposure-internal";
}

// 점수(0~1) → 등급 배지 클래스 + 라벨
export function scoreBucket(score) {
  if (score >= 0.75) return { cls: "badge-risk-critical", label: "CRITICAL" };
  if (score >= 0.55) return { cls: "badge-risk-high", label: "HIGH" };
  if (score >= 0.3) return { cls: "badge-risk-medium", label: "MEDIUM" };
  if (score > 0) return { cls: "badge-risk-low", label: "LOW" };
  return { cls: "badge-risk-none", label: "–" };
}

// risk 벡터에서 가장 높은 차원 (score, dim)
export function topDimension(risk) {
  let best = { dim: null, score: -1, confidence: 0 };
  for (const [dim, entry] of Object.entries(risk || {})) {
    if ((entry?.score ?? 0) > best.score) best = { dim, score: entry.score, confidence: entry.confidence };
  }
  return best;
}
