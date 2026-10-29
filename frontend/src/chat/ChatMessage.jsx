import AgentActivityTimeline from "../AgentActivityTimeline";
import Avatar from "../components/Avatar";
import AiAvatarIcon from "../icons/AiAvatarIcon";
import { TOOL_LABELS } from "../toolLabels";
import { useLanguage } from "../i18n/LanguageContext";
import EvidencePanel from "./EvidencePanel";
import FlowCards from "./FlowCard";
import MarkdownAnswer from "./MarkdownAnswer";

function formatTime(iso, lang) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(lang === "en" ? "en-US" : "ko-KR", { hour: "2-digit", minute: "2-digit" });
}

// 채팅 메시지 한 줄 — 팀 채팅방 형식:
//  - 내 질문: 오른쪽 말풍선(프로필·이름 없이)
//  - 다른 팀원 질문: 왼쪽에 아바타(역할 모자) + 닉네임·시간 + 말풍선
//  - AI 답변: 왼쪽에 AI 아바타, 버블 없이 마크다운 그대로(넓은 폭)
//  - 작성자 기록이 없는 예전 질문(sql/029 이전, author=null)은 이전처럼 오른쪽 말풍선
// 사용자 입력은 절대 마크다운을 태우지 않는다. 진행 타임라인은 기존 컴포넌트 재사용.
export default function ChatMessage({
  role, content, events, toolsUsed, live, streaming, pending, risk, onOpenRiskDetail, flows, onPlayFlow,
  author, mine, createdAt,
}) {
  const hasEvents = events && events.length > 0;
  const { lang } = useLanguage();

  if (role === "user") {
    if (mine || !author) {
      // 내 질문(그리고 작성자 기록 sql/029 이전의 질문) — 오른쪽 말풍선만, 프로필·이름 없이.
      return (
        <div className="chat-message chat-message-user">
          <div className="chat-message-content">
            <div className="chat-message-bubble">{content}</div>
            {createdAt && <span className="chat-message-time chat-message-time-right">{formatTime(createdAt, lang)}</span>}
          </div>
        </div>
      );
    }
    return (
      <div className="chat-message chat-message-other">
        <Avatar profile={author} size={34} role={author.role || undefined} className="chat-message-avatar" />
        <div className="chat-message-content">
          <div className="chat-message-meta">
            <span className="chat-message-author">{author.nickname}</span>
            {author.name && author.name !== author.nickname && (
              <span className="chat-message-realname">{author.name}</span>
            )}
            {createdAt && <span className="chat-message-time">{formatTime(createdAt, lang)}</span>}
          </div>
          <div className="chat-message-bubble chat-message-bubble-other">{content}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="chat-message chat-message-assistant">
      <span className="chat-message-avatar chat-message-ai-avatar"><AiAvatarIcon size={30} /></span>
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

        {flows && !pending && !streaming && <FlowCards flows={flows} onPlay={onPlayFlow} />}
        {risk && !pending && <EvidencePanel risk={risk} onOpenDetail={onOpenRiskDetail} />}
      </div>
    </div>
  );
}
