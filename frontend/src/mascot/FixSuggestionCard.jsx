import { useEffect, useState } from "react";
import { fetchFix, generateFix, createFixPr } from "../api";
import DiffView from "./DiffView";

const POLL_INTERVAL_MS = 3000;

// finding 1건의 수정 제안 카드 — 펼칠 때 처음이면 generate-fix를 호출하고
// (이미 요청된 적 있으면 GET만으로 충분), status==='pending'인 동안만 짧게
// 폴링한다(fix_ready WS 알림도 오지만, 카드를 펼친 채 기다리는 경우를 놓치지
// 않도록 폴링을 병행한다).
export default function FixSuggestionCard({ findingId }) {
  const [expanded, setExpanded] = useState(false);
  const [fix, setFix] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!expanded) return undefined;
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError(null);
      try {
        let current = await fetchFix(findingId);
        if (cancelled) return;
        if (!current) {
          await generateFix(findingId);
          if (cancelled) return;
          current = await fetchFix(findingId);
        }
        if (!cancelled) setFix(current);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [expanded, findingId]);

  useEffect(() => {
    if (!expanded || fix?.status !== "pending") return undefined;
    const timer = setInterval(async () => {
      try {
        const current = await fetchFix(findingId);
        if (current) setFix(current);
      } catch {
        // 다음 폴링에서 재시도
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [expanded, fix?.status, findingId]);

  const handleCreatePr = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await createFixPr(findingId);
      setFix((prev) => (prev ? { ...prev, status: "pr_created", pr_url: result.pr_url } : prev));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fix-suggestion-card">
      <button type="button" className="fix-suggestion-toggle" onClick={() => setExpanded((v) => !v)}>
        {expanded ? "▾" : "▸"} 수정 제안 보기
      </button>
      {expanded && (
        <div className="fix-suggestion-body">
          {loading && !fix && <p className="muted">불러오는 중...</p>}
          {error && <p className="fix-suggestion-error">{error}</p>}
          {fix?.status === "pending" && <p className="muted">수정 코드를 생성하는 중...</p>}
          {fix?.status === "failed" && <p className="fix-suggestion-error">생성 실패: {fix.error}</p>}
          {(fix?.status === "ready" || fix?.status === "pr_created") && (
            <>
              <p>{fix.summary}</p>
              <DiffView diff={fix.diff} />
              {fix.status === "ready" ? (
                <div className="fix-suggestion-actions">
                  <button type="button" onClick={handleCreatePr} disabled={loading}>
                    {loading ? "생성 중..." : "PR 생성"}
                  </button>
                </div>
              ) : (
                <p>
                  <a href={fix.pr_url} target="_blank" rel="noreferrer">PR 보기 →</a>
                </p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
