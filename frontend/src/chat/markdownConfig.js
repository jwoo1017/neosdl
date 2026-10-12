// 채팅 답변 마크다운 렌더링 설정 — react-markdown 플러그인/허용 태그/스트리밍 보정.
//
// 보안: raw HTML은 파싱하지 않는다(react-markdown 기본값). rehype-raw / rehype-sanitize를
// 일부러 넣지 않았다 — raw HTML이 이미 불활성이고, 아래 ALLOWED_ELEMENTS allow-list +
// urlTransform 으로 남은 표면(javascript:/data: URL 등)을 막는다. hljs 클래스는 유지돼야
// 하이라이팅이 되므로 rehype-sanitize 스키마를 늘리는 비용을 피한다.

import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

import javascript from "highlight.js/lib/languages/javascript";
import typescript from "highlight.js/lib/languages/typescript";
import python from "highlight.js/lib/languages/python";
import java from "highlight.js/lib/languages/java";
import go from "highlight.js/lib/languages/go";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import json from "highlight.js/lib/languages/json";
import yaml from "highlight.js/lib/languages/yaml";
import bash from "highlight.js/lib/languages/bash";
import dockerfile from "highlight.js/lib/languages/dockerfile";
import xml from "highlight.js/lib/languages/xml";
import css from "highlight.js/lib/languages/css";
import diff from "highlight.js/lib/languages/diff";
import plaintext from "highlight.js/lib/languages/plaintext";

// 큐레이션된 언어만 등록한다 — `import "highlight.js"`(약 40개 언어 일괄) 금지.
const LANGUAGES = {
  javascript, typescript, python, java, go, rust, sql, json,
  yaml, bash, dockerfile, xml, css, diff, plaintext,
};

export const REMARK = [remarkGfm];

// 스트리밍이 끝난 뒤에만 하이라이팅한다(아래 MarkdownAnswer 참고).
export const REHYPE_HIGHLIGHT = [
  [rehypeHighlight, { detect: true, languages: LANGUAGES, ignoreMissing: true }],
];
export const REHYPE_PLAIN = [];

// GFM 태스크리스트(input/span)와 hljs span 까지 포함한 allow-list.
export const ALLOWED_ELEMENTS = [
  "p", "br", "strong", "em", "del", "a",
  "ul", "ol", "li", "blockquote",
  "h1", "h2", "h3", "h4", "h5", "h6",
  "code", "pre", "hr", "img", "input", "span",
  "table", "thead", "tbody", "tr", "th", "td",
];

// http/https/mailto/앵커/상대경로만 허용, 그 외(javascript:, data: 등)는 제거.
export function urlTransform(url) {
  const u = String(url || "");
  if (u.startsWith("#") || u.startsWith("/")) return u;
  try {
    const proto = new URL(u, "https://x.invalid").protocol;
    return ["http:", "https:", "mailto:"].includes(proto) ? u : "";
  } catch {
    return "";
  }
}

// 스트리밍 도중 아직 닫히지 않은 코드펜스/인라인 백틱을 임시로 닫아 준다 —
// 안 그러면 열린 ``` 이후 나머지 답변이 통째로 코드로 렌더된다. 보수적으로:
// 펜스가 홀수면 ``` 를 붙이고, 펜스가 균형일 때만 마지막 줄의 미종료 백틱을 닫는다.
export function closeOpenFence(text) {
  const fenceCount = (text.match(/^```/gm) || []).length;
  let out = text;
  if (fenceCount % 2 === 1) {
    out += "\n```";
  } else {
    const lastLine = out.slice(out.lastIndexOf("\n") + 1);
    if ((lastLine.match(/`/g) || []).length % 2 === 1) out += "`";
  }
  return out;
}

// 펜스에 언어 표기가 없고 본문이 SQL 키워드로 시작하면 sql 로 추정.
const SQL_RE = /^\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|DROP)\b/im;
export function detectSqlLang(body, className) {
  if (/language-/.test(className || "")) return null;
  return SQL_RE.test(body || "") ? "sql" : null;
}

// hast 노드에서 텍스트만 이어붙인다(복사 버튼용 원본 코드 추출 — hljs span 무시).
export function hastToText(node) {
  if (!node) return "";
  if (node.type === "text") return node.value || "";
  if (typeof node.value === "string") return node.value;
  return (node.children || []).map(hastToText).join("");
}
