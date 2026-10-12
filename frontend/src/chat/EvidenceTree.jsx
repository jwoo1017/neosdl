import { useState } from "react";
import CodeViewer from "../CodeViewer";
import { DIMENSION_LABELS, scoreBucket } from "../riskDisplay";

// Risk 판단의 근거를 kind별로 접이식 트리로 보여준다(§5, §16 역추적).
//   [kind 그룹] ▸ evidence 항목 (차원·신뢰도·설명) ▸ 위치 클릭 → CodeViewer
// props: detail = evidence_detail[] (app/risk/evidence.py Evidence.to_dict())
export default function EvidenceTree({ detail, scanId }) {
  const [code, setCode] = useState(null); // { path, line } | null

  if (!detail || detail.length === 0) {
    return <p className="muted">연결된 근거가 없습니다.</p>;
  }

  const groups = {};
  for (const e of detail) (groups[e.kind] ||= []).push(e);

  return (
    <div className="evidence-tree">
      {Object.entries(groups).map(([kind, items]) => (
        <details key={kind} className="evidence-tree-group" open={items.length <= 3}>
          <summary>
            <span className="evidence-tree-kind">{kind}</span>
            <span className="muted"> · {items.length}</span>
          </summary>
          <ul className="evidence-tree-list">
            {items.map((e, i) => {
              const b = scoreBucket(e.weight * e.confidence);
              const loc = e.location || {};
              return (
                <li key={i} className="evidence-tree-item">
                  <div className="evidence-tree-item-head">
                    {e.dimension !== "none" && (
                      <span className={`badge ${b.cls}`}>{DIMENSION_LABELS[e.dimension] || e.dimension}</span>
                    )}
                    <span className="muted">신뢰도 {Math.round(e.confidence * 100)}%</span>
                  </div>
                  <div className="evidence-tree-desc">{e.description}</div>
                  {(loc.path || e.related) && (
                    <div className="evidence-tree-meta muted">
                      {loc.path && (
                        <button
                          className="evidence-tree-loc"
                          onClick={() => scanId && setCode({ path: loc.path, line: loc.line })}
                        >
                          {loc.path}{loc.line ? `:${loc.line}` : ""}
                        </button>
                      )}
                      {e.related && <span> → {e.related.split("#").pop()}</span>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      ))}

      {code && (
        <CodeViewer scanId={scanId} path={code.path} line={code.line} onClose={() => setCode(null)} />
      )}
    </div>
  );
}
