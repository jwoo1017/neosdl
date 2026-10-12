// 채팅 입력창 모델 드롭다운.
//
// 모델 목록은 백엔드 `GET /models` 가 돌려준다 — 이 값은 LiteLLM 게이트웨이가
// 실제로 서빙 중인 모델(= litellm/config.yaml + 채워진 API 키)에서 나온다.
// 여기서 고른 id 는 질문 전송 시 그대로 실려(app/chat.py), 게이트웨이가 해당
// provider 호출 규격으로 번역한다. 즉 이 드롭다운은 더 이상 장식이 아니다.
//
// 이 파일은 아이콘/그룹 순서 같은 "표시 메타데이터"와, 백엔드 응답이 오기 전
// (또는 실패 시) 쓸 폴백 목록만 들고 있는다.
import AnthropicMark from "./icons/AnthropicMark";
import OpenAIMark from "./icons/OpenAIMark";
import GeminiMark from "./icons/GeminiMark";
import KimiMark from "./icons/KimiMark";
import AutoIcon from "./icons/AutoIcon";

export const AUTO_MODEL = {
  id: "auto",
  label: "자동",
  description: "속도, 노력, 비용을 균형 있게 조절합니다",
  Icon: AutoIcon,
};

// provider 문자열(백엔드 /models 응답의 provider 필드) → 아이콘 컴포넌트.
const PROVIDER_ICON = {
  anthropic: AnthropicMark,
  openai: OpenAIMark,
  gemini: GeminiMark,
  kimi: KimiMark,
};

export function iconForProvider(provider) {
  return PROVIDER_ICON[provider] || AutoIcon;
}

// 그룹 표시 순서. /models 응답의 group 값이 여기 없으면 맨 뒤 "그 외"로.
const GROUP_ORDER = ["가장 어려운 작업에 적합", "Claude", "GPT", "Gemini", "그 외", "로컬"];

// /models 응답(rows: [{id,label,provider,group,starred,available}]) → 드롭다운 그룹.
export function buildModelGroups(rows) {
  const byGroup = new Map();
  for (const r of rows || []) {
    const g = GROUP_ORDER.includes(r.group) ? r.group : "그 외";
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push({
      id: r.id,
      label: r.label || r.id,
      Icon: iconForProvider(r.provider),
      starred: !!r.starred,
      disabled: r.available === false,
      badge: r.available === false ? "키 없음" : undefined,
    });
  }
  return GROUP_ORDER.filter((g) => byGroup.has(g)).map((title) => ({ title, models: byGroup.get(title) }));
}

export function findModelOption(groups, id) {
  for (const group of groups) {
    for (const m of group.models) if (m.id === id) return m;
  }
  return null;
}

// 백엔드 응답 전/실패 시 쓰는 폴백 — 게이트웨이 기본 config.yaml 과 대략 일치.
export const FALLBACK_MODELS = [
  { id: "claude-opus-5", label: "Claude Opus 5", provider: "anthropic", group: "가장 어려운 작업에 적합", starred: true, available: true },
  { id: "gpt-5.1", label: "GPT-5.1", provider: "openai", group: "가장 어려운 작업에 적합", available: true },
  { id: "claude-sonnet-5", label: "Claude Sonnet 5", provider: "anthropic", group: "Claude", available: true },
  { id: "claude-haiku-4-5", label: "Claude Haiku 4.5", provider: "anthropic", group: "Claude", available: true },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", provider: "gemini", group: "Gemini", starred: true, available: true },
  { id: "local-qwen-coder", label: "Qwen2.5 Coder (로컬)", provider: "local", group: "로컬", available: true },
];
