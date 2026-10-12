import { useEffect, useMemo, useState } from "react";
import { fetchRepositoryActivityFeed } from "../api";
import { useMascot } from "../MascotContext";
import { IconCheck, IconGitCommit, IconGitPullRequest, IconSearch, IconWarn, IconWrench } from "../chatIcons";
import FixSuggestionCard from "./FixSuggestionCard";

const ICONS = {
  push_received: IconGitCommit,
  pull_request: IconGitPullRequest,
  scan_started: IconSearch,
  scan_completed: IconCheck,
  security_snapshot: IconWarn,
  fix_ready: IconWrench,
  pr_created: IconGitPullRequest,
};

function normalizeLiveEvent(event) {
  const at = new Date().toISOString();
  switch (event.type) {
    case "push_received":
      return { type: event.type, at, title: "새 커밋이 푸시됨", ref: event.commit_sha?.slice(0, 8), meta: event };
    case "scan_started":
      return { type: event.type, at, title: "스캔 시작", ref: null, meta: event };
    case "scan_completed":
      return {
        type: event.type, at,
        title: event.findings_count > 0 ? `취약점 ${event.findings_count}건 발견` : "분석 완료 — 취약점 없음",
        ref: null, meta: event,
      };
    case "fix_ready":
      return { type: event.type, at, title: "수정 제안 준비됨", ref: null, meta: event };
    case "pr_created":
      return { type: event.type, at, title: "PR 생성됨", ref: null, url: event.pr_url, meta: event };
    default:
      return { type: event.type, at, title: event.type, ref: null, meta: event };
  }
}

export default function ActivityStoryBar({ repositoryId }) {
  const { eventsByRepo } = useMascot();
  const [feed, setFeed] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!repositoryId) return;
    setFeed(null);
    setError(null);
    fetchRepositoryActivityFeed(repositoryId)
      .then(setFeed)
      .catch((err) => setError(err.message));
  }, [repositoryId]);

  const liveEvents = useMemo(
    () => (eventsByRepo[repositoryId] || []).map(normalizeLiveEvent),
    [eventsByRepo, repositoryId],
  );

  const items = useMemo(() => [...liveEvents, ...(feed || [])], [liveEvents, feed]);

  if (error) return <p className="fix-suggestion-error">{error}</p>;
  if (feed === null) return <p className="muted">활동 내역을 불러오는 중...</p>;
  if (items.length === 0) return <p className="muted">아직 활동 내역이 없습니다.</p>;

  return (
    <ul className="activity-story-bar">
      {items.map((item, i) => {
        const Icon = ICONS[item.type] || IconGitCommit;
        return (
          // eslint-disable-next-line react/no-array-index-key
          <li key={`${item.type}-${item.at}-${i}`} className={`activity-story-item activity-story-${item.type}`}>
            <span className="activity-story-icon"><Icon /></span>
            <div className="activity-story-body">
              <div className="activity-story-title">
                {item.url ? (
                  <a href={item.url} target="_blank" rel="noreferrer">{item.title}</a>
                ) : (
                  item.title
                )}
                {item.ref && <span className="activity-story-ref">{item.ref}</span>}
              </div>
              <time className="activity-story-time">{new Date(item.at).toLocaleString()}</time>
              {item.type === "fix_ready" && item.meta?.finding_id && (
                <FixSuggestionCard findingId={item.meta.finding_id} />
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
