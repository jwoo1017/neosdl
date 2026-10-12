// Anthropic 로고 마크 — path data 출처: Simple Icons(simple-icons/simple-icons, CC0).
// Claude 계열 모델(Opus/Sonnet/Fable) 공용 아이콘으로 쓴다.
export default function AnthropicMark({ size = 16, color = "#D97757", ...props }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={color} {...props}>
      <path d="M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7.0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z" />
    </svg>
  );
}
