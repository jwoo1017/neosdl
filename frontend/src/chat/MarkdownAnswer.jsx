import { memo, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { IconCopy, IconCheck, IconDownload } from "../chatIcons";
import {
  REMARK, REHYPE_HIGHLIGHT, REHYPE_PLAIN, ALLOWED_ELEMENTS,
  urlTransform, closeOpenFence, detectSqlLang, hastToText,
} from "./markdownConfig";

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 클립보드 권한/보안 컨텍스트 없음 — 조용히 무시
    }
  };
  return (
    <button type="button" className="md-code-btn" onClick={onCopy} title="복사">
      {copied ? <IconCheck /> : <IconCopy />}
    </button>
  );
}

const EXT = {
  javascript: "js", typescript: "ts", python: "py", java: "java", go: "go",
  rust: "rs", sql: "sql", json: "json", yaml: "yml", bash: "sh",
  dockerfile: "Dockerfile", xml: "xml", css: "css", diff: "diff", text: "txt",
};

function DownloadButton({ text, lang }) {
  const onDownload = () => {
    try {
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `snippet.${EXT[lang] || "txt"}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      // 다운로드 불가 환경 — 조용히 무시
    }
  };
  return (
    <button type="button" className="md-code-btn" onClick={onDownload} title="다운로드">
      <IconDownload />
    </button>
  );
}

// pre(코드블록) — 상단 바(언어 + 복사) + 하이라이트된 코드. children 은
// rehype-highlight 가 hljs span 을 심어 둔 <code> 이므로 그대로 렌더한다.
function CodeBlock({ node, children }) {
  const codeNode = node?.children?.[0];
  const rawClass = codeNode?.properties?.className;
  const className = Array.isArray(rawClass) ? rawClass.join(" ") : String(rawClass || "");
  const codeText = hastToText(codeNode);
  const match = /language-([\w-]+)/.exec(className);
  const lang = match?.[1] || detectSqlLang(codeText, className) || "text";
  return (
    <div className="md-code-block">
      <div className="md-code-bar">
        <span className="md-code-lang">{lang}</span>
        <span className="md-code-actions">
          <DownloadButton text={codeText} lang={lang} />
          <CopyButton text={codeText} />
        </span>
      </div>
      <pre className="md-code-pre">{children}</pre>
    </div>
  );
}

const COMPONENTS = {
  pre: CodeBlock,
  code({ className = "", children, ...rest }) {
    // hljs/language- 클래스가 있으면 코드블록 내부 <code> — 통과.
    if (/language-|hljs/.test(className)) {
      return <code className={className} {...rest}>{children}</code>;
    }
    return <code className="md-code-inline" {...rest}>{children}</code>;
  },
  a({ children, ...rest }) {
    return (
      <a {...rest} className="md-link" target="_blank" rel="noopener noreferrer nofollow">
        {children}
      </a>
    );
  },
  table({ children, ...rest }) {
    return <div className="md-table-wrap"><table {...rest}>{children}</table></div>;
  },
  img({ ...rest }) {
    return <img {...rest} className="md-img" loading="lazy" alt={rest.alt || ""} />;
  },
};

function MarkdownAnswerImpl({ content, streaming }) {
  const text = useMemo(
    () => (streaming ? closeOpenFence(content || "") : content || ""),
    [content, streaming]
  );
  // 스트리밍 중엔 하이라이팅을 끈다 — 80ms 마다 커지는 블록을 재토큰화하지
  // 않도록. 완료되면 REHYPE_HIGHLIGHT 로 바뀌며 색이 한 번에 스냅인.
  const rehypePlugins = streaming ? REHYPE_PLAIN : REHYPE_HIGHLIGHT;

  return useMemo(
    () => (
      <div className="md-body">
        <ReactMarkdown
          remarkPlugins={REMARK}
          rehypePlugins={rehypePlugins}
          allowedElements={ALLOWED_ELEMENTS}
          unwrapDisallowed
          urlTransform={urlTransform}
          components={COMPONENTS}
        >
          {text}
        </ReactMarkdown>
      </div>
    ),
    [text, rehypePlugins]
  );
}

export default memo(MarkdownAnswerImpl);
