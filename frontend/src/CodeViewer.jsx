import { useEffect, useRef, useState } from "react";
import { fetchFileContent } from "./api";

/**
 * 그래프 ↔ 코드 동기화(읽기 전용, 한 방향만) — 노드를 클릭하면 이 패널이
 * 그 심볼의 실제 소스를 보여주고 해당 줄로 스크롤한다. 문법 강조는 없다
 * (라이브러리 추가는 범위 밖) — 줄 번호 + 대상 줄 하이라이트만으로도
 * "이 관계가 실제 코드 어디서 왔는가"를 확인하는 목적은 충분히 만족한다.
 */
export default function CodeViewer({ scanId, path, line, onClose }) {
  const [content, setContent] = useState(null);
  const [error, setError] = useState(null);
  const lineRef = useRef(null);

  useEffect(() => {
    if (!scanId || !path) return;
    let cancelled = false;
    setContent(null);
    setError(null);
    fetchFileContent(scanId, path)
      .then((r) => {
        if (!cancelled) setContent(r.content);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scanId, path]);

  useEffect(() => {
    if (lineRef.current) {
      lineRef.current.scrollIntoView({ block: "center" });
    }
  }, [content, line]);

  return (
    <div className="code-viewer">
      <div className="code-viewer-header">
        <span className="code-viewer-path">{path}{line ? ` · L${line}` : ""}</span>
        <button className="graph-search-close" onClick={onClose}>×</button>
      </div>
      <div className="code-viewer-body">
        {error && <div className="graph-search-empty">불러오지 못했습니다: {error}</div>}
        {content === null && !error && <div className="graph-search-empty">불러오는 중...</div>}
        {content !== null && (
          <pre>
            {content.split("\n").map((text, i) => {
              const lineNo = i + 1;
              const isTarget = lineNo === line;
              return (
                <div
                  key={lineNo}
                  ref={isTarget ? lineRef : undefined}
                  className={isTarget ? "code-line code-line-target" : "code-line"}
                >
                  <span className="code-line-no">{lineNo}</span>
                  <span className="code-line-text">{text}</span>
                </div>
              );
            })}
          </pre>
        )}
      </div>
    </div>
  );
}
