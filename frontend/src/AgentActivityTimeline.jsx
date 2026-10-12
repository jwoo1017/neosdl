import { useEffect, useRef, useState } from "react";
import { TOOL_LABELS } from "./toolLabels";
import {
  IconSearch, IconWrench, IconCheck, IconWarn, IconPencil, IconList,
  IconChevronDown, IconSpinner,
} from "./chatIcons";

// 히스토리 메시지(m)에서 저장된 AgentEvent 목록을 꺼낸다. 백엔드는
// evidence.agent_events에 저장하지만(app/chat.py), 마이그레이션 이전 메시지는
// 이 키 자체가 없을 수 있어 빈 배열로 폴백한다 — 그 경우 호출부가 기존
// tools_used 뱃지로 대체 렌더한다.
export function historyEvents(message) {
  return message.agent_events || message.evidence?.agent_events || [];
}

// id별로 마지막 상태만 남긴다(순서는 최초 등장 순서를 유지). 실시간
// 스트리밍은 ws.onmessage 쪽에서 이미 id로 upsert해 두지만, 저장된 히스토리
// (app/chat.py의 collected_events)는 running/done을 모두 그대로 append한
// 원본 로그라서 여기서도 반드시 다시 병합해야 한다 — 안 그러면 완료된
// 메시지를 다시 열 때마다 "running"으로 멈춘 옛 항목이 그대로 남아 경과
// 시간이 끝없이 올라가고, 같은 단계가 두 줄로 중복 표시된다.
function dedupeEvents(events) {
  const order = [];
  const byId = new Map();
  for (const event of events) {
    if (!byId.has(event.id)) order.push(event.id);
    byId.set(event.id, { ...(byId.get(event.id) || {}), ...event });
  }
  return order.map((id) => byId.get(id));
}

function elapsedSeconds(event, now) {
  if (!event.started_at) return null;
  const start = new Date(event.started_at).getTime();
  const end = event.finished_at ? new Date(event.finished_at).getTime() : now;
  return Math.max(0, Math.round((end - start) / 1000));
}

// running이면 항상 스피너로 덮어쓰고, 아니면 타입/성공여부에 맞는 정적 아이콘.
function rowIcon(event) {
  if (event.status === "running") return IconSpinner;
  if (event.type === "tool_result") return event.status === "error" ? IconWarn : IconCheck;
  if (event.status === "error") return IconWarn;
  switch (event.type) {
    case "plan": return IconList;
    case "tool_call": return IconWrench;
    case "final_response": return IconPencil;
    case "thinking":
    default:
      return IconSearch;
  }
}

// 백엔드(app/agent_events.py tool_target_text)는 이제 사람이 읽을 문구를 보내지만,
// 마이그레이션 이전에 저장된 이벤트는 target에 raw JSON(`{"keywords":[...]}`)이 들어
// 있다 — 그 경우 값만 뽑아 콤마로 잇고(키/중괄호 노출 방지), 못 살리면 숨긴다.
function formatTarget(target) {
  if (!target) return null;
  const text = String(target).trim();
  if (text[0] !== "{" && text[0] !== "[") return text;
  try {
    const parsed = JSON.parse(text);
    const values = Object.values(parsed).flat().filter((v) => v != null && v !== "");
    return values.length > 0 ? values.join(", ") : null;
  } catch {
    return null;
  }
}

function rowHeading(event) {
  switch (event.type) {
    case "thinking": return "생각하는 중";
    case "plan": return "계획";
    // tool_call과 tool_result는 같은 id로 병합되므로(dedupeEvents) 결과 행에도
    // tool 필드가 남아 있다 — 어느 도구가 돌았는지 감추지 않고 라벨로 보여준다.
    case "tool_call":
    case "tool_result":
      return TOOL_LABELS[event.tool] || event.tool || (event.type === "tool_result" ? "결과" : "도구 호출");
    case "final_response": return "답변 작성";
    default: return event.type;
  }
}

function AgentEventRow({ event, now }) {
  const Icon = rowIcon(event);
  const secs = elapsedSeconds(event, now);
  const rowClass = [
    "chat-activity-row",
    event.status === "running" ? "chat-activity-row-active" : "",
    event.status === "error" ? "chat-activity-row-error" : "",
  ].filter(Boolean).join(" ");
  const isTool = event.type === "tool_call" || event.type === "tool_result";
  const targetText = isTool ? formatTarget(event.target) : null;
  const planSteps = event.type === "plan" ? (event.plan_steps || []) : [];

  return (
    <div className={rowClass}>
      <Icon className="chat-activity-row-icon" />
      <div className="chat-activity-row-body">
        <div className="chat-activity-row-head">
          {rowHeading(event)}
          {secs != null && <span className="chat-activity-row-elapsed"> · {secs}초</span>}
        </div>
        {targetText && (
          <div className="chat-activity-row-target">{targetText}</div>
        )}
        {planSteps.length > 0 && (
          // ol이 번호를 매기므로 스텝 텍스트 앞의 "1. " 같은 접두 번호는 떼어낸다
          // (백엔드가 이미 제거하지만, 마이그레이션 이전에 저장된 계획 대비).
          <ol className="chat-activity-plan-list">
            {planSteps.map((step, i) => (
              <li key={i}>{String(step).replace(/^\s*\d+[.)]\s*/, "")}</li>
            ))}
          </ol>
        )}
        {event.summary && !(event.type === "plan" && planSteps.length > 0) && (
          <div className="chat-activity-row-summary">{event.summary}</div>
        )}
      </div>
    </div>
  );
}

// 실시간(live=true)이면 펼쳐진 채로 시작해 진행 상황을 그대로 보여주다가,
// 최종 답변 작성이 시작되면(final_response 이벤트 등장) 한 줄 요약으로 스스로
// 접힌다 — 단, 사용자가 이미 수동으로 펼치거나 접었다면 그 선택을 존중해 다시
// 강제로 접지 않는다. 히스토리(live 미지정)는 접힌 채로 시작.
export default function AgentActivityTimeline({ events: rawEvents, live }) {
  const [expanded, setExpanded] = useState(!!live);
  const [now, setNow] = useState(() => Date.now());
  const userToggledRef = useRef(false);

  const events = dedupeEvents(rawEvents);
  const hasRunning = events.some((e) => e.status === "running");
  // 계획·도구 호출 사이에는 잠깐 running 이벤트가 하나도 없는 순간이 생기므로
  // "running 없음"으로 접으면 루프 도중에 성급하게 접힌다. 최종 답변 단계
  // (final_response)가 시작될 때까지는 펼친 채로 둔다.
  const answerStarted = events.some((e) => e.type === "final_response");

  useEffect(() => {
    if (!hasRunning) return undefined;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [hasRunning]);

  useEffect(() => {
    if (live && answerStarted && !userToggledRef.current) {
      setExpanded(false);
    }
  }, [live, answerStarted]);

  if (events.length === 0) return null;

  const toggle = () => {
    userToggledRef.current = true;
    setExpanded((v) => !v);
  };

  const start = events[0]?.started_at ? new Date(events[0].started_at).getTime() : null;
  const lastEvent = events[events.length - 1];
  const end = hasRunning ? now : (lastEvent?.finished_at ? new Date(lastEvent.finished_at).getTime() : now);
  const totalSecs = start != null ? Math.max(0, Math.round((end - start) / 1000)) : null;

  const summaryText = `${hasRunning ? "에이전트 작업 중" : "에이전트 작업 완료"} · ${events.length}단계` +
    (totalSecs != null ? ` · ${totalSecs}초` : "");

  return (
    <div className="chat-activity-timeline">
      <button type="button" className="chat-activity-summary" onClick={toggle}>
        <IconChevronDown className={`chat-activity-caret${expanded ? " chat-activity-caret-open" : ""}`} />
        {summaryText}
      </button>
      <div className={`chat-activity-list-wrap${expanded ? " chat-activity-list-wrap-open" : ""}`}>
        <div className="chat-activity-list">
          {events.map((event) => <AgentEventRow key={event.id} event={event} now={now} />)}
        </div>
      </div>
    </div>
  );
}
