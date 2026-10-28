import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  chatSocketUrl, createChatSession, createEngagement, deleteChatSession, fetchChatMessages,
  fetchChatSessions, fetchModels, fetchTeamspaceProjects, renameChatSession, requestScan,
} from "../api";
import { useProjectAnalysis } from "../analysis/useProjectAnalysis";
import AnalysisLockPanel from "../analysis/AnalysisLockPanel";
import { watchProjectAnalysis } from "../analysis/analysisWatch";
import { SpinnerRing } from "../icons/AnalysisIcons";
import { useProject } from "../ProjectContext";
import { useTabs } from "../TabsContext";
import { TOOL_LABELS } from "../toolLabels";
import { AUTO_MODEL, buildModelGroups, FALLBACK_MODELS, findModelOption } from "../modelMenu";

const MODEL_STORAGE_KEY = "neosdl.chat.model";
import Popover from "../components/Popover";
import LivePentestPanel from "../chat/LivePentestPanel";
import PlusIcon from "../icons/PlusIcon";
import CloseIcon from "../icons/CloseIcon";
import AutoIcon from "../icons/AutoIcon";
import { historyEvents } from "../AgentActivityTimeline";
import ChatMessage from "../chat/ChatMessage";
import { useMe } from "../MeContext";
import { createBackoff, isTerminalClose } from "../wsReconnect";
import { useThrottledValue } from "../chat/useThrottledValue";
import {
  IconBack, IconPaperclip, IconSend, IconStar, IconWrench, IconCheck, IconChevronDown, IconPencil, IconSearch,
} from "../chatIcons";
import NewChatIcon from "../icons/NewChatIcon";
import MoreHorizontalIcon from "../icons/MoreHorizontalIcon";
import LinkIcon from "../icons/LinkIcon";
import TrashIcon from "../icons/TrashIcon";

const MENTION_SEARCH_DEBOUNCE_MS = 250;

// "+" 메뉴 / "자동" 모델 메뉴 / 멘션·스킬 칩까지 포함한 입력창.
// mentionedProjects/forcedTool/selectedModel/attachedFiles는 부모(ProjectChatPage)
// state라 전송(send) 시 그대로 쓸 수 있게 끌어올려져 있다.
// 커서 바로 앞의 "@토큰"을 찾는다 — "@"가 문자열 맨 앞이거나 공백 뒤에
// 있고, "@" 다음부터 커서까지 공백이 없어야 아직 "멘션 입력 중"으로 본다
// (다른 데 "@"가 있거나 이미 공백으로 끝난 멘션은 트리거하지 않는다).
function findMentionTrigger(text, cursor) {
  const before = text.slice(0, cursor);
  const at = before.lastIndexOf("@");
  if (at === -1) return null;
  if (at > 0 && !/\s/.test(before[at - 1])) return null;
  const query = before.slice(at + 1);
  if (/\s/.test(query)) return null;
  return { start: at, query };
}

function ChatComposer({
  question, setQuestion, onSubmit, disabled, connected, placeholder,
  teamspaceId, currentProjectId,
  mentionedProjects, setMentionedProjects,
  forcedTool, setForcedTool,
  attachedFiles, setAttachedFiles,
  selectedModel, onSelectModel, modelGroups,
}) {
  const [openMenu, setOpenMenu] = useState(null); // null | "plus" | "model"
  const [plusView, setPlusView] = useState("root"); // "root" | "skills"
  const [inlineMention, setInlineMention] = useState(null); // {start, query} | null — 입력창의 실시간 "@" 트리거
  const [mentionResults, setMentionResults] = useState([]);
  const [mentionLoading, setMentionLoading] = useState(false);
  const [mentionHighlight, setMentionHighlight] = useState(0);
  const textareaRef = useRef(null);
  const fileInputRef = useRef(null);
  const plusBtnRef = useRef(null);
  const modelBtnRef = useRef(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [question]);

  useEffect(() => {
    if (!inlineMention || !teamspaceId) {
      setMentionResults([]);
      return undefined;
    }
    setMentionLoading(true);
    const timer = setTimeout(() => {
      fetchTeamspaceProjects(teamspaceId, inlineMention.query)
        .then((rows) => {
          setMentionResults(rows.filter((p) => p.id !== currentProjectId));
          setMentionHighlight(0);
        })
        .catch(() => setMentionResults([]))
        .finally(() => setMentionLoading(false));
    }, MENTION_SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [inlineMention?.start, inlineMention?.query, teamspaceId, currentProjectId]);

  const closeMenu = () => {
    setOpenMenu(null);
    setPlusView("root");
  };

  const togglePlusMenu = () => {
    if (openMenu === "plus") {
      closeMenu();
    } else {
      setOpenMenu("plus");
      setPlusView("root");
    }
  };

  const toggleModelMenu = () => {
    if (openMenu === "model") closeMenu();
    else setOpenMenu("model");
  };

  const closeInlineMention = () => {
    setInlineMention(null);
    setMentionResults([]);
  };

  const onQuestionChange = (e) => {
    const value = e.target.value;
    const cursor = e.target.selectionStart;
    setQuestion(value);
    setInlineMention(findMentionTrigger(value, cursor));
  };

  // "+" 메뉴에서 "프로젝트 멘션하기"를 클릭하면 커서 위치에 "@"를 끼워 넣어
  // 실시간 트리거를 그대로 재사용한다 — 검색 UI를 두 벌 만들지 않는다.
  const insertMentionTrigger = () => {
    closeMenu();
    const el = textareaRef.current;
    const cursor = el ? el.selectionStart : question.length;
    const before = question.slice(0, cursor);
    const after = question.slice(cursor);
    const needsSpace = before.length > 0 && !/\s$/.test(before);
    const insertion = `${needsSpace ? " " : ""}@`;
    const nextValue = `${before}${insertion}${after}`;
    const newCursor = before.length + insertion.length;
    setQuestion(nextValue);
    setInlineMention({ start: newCursor - 1, query: "" });
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        el.selectionStart = el.selectionEnd = newCursor;
      }
    });
  };

  const selectMention = (project) => {
    if (!inlineMention) return;
    const before = question.slice(0, inlineMention.start);
    const after = question.slice(inlineMention.start + 1 + inlineMention.query.length);
    const nextValue = `${before}${after}`;
    setQuestion(nextValue);
    setMentionedProjects((prev) => (prev.some((p) => p.id === project.id) ? prev : [...prev, project]));
    closeInlineMention();
    const el = textareaRef.current;
    requestAnimationFrame(() => {
      if (el) {
        el.focus();
        el.selectionStart = el.selectionEnd = before.length;
      }
    });
  };

  const selectSkill = (toolName) => {
    setForcedTool((prev) => (prev === toolName ? null : toolName));
    closeMenu();
  };

  const onKeyDown = (e) => {
    if (inlineMention) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionHighlight((i) => Math.min(i + 1, Math.max(mentionResults.length - 1, 0)));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionHighlight((i) => Math.max(i - 1, 0));
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closeInlineMention();
        return;
      }
      if ((e.key === "Enter" || e.key === "Tab") && !e.shiftKey && mentionResults.length > 0) {
        e.preventDefault();
        selectMention(mentionResults[Math.min(mentionHighlight, mentionResults.length - 1)]);
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSubmit(e);
    }
  };

  const onFilesSelected = (e) => {
    const files = Array.from(e.target.files || []);
    if (files.length > 0) setAttachedFiles((prev) => [...prev, ...files]);
    e.target.value = "";
    closeMenu();
  };

  const hasChips = attachedFiles.length > 0 || mentionedProjects.length > 0 || !!forcedTool;

  return (
    <form className="chat-composer" onSubmit={onSubmit}>
      {hasChips && (
        <div className="chat-composer-chips">
          {attachedFiles.map((f, i) => (
            <span className="chat-chip" key={`file-${i}`}>
              <IconPaperclip /> {f.name}
              <button type="button" onClick={() => setAttachedFiles((prev) => prev.filter((_, idx) => idx !== i))}>
                <CloseIcon size={12} />
              </button>
            </span>
          ))}
          {mentionedProjects.map((p) => (
            <span className="chat-chip" key={`mention-${p.id}`}>
              @{p.name}
              <button type="button" onClick={() => setMentionedProjects((prev) => prev.filter((x) => x.id !== p.id))}>
                <CloseIcon size={12} />
              </button>
            </span>
          ))}
          {forcedTool && (
            <span className="chat-chip">
              <IconWrench /> {TOOL_LABELS[forcedTool] || forcedTool}
              <button type="button" onClick={() => setForcedTool(null)}>
                <CloseIcon size={12} />
              </button>
            </span>
          )}
        </div>
      )}

      <div className="chat-composer-textarea-wrap">
        <textarea
          ref={textareaRef}
          className="chat-composer-textarea"
          value={question}
          onChange={onQuestionChange}
          onKeyDown={onKeyDown}
          placeholder={placeholder || "AI로 무엇이든 시도해 보세요..."}
          rows={1}
          disabled={disabled}
        />
        {inlineMention && (
          <Popover align="left" width={280} onClose={closeInlineMention} triggerRef={textareaRef}>
            <div className="chat-popover-list">
              {mentionLoading ? (
                <div className="chat-popover-empty">검색 중...</div>
              ) : mentionResults.length === 0 ? (
                <div className="chat-popover-empty">일치하는 프로젝트가 없습니다</div>
              ) : (
                mentionResults.map((p, i) => (
                  <button
                    type="button"
                    key={p.id}
                    className={`chat-popover-item${i === mentionHighlight ? " chat-popover-item-active" : ""}`}
                    onMouseEnter={() => setMentionHighlight(i)}
                    onClick={() => selectMention(p)}
                  >
                    {p.name}
                  </button>
                ))
              )}
            </div>
          </Popover>
        )}
      </div>

      <div className="chat-composer-toolbar">
        <div className="chat-composer-toolbar-side">
          <button
            ref={plusBtnRef}
            type="button"
            className="chat-composer-btn"
            aria-label="추가"
            onClick={togglePlusMenu}
          >
            <PlusIcon size={18} />
          </button>
          <input ref={fileInputRef} type="file" multiple hidden onChange={onFilesSelected} />
          {openMenu === "plus" && (
            <Popover align="left" width={260} onClose={closeMenu} triggerRef={plusBtnRef}>
              {plusView === "root" && (
                <div className="chat-popover-list">
                  <button type="button" className="chat-popover-item" onClick={() => fileInputRef.current?.click()}>
                    <IconPaperclip /> 사진 및 파일 추가하기
                  </button>
                  <button type="button" className="chat-popover-item" onClick={insertMentionTrigger}>
                    <span className="chat-popover-item-glyph">@</span> 프로젝트 멘션하기
                  </button>
                  <button type="button" className="chat-popover-item" onClick={() => setPlusView("skills")}>
                    <IconWrench /> 스킬
                  </button>
                </div>
              )}
              {plusView === "skills" && (
                <div className="chat-popover-list">
                  <button type="button" className="chat-popover-item chat-popover-item-back" onClick={() => setPlusView("root")}>
                    <IconBack /> 스킬
                  </button>
                  {Object.entries(TOOL_LABELS).map(([name, label]) => (
                    <button
                      type="button"
                      key={name}
                      className={`chat-popover-item${forcedTool === name ? " chat-popover-item-active" : ""}`}
                      onClick={() => selectSkill(name)}
                    >
                      <IconWrench /> {label}
                      {forcedTool === name && <IconCheck className="chat-popover-item-check" />}
                    </button>
                  ))}
                </div>
              )}
            </Popover>
          )}
        </div>

        <div className="chat-composer-toolbar-side">
          <button
            ref={modelBtnRef}
            type="button"
            className="chat-composer-model-btn"
            onClick={toggleModelMenu}
          >
            <selectedModel.Icon size={14} /> {selectedModel.label} <IconChevronDown />
          </button>
          {openMenu === "model" && (
            <Popover align="right" width={280} onClose={closeMenu} triggerRef={modelBtnRef}>
              <div className="chat-popover-list">
                <button
                  type="button"
                  className={`chat-popover-item${selectedModel.id === AUTO_MODEL.id ? " chat-popover-item-active" : ""}`}
                  onClick={() => {
                    onSelectModel(AUTO_MODEL);
                    closeMenu();
                  }}
                >
                  <AutoIcon />
                  <span className="chat-popover-item-body">
                    <span>{AUTO_MODEL.label}</span>
                    <span className="chat-popover-item-desc">{AUTO_MODEL.description}</span>
                  </span>
                </button>
                {(modelGroups || []).map((group) => (
                  <div className="model-menu-group" key={group.title}>
                    <div className="model-menu-group-title">{group.title}</div>
                    {group.models.map((model, i) => (
                      <button
                        type="button"
                        key={`${group.title}-${i}`}
                        className={`chat-popover-item${model.disabled ? " chat-popover-item-disabled" : ""}${
                          selectedModel.id === model.id ? " chat-popover-item-active" : ""
                        }`}
                        disabled={model.disabled}
                        onClick={() => {
                          onSelectModel(model);
                          closeMenu();
                        }}
                      >
                        <model.Icon size={14} /> {model.label}
                        {model.starred && <IconStar className="chat-popover-item-star" />}
                        {model.badge && <span className="chat-popover-item-badge">{model.badge}</span>}
                      </button>
                    ))}
                  </div>
                ))}
              </div>
            </Popover>
          )}
          <button type="submit" className="chat-composer-send" disabled={!connected || !question.trim() || disabled}>
            <IconSend />
          </button>
        </div>
      </div>
    </form>
  );
}

// 제목 옆 세션 드롭다운 + 새 채팅/더보기 아이콘. 백엔드가 프로젝트 안 여러
// 세션(chat_session, sql/018)을 지원하므로 실제 세션 목록을 보여주고 전환/
// 생성/이름변경/삭제를 모두 연결한다. "링크 복사"는 여전히 순수 프론트 동작.
function ChatHeader({
  title, connected, reanalyzing, sessions, activeSessionId,
  onSelectSession, onNewChat, onRenameSession, onDeleteSession,
}) {
  const [openMenu, setOpenMenu] = useState(null); // null | "session" | "more"
  const [linkCopied, setLinkCopied] = useState(false);
  const [sessionSearchOpen, setSessionSearchOpen] = useState(false);
  const [sessionQuery, setSessionQuery] = useState("");
  const sessionBtnRef = useRef(null);
  const moreBtnRef = useRef(null);

  const closeMenu = () => {
    setOpenMenu(null);
    setSessionSearchOpen(false);
    setSessionQuery("");
  };
  const toggleSessionMenu = () => setOpenMenu((m) => (m === "session" ? null : "session"));
  const toggleMoreMenu = () => setOpenMenu((m) => (m === "more" ? null : "more"));

  const list = sessions || [];
  const q = sessionQuery.trim().toLowerCase();
  const filtered = q ? list.filter((s) => (s.title || "새 채팅").toLowerCase().includes(q)) : list;

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setLinkCopied(true);
      setTimeout(() => setLinkCopied(false), 1500);
    } catch {
      // 클립보드 권한이 없는 환경 — 조용히 무시
    }
  };

  const renameActive = () => {
    const current = list.find((s) => s.id === activeSessionId);
    const next = window.prompt("세션 이름 바꾸기", current?.title || "");
    if (next != null && next.trim()) onRenameSession(activeSessionId, next);
    closeMenu();
  };

  const deleteActive = () => {
    if (window.confirm("이 세션을 삭제할까요? 목록에서 사라집니다.")) onDeleteSession(activeSessionId);
    closeMenu();
  };

  return (
    <div className="chat-header">
      <div className="chat-header-title-wrap">
        <button ref={sessionBtnRef} type="button" className="chat-header-title-btn" onClick={toggleSessionMenu}>
          <span className="chat-header-title-text">{title}</span>
          {!connected && <span className="muted">(연결 중...)</span>}
          <IconChevronDown />
        </button>
        {openMenu === "session" && (
          <Popover align="left" side="bottom" width={280} onClose={closeMenu} triggerRef={sessionBtnRef}>
            <div className="chat-session-menu-header">
              <span className="chat-session-menu-label">대화 세션</span>
              <button
                type="button"
                className="chat-session-menu-search-btn"
                aria-label="대화 검색"
                onClick={() => setSessionSearchOpen((v) => !v)}
              >
                <IconSearch />
              </button>
            </div>
            {sessionSearchOpen && (
              <input
                autoFocus
                className="chat-popover-search"
                value={sessionQuery}
                onChange={(e) => setSessionQuery(e.target.value)}
                placeholder="대화 검색..."
              />
            )}
            <div className="chat-popover-list">
              {filtered.length === 0 ? (
                <div className="chat-popover-empty">일치하는 대화가 없습니다</div>
              ) : (
                filtered.map((s) => (
                  <button
                    type="button"
                    key={s.id}
                    className={`chat-popover-item${s.id === activeSessionId ? " chat-popover-item-active" : ""}`}
                    onClick={() => {
                      onSelectSession(s.id);
                      closeMenu();
                    }}
                  >
                    {s.id === activeSessionId && <IconCheck className="chat-popover-item-check" />}
                    {s.title || "새 채팅"}
                  </button>
                ))
              )}
            </div>
          </Popover>
        )}
      </div>

      <div className="chat-header-actions">
        {reanalyzing && (
          <span className="chat-reanalyze-chip" title="이전 분석 결과로 답하고 있어요. 끝나면 자동으로 최신 결과를 써요.">
            <SpinnerRing size={12} /> 최신 커밋 분석 중
          </span>
        )}
        <button type="button" className="chat-header-icon-btn" title="새 채팅" onClick={onNewChat}>
          <NewChatIcon size={17} />
        </button>
        <button ref={moreBtnRef} type="button" className="chat-header-icon-btn" onClick={toggleMoreMenu}>
          <MoreHorizontalIcon size={17} />
        </button>
        {openMenu === "more" && (
          <Popover align="right" side="bottom" width={200} onClose={closeMenu} triggerRef={moreBtnRef}>
            <div className="chat-popover-list">
              <button type="button" className="chat-popover-item" onClick={copyLink}>
                <LinkIcon /> {linkCopied ? "복사됨" : "링크 복사"}
              </button>
              <button
                type="button"
                className="chat-popover-item"
                onClick={renameActive}
                disabled={!activeSessionId}
              >
                <IconPencil /> 이름 바꾸기
              </button>
              <button
                type="button"
                className="chat-popover-item"
                onClick={deleteActive}
                disabled={!activeSessionId}
              >
                <TrashIcon /> 삭제하기
              </button>
            </div>
          </Popover>
        )}
      </div>
    </div>
  );
}

// projectId를 prop으로 받으면 그 값을, 아니면 URL(useParams)을 쓴다 —
// 멀티페인 셸은 프로젝트마다 별도 채팅 패널을 띄우므로 전역 activeProject에
// 의존하지 않고 자기 projectId로 프로젝트를 직접 찾는다.
export default function ProjectChatPage({ projectId: propProjectId }) {
  const { projectId: routeProjectId } = useParams();
  const projectId = propProjectId ?? routeProjectId;
  const { projects } = useProject();
  const { openDetailTab } = useTabs();
  const { me } = useMe(); // 내 질문(오른쪽)과 팀원 질문(왼쪽, 프로필) 구분
  const project = useMemo(
    () => (projects || []).find((p) => p.id === projectId) || null,
    [projects, projectId]
  );
  const openRiskDetail = useCallback(() => {
    if (project) openDetailTab(project);
  }, [project, openDetailTab]);
  const [sessions, setSessions] = useState(null); // null = 불러오는 중 | ChatSession[]
  const [activeSessionId, setActiveSessionId] = useState(null); // 현재 보고 있는 세션
  const [messages, setMessages] = useState(null); // null = 불러오는 중
  // streaming/agentEvents는 답변이 확정된 뒤에도 "다음 질문을 보내기 전까지"
  // 지우지 않는다 — 완료 즉시 히스토리용 DOM(key=message.id)으로 갈아끼우면
  // client_msg_id로 마운트돼 있던 타임라인/버블이 통째로 리마운트되어, 막
  // 재생되던 타임라인 접힘/답변 페이드인 애니메이션이 그 순간 사라져버린다.
  // 항상 "아직 안 넘긴 최신 턴" 하나만 남기므로(다음 send() 시점에 정리)
  // messages 목록과의 시간 순서는 깨지지 않는다.
  const [streaming, setStreaming] = useState({}); // client_msg_id -> 누적 텍스트(내가 보낸 것만)
  const [agentEvents, setAgentEvents] = useState({}); // client_msg_id -> AgentEvent[] (id로 upsert, 내가 보낸 것만)
  const [riskByCid, setRiskByCid] = useState({}); // client_msg_id -> Contextual Risk(§16, evaluate_query 결과)
  const [pendingClientMsgId, setPendingClientMsgId] = useState(null); // 응답을 기다리는 중인 질문(최대 1개), 없으면 null
  const [liveShownMessageIds, setLiveShownMessageIds] = useState(() => new Set()); // 아직 실시간 블록으로 보여주는 중이라 히스토리 목록에서는 건너뛸 DB message id
  const [question, setQuestion] = useState("");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const [mentionedProjects, setMentionedProjects] = useState([]); // [{id, name}]
  const [forcedTool, setForcedTool] = useState(null); // 스킬로 강제 지정한 도구명
  const [attachedFiles, setAttachedFiles] = useState([]); // UI 표시 전용 — 업로드하지 않음
  // 모델 목록은 게이트웨이가 서빙 중인 것(백엔드 GET /models). 응답 전/실패 시 폴백.
  const [modelRows, setModelRows] = useState(FALLBACK_MODELS);
  const [selectedModelId, setSelectedModelId] = useState(() => {
    try {
      return localStorage.getItem(MODEL_STORAGE_KEY) || AUTO_MODEL.id;
    } catch {
      return AUTO_MODEL.id;
    }
  });
  const [liveChatTitle, setLiveChatTitle] = useState(null); // 첫 질문 후 서버가 지어준 제목(새로고침 전 즉시 반영용)
  const [pentestSuggestion, setPentestSuggestion] = useState(null); // {target, goal} — 보안 점검 의도 감지 시 블랙박스 실행 제안
  const bottomRef = useRef(null);
  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  // 질문을 보낸 시점의 messages.length 스냅샷 — 확인 useEffect가 "그 이후에
  // 새로 도착한" assistant 메시지에만 반응하도록 막아준다. 이게 없으면,
  // send()가 이전 턴을 히스토리로 넘기려고 liveShownMessageIds를 비우는
  // 순간(아직 새 질문의 user 메시지도 서버에서 안 돌아온 시점) messages
  // 배열의 "마지막 메시지"가 여전히 이전 답변이라, 그걸 새 질문의 답변으로
  // 오인해서 즉시(가짜로) 확인 처리해버린다.
  const pendingSinceCountRef = useRef(0);

  // 첫 분석이 끝나기 전엔 질문을 막는다(서버도 analysis_not_ready 로 거절 — app/chat.py).
  const { analysis, refresh: refreshAnalysis } = useProjectAnalysis(projectId);
  const chatLocked = !analysis || !analysis.chat_ready;
  const refreshAnalysisRef = useRef(refreshAnalysis);
  useEffect(() => {
    refreshAnalysisRef.current = refreshAnalysis;
  }, [refreshAnalysis]);
  const retryAnalysis = useCallback(async () => {
    const repoId = analysis?.latest?.repository?.id;
    if (!repoId) return;
    try {
      await requestScan(repoId);
      watchProjectAnalysis(projectId, project?.name);
    } catch (err) {
      setError(err.message);
    }
    await refreshAnalysis();
  }, [analysis, projectId, project, refreshAnalysis]);

  useEffect(() => {
    let alive = true;
    fetchModels()
      .then((data) => {
        if (!alive) return;
        if (Array.isArray(data?.models) && data.models.length) setModelRows(data.models);
      })
      .catch(() => {}); // 폴백 목록 유지 — 모델 선택 불가로 채팅을 막지 않는다
    return () => {
      alive = false;
    };
  }, []);

  const modelGroups = useMemo(() => buildModelGroups(modelRows), [modelRows]);
  const selectedModel = useMemo(() => {
    if (selectedModelId === AUTO_MODEL.id) return AUTO_MODEL;
    return findModelOption(modelGroups, selectedModelId) || AUTO_MODEL;
  }, [selectedModelId, modelGroups]);
  const selectModel = useCallback((model) => {
    setSelectedModelId(model.id);
    try {
      localStorage.setItem(MODEL_STORAGE_KEY, model.id);
    } catch {
      /* private mode 등 — 저장 실패는 무시 */
    }
  }, []);

  const loadHistory = useCallback(() => {
    if (!projectId || !activeSessionId) return;
    fetchChatMessages(projectId, activeSessionId).then(setMessages).catch((err) => setError(err.message));
  }, [projectId, activeSessionId]);

  // 세션 목록 부트스트랩 — projectId가 바뀔 때마다 세션을 불러오고, 하나도
  // 없으면 즉시 빈 세션 하나를 만들어 활성화한다(ChatGPT류의 "첫 방문 시 빈
  // 대화"). 이미 보고 있던 세션이 목록에 그대로 있으면 그 선택을 유지한다.
  useEffect(() => {
    if (!projectId) {
      setSessions(null);
      setActiveSessionId(null);
      return undefined;
    }
    let cancelled = false;
    setSessions(null);
    setActiveSessionId(null);
    setError(null);
    (async () => {
      try {
        let list = await fetchChatSessions(projectId);
        if (cancelled) return;
        if (list.length === 0) {
          const created = await createChatSession(projectId);
          if (cancelled) return;
          list = [created];
        }
        setSessions(list);
        setActiveSessionId((prev) => (prev && list.some((s) => s.id === prev) ? prev : list[0].id));
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // WebSocket 연결 — 활성 세션당 하나. 끊기면(네트워크 문제 등) 짧은 backoff로
  // 재연결하고, 재연결 성공 시마다 히스토리를 다시 불러온다 — Postgres NOTIFY는
  // 재전송 큐가 없어서 끊겨 있던 동안 놓친 메시지는 재조회로만 메꿀 수 있다.
  // activeSessionId가 바뀌면(세션 전환) 이 이펙트가 통째로 재실행되며 진행 중
  // 상태를 전부 리셋하고 새 세션 소켓으로 다시 붙는다.
  useEffect(() => {
    if (!projectId || !activeSessionId) return undefined;
    let cancelled = false;
    setMessages(null);
    setStreaming({});
    setAgentEvents({});
    setRiskByCid({});
    setPendingClientMsgId(null);
    pendingSinceCountRef.current = 0;
    setLiveShownMessageIds(new Set());
    setError(null);
    setLiveChatTitle(null);
    loadHistory();

    const backoff = createBackoff();
    let gaveUp = false; // 4401/4403 — 다시 연결해도 소용없음

    const connect = () => {
      if (cancelled || gaveUp) return;
      reconnectTimerRef.current = null;
      const ws = new WebSocket(chatSocketUrl(projectId, activeSessionId));
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelled) return;
        backoff.reset();
        setConnected(true);
        loadHistory(); // 끊겨 있던 동안의 메시지는 브로드캐스트로 재생되지 않으므로 다시 불러온다
      };

      ws.onmessage = (evt) => {
        if (cancelled) return;
        const data = JSON.parse(evt.data);
        if (data.type === "message") {
          const msg = data.message;
          setMessages((prev) => {
            const list = prev || [];
            if (list.some((m) => m.id === msg.id)) return list;
            return [...list, msg];
          });
        } else if (data.type === "agent_event") {
          const { client_msg_id, event } = data;
          setAgentEvents((prev) => {
            const list = prev[client_msg_id] || [];
            const idx = list.findIndex((e) => e.id === event.id);
            const next = idx === -1 ? [...list, event] : list.map((e, i) => (i === idx ? { ...e, ...event } : e));
            return { ...prev, [client_msg_id]: next };
          });
        } else if (data.type === "chat_title") {
          setLiveChatTitle(data.title);
          setSessions((prev) =>
            (prev || []).map((s) => (s.id === activeSessionId ? { ...s, title: data.title } : s))
          );
        } else if (data.type === "answer_chunk") {
          setStreaming((prev) => ({
            ...prev,
            [data.client_msg_id]: (prev[data.client_msg_id] || "") + data.delta,
          }));
        } else if (data.type === "risk") {
          setRiskByCid((prev) => ({ ...prev, [data.client_msg_id]: data.risk }));
        } else if (data.type === "pentest_suggestion") {
          // 보안 점검 의도 + 인가 표적 감지 — 블랙박스 engagement 실행 제안(사용자 승인 후 실행).
          setPentestSuggestion({ target: data.target, goal: data.goal });
        } else if (data.type === "error") {
          if (data.error === "analysis_not_ready") {
            // 화면은 열려 있었는데 서버 기준으론 아직 — 상태를 다시 맞춘다.
            setError(data.message || "저장소 분석이 끝나면 질문할 수 있어요");
            refreshAnalysisRef.current?.();
          } else {
            setError(data.error);
          }
          // 이 턴은 끝났다 — 대기 상태를 풀어 입력창을 다시 열고, 진행 중
          // 블록의 "생각하는 중..." 버블이 계속 남지 않게 한다.
          setPendingClientMsgId((cur) => (cur === data.client_msg_id ? null : cur));
        }
      };

      ws.onclose = (evt) => {
        if (cancelled) return;
        setConnected(false);
        if (isTerminalClose(evt.code)) {
          gaveUp = true;
          return;
        }
        reconnectTimerRef.current = setTimeout(connect, backoff.next());
      };

      ws.onerror = () => ws.close();
    };

    // 백그라운드 탭에서 백오프로 오래 기다리던 중이면, 다시 보는 순간 바로 연결한다.
    const onVisible = () => {
      if (document.visibilityState === "visible" && reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisible);

    connect();

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisible);
      clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [projectId, activeSessionId, loadHistory]);

  // 진행 중인 턴 = 방금 보낸 질문(pendingClientMsgId). 답변 토큰(answer_chunk)이
  // 오기 전에도 agent_event(계획/생각/도구 호출)를 실시간으로 렌더해야 하므로
  // streaming(answer_chunk에서만 채워짐)이 아니라 pendingClientMsgId를 기준으로
  // 잡는다. 답변이 확정된 뒤에도 다음 send() 전까지는 agentEvents/streaming에
  // 남아 있는 그 키로 계속 렌더한다(리마운트 방지 — 아래 상태 선언부 주석 참고).
  // 내 턴은 항상 0~1개라 단일 값으로 다루면 throttle 훅을 조건 없이 호출할 수 있다.
  const streamingCid =
    pendingClientMsgId || Object.keys(agentEvents)[0] || Object.keys(streaming)[0];
  const streamingRaw = streamingCid ? streaming[streamingCid] || "" : "";
  const streamingText = useThrottledValue(streamingRaw, 80);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streaming, streamingText, agentEvents]);

  // 보낸 질문의 답변이 확정되면(서버가 브로드캐스트) "응답 대기 중" 상태만
  // 풀어준다 — streaming/agentEvents 자체는 지우지 않아 다음 질문을 보낼
  // 때까지 같은 DOM으로 계속 렌더링된다(리마운트 방지, 위 상태 선언부 참고).
  useEffect(() => {
    if (!messages || messages.length === 0 || !pendingClientMsgId) return;
    if (messages.length <= pendingSinceCountRef.current) return; // 질문 보내기 전부터 있던 메시지 — 무시
    const last = messages[messages.length - 1];
    if (last.role !== "assistant" || liveShownMessageIds.has(last.id)) return;
    // client_msg_id는 서버가 안 돌려주므로(스키마 변경 없이 상관관계만 주는
    // 필드) 정확한 1:1 매칭은 못 하지만, 응답을 기다리는 중에 새 assistant
    // 메시지가 도착하면 사실상 내가 보낸 질문의 답이 온 것으로 본다.
    setLiveShownMessageIds((prev) => new Set(prev).add(last.id));
    setPendingClientMsgId(null);
  }, [messages, pendingClientMsgId, liveShownMessageIds]);

  const isStreaming = pendingClientMsgId !== null;
  // 실시간 블록으로 이미 보여주고 있는 assistant 메시지는 히스토리 목록에서
  // 건너뛴다 — 안 그러면 같은 답변이 두 번(위: 히스토리, 아래: 실시간) 보인다.
  const visibleMessages = (messages || []).filter((m) => !liveShownMessageIds.has(m.id));

  const send = (e) => {
    e.preventDefault();
    const q = question.trim();
    const ws = wsRef.current;
    if (!q || isStreaming || chatLocked || !ws || ws.readyState !== WebSocket.OPEN) return;
    const clientMsgId = crypto.randomUUID();
    // 직전 턴은 확정된 지 오래고 이제 새 턴이 화면 맨 아래를 차지하니, 그동안
    // 리마운트를 피하려고 남겨뒀던 실시간 렌더링 자리를 여기서 정리한다 —
    // 히스토리 목록(messages)이 evidence.agent_events로 이어서 정상 렌더한다
    // (정보 손실 없음, dedupeEvents가 그대로 병합해 보여준다).
    setStreaming((prev) => (Object.keys(prev).length === 0 ? prev : {}));
    setAgentEvents((prev) => (Object.keys(prev).length === 0 ? prev : {}));
    setRiskByCid((prev) => (Object.keys(prev).length === 0 ? prev : {}));
    setLiveShownMessageIds((prev) => (prev.size === 0 ? prev : new Set()));
    // 지금 이 시점까지의 메시지 개수를 스냅샷으로 남긴다 — 위 확인 useEffect가
    // 이 개수를 넘어서는(=이 질문을 보낸 뒤에 새로 도착한) assistant 메시지만
    // "이 질문의 답"으로 인정하게 하기 위함(바로 위 liveShownMessageIds 초기화로
    // 다시 드러난 직전 답변을 새 질문의 답으로 오인하는 경쟁 상태 방지).
    pendingSinceCountRef.current = messages ? messages.length : 0;
    ws.send(JSON.stringify({
      type: "message",
      question: q,
      client_msg_id: clientMsgId,
      mentioned_project_ids: mentionedProjects.map((p) => p.id),
      forced_tool: forcedTool,
      // "auto"는 서버가 전역 기본 모델로 해석한다. 게이트웨이가 안 서빙하는 값도 서버가 폴백.
      model: selectedModel.id,
    }));
    setPendingClientMsgId(clientMsgId);
    setQuestion("");
    setMentionedProjects([]);
    setForcedTool(null);
    setAttachedFiles([]); // UI 표시 전용이라 실제 첨부 없이 그냥 비운다
  };

  const activeSession = useMemo(
    () => (sessions || []).find((s) => s.id === activeSessionId) || null,
    [sessions, activeSessionId]
  );
  const headerTitle = liveChatTitle || activeSession?.title || "새 채팅";

  const selectSession = (id) => {
    if (id === activeSessionId) return;
    setLiveChatTitle(null);
    setActiveSessionId(id);
  };

  const newChatSession = async () => {
    try {
      const created = await createChatSession(projectId);
      setSessions((prev) => [created, ...(prev || [])]);
      setLiveChatTitle(null);
      setActiveSessionId(created.id);
    } catch (err) {
      setError(err.message);
    }
  };

  const renameSession = async (id, nextTitle) => {
    const t = nextTitle.trim();
    if (!t) return;
    try {
      const updated = await renameChatSession(projectId, id, t);
      setSessions((prev) => (prev || []).map((s) => (s.id === id ? { ...s, title: updated.title } : s)));
      if (id === activeSessionId) setLiveChatTitle(null);
    } catch (err) {
      setError(err.message);
    }
  };

  const removeSession = async (id) => {
    try {
      await deleteChatSession(projectId, id);
    } catch (err) {
      setError(err.message);
      return;
    }
    const remaining = (sessions || []).filter((s) => s.id !== id);
    if (remaining.length === 0) {
      try {
        const created = await createChatSession(projectId);
        setSessions([created]);
        setLiveChatTitle(null);
        setActiveSessionId(created.id);
      } catch (err) {
        setError(err.message);
      }
      return;
    }
    setSessions(remaining);
    if (id === activeSessionId) {
      setLiveChatTitle(null);
      setActiveSessionId(remaining[0].id);
    }
  };

  return (
    <div className="chat-page">
      <ChatHeader
        title={headerTitle}
        connected={connected}
        reanalyzing={analysis?.state === "ready_reanalyzing"}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={selectSession}
        onNewChat={newChatSession}
        onRenameSession={renameSession}
        onDeleteSession={removeSession}
      />

      <div className="chat-messages">
        <div className="chat-messages-inner">
          {messages === null ? (
            <p className="muted">불러오는 중...</p>
          ) : visibleMessages.length === 0 && !streamingCid ? (
            chatLocked ? null : <p className="muted">
              이 프로젝트에 대해 무엇이든 물어보세요 — 커밋/브랜치/PR/릴리즈 이력부터 코드 위치, 보안 취약점까지
              필요한 데이터를 찾아 답합니다.
            </p>
          ) : (
            visibleMessages.map((m) => (
              <ChatMessage
                key={m.id}
                role={m.role}
                content={m.content}
                author={m.author}
                mine={!!m.author && m.author.id === me?.id}
                createdAt={m.created_at}
                events={historyEvents(m)}
                toolsUsed={m.tools_used}
                risk={m.evidence?.risk}
                onOpenRiskDetail={openRiskDetail}
              />
            ))
          )}
          {streamingCid && (
            <ChatMessage
              key={streamingCid}
              role="assistant"
              content={streamingText}
              events={agentEvents[streamingCid] || []}
              risk={riskByCid[streamingCid]}
              onOpenRiskDetail={openRiskDetail}
              live
              streaming={isStreaming}
              pending={!streamingText && (agentEvents[streamingCid] || []).length === 0}
            />
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {error && <div className="alert-error">{error}</div>}

      {pentestSuggestion && (
        <PentestSuggestionBanner
          projectId={projectId}
          suggestion={pentestSuggestion}
          onDismiss={() => setPentestSuggestion(null)}
        />
      )}

      {chatLocked && (
        <AnalysisLockPanel
          analysis={analysis}
          onConnectRepository={project ? () => openDetailTab(project) : null}
          onRetry={retryAnalysis}
        />
      )}

      <ChatComposer
        question={question}
        setQuestion={setQuestion}
        onSubmit={send}
        disabled={isStreaming || chatLocked}
        placeholder={
          !chatLocked ? null
            : analysis?.state === "no_repository" ? "저장소를 연결하면 질문할 수 있어요"
              : "분석이 끝나면 질문할 수 있어요"
        }
        connected={connected}
        teamspaceId={project?.teamspace_id}
        currentProjectId={projectId}
        mentionedProjects={mentionedProjects}
        setMentionedProjects={setMentionedProjects}
        forcedTool={forcedTool}
        setForcedTool={setForcedTool}
        attachedFiles={attachedFiles}
        setAttachedFiles={setAttachedFiles}
        selectedModel={selectedModel}
        onSelectModel={selectModel}
        modelGroups={modelGroups}
      />
    </div>
  );
}

// 보안 점검 의도가 감지되면 뜨는 배너 — 승인(버튼 클릭) 시에만 블랙박스 engagement 를 띄운다.
// 채팅에서 공격 도구를 인라인 실행하지 않는다(실행은 샌드박스·permission·HITL·감사가 붙은 engagement).
function PentestSuggestionBanner({ projectId, suggestion, onDismiss }) {
  const [busy, setBusy] = useState(false);
  const [engId, setEngId] = useState(null);
  const [err, setErr] = useState(null);

  const start = async () => {
    setBusy(true); setErr(null);
    try {
      const res = await createEngagement(projectId, {
        goal: suggestion.goal, mode: "automation", permission_mode: "ask", target: suggestion.target,
      });
      setEngId(res.id);   // 라이브 패널을 채팅 안에서 바로 띄운다
    } catch (e) {
      setErr(e.message);
      setBusy(false);
    }
  };

  // 검증이 시작되면 채팅 안에서 실시간 진행(스텝·추론·승인·발견)을 보여준다.
  if (engId) return <LivePentestPanel engagementId={engId} onDismiss={onDismiss} />;
  return (
    <div className="dashboard-section" style={{ margin: "0.4rem 0", padding: "0.6rem 0.8rem",
                                               display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" }}>
      <span>🛡 인가된 표적 <code>{suggestion.target}</code> 에 보안 도구 점검을 실행할까요? (승인 필요 모드로 시작)</span>
      {err && <span className="alert-error" style={{ padding: "0.2rem 0.4rem" }}>{err}</span>}
      <span style={{ marginLeft: "auto", display: "flex", gap: "0.4rem" }}>
        <button className="btn-primary" onClick={start} disabled={busy}>
          {busy ? "시작 중..." : "블랙박스 검증 실행"}
        </button>
        <button onClick={onDismiss} disabled={busy}>무시</button>
      </span>
    </div>
  );
}
