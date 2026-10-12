import AgentActivityTimeline from "../AgentActivityTimeline";
import { TOOL_LABELS } from "../toolLabels";
import EvidencePanel from "./EvidencePanel";
import MarkdownAnswer from "./MarkdownAnswer";

// 채팅 메시지 한 줄. 주요 LLM 채팅 UI 관례를 따른다:
//  - 사용자 메시지: 오른쪽 정렬 버블
//  - 어시스턴트 답변: 버블 없이 페이지 배경 위에 마크다운 그대로(넓은 폭)
// 사용자 입력은 절대 마크다운을 태우지 않는다. 진행 타임라인은 기존 컴포넌트 재사용.
export default function ChatMessage({
  role, content, events, toolsUsed, live, streaming, pending, risk, onOpenRiskDetail,
}) {
  const hasEvents = events && events.length > 0;

  if (role === "user") {
    return (
      <div className="chat-message chat-message-user">
        <div className="chat-message-content">
          <div className="chat-message-bubble">{content}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-message chat-message-assistant">
      <div className="chat-message-content">
        {hasEvents ? (
          <AgentActivityTimeline events={events} live={live} />
        ) : (
          toolsUsed && toolsUsed.length > 0 && (
            <div className="chat-message-tools">
              {toolsUsed.map((t, i) => (
                <span key={i} className="badge">{TOOL_LABELS[t.name] || t.name}</span>
              ))}
            </div>
          )
        )}

        {pending ? (
          <div className="chat-typing" aria-label="생각하는 중">
            <span /><span /><span />
          </div>
        ) : content ? (
          <div className="chat-answer">
            <MarkdownAnswer content={content} streaming={streaming} />
          </div>
        ) : null}

        {risk && !pending && <EvidencePanel risk={risk} onOpenDetail={onOpenRiskDetail} />}
      </div>
    </div>
  );
}
