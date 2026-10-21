"""프로젝트 단위 AI 채팅 — 프로젝트 안에 여러 세션(대화 스레드, sql/018 참고).

한 프로젝트는 여러 개의 `chat_session`을 가지며, 각 메시지는 정확히 하나의
세션에 속한다(`chat_message.session_id`). 세션 목록/생성/이름변경/삭제는 REST로,
질문 전송은 세션별 WebSocket(`/ws/projects/{project_id}/chat/sessions/{session_id}`)
하나로만 받는다 — REST POST와 WS 두 갈래로 같은 로직을 유지하지 않는다.
히스토리 최초 로딩용 GET만 REST로 남긴다.

메시지 브로드캐스트는 `app/chat_bus.py`(Postgres LISTEN/NOTIFY, Redis 없음)를
쓴다 — 완성된 메시지 단위로만, 그 **세션**을 보고 있는 모든 뷰어에게 실시간
전달되고, 토큰 스트리밍은 그 질문을 보낸 연결에만 로컬로 전달된다(토큰마다
Postgres NOTIFY를 쏘면 호출이 폭주하므로 chat_bus를 거치지 않음). 다만 실제
전송은 이 연결이 `chat_bus` 구독으로 받은 큐에 넣기만 하고, `websocket.send_json`
호출 자체는 그 큐를 비우는 `forward()` 태스크 하나만 한다 — 스트리밍
(`_handle_question`)과 브로드캐스트 전달(`forward`)이 같은 WebSocket에 각자 직접
send하면 동시 호출로 충돌할 수 있어서(한쪽이 조용히 실패해도 감지가 안 됨),
보내는 주체를 하나로 좁혔다.

`/scans/{scan_id}/ask`(main.py)와 같은 orchestrator 파이프라인을 쓰지만, scan이
아니라 project를 진입점으로 삼는다. project에 attach된 repository 중 가장 최근
것을 대표 repository로 골라 repo_* 도구(커밋/PR/릴리즈/브랜치)의 근거로 쓰고,
그 repository의 가장 최근 성공한 scan을 semantic_search/dataflow 도구의 근거로
쓴다. 아직 스캔이 없어도(또는 repository가 아직 없어도) 채팅 자체는 동작해야
하므로 둘 다 없으면 orchestrator가 관련 도구를 조용히 건너뛴다
([[app.orchestrator._execute_tool]]).
"""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import ai_providers, chat_bus, observability, orchestrator, profiles
from app.agent_events import AgentEvent, mark_done, new_event
from app.auth import (
    assert_project_role, get_current_member, get_current_member_ws, watch_ws_session, ws_origin_allowed,
)
from app.db import tx
from app.risk import synthesis as risk_synthesis

log = logging.getLogger("app.chat")

router = APIRouter(tags=["chat"])

_MAX_QUESTION_LEN = 2000

_CHAT_TITLE_PROMPT = """다음은 사용자가 프로젝트 AI 채팅 세션에서 처음 보낸 질문이다. 이 세션을 목록에서
한눈에 구분할 수 있는 짧은 제목을 지어라.

질문: {question}

규칙:
- 완전한 문장이 아니라 명사구로만 쓴다. 첫 질문의 핵심 주제를 명사로 요약하고,
  필요하면 "방법", "개념", "원인", "차이", "구조", "설정", "흐름", "예시" 같은
  명사로 끝맺는다.
- 서술어(~하다, ~한다, ~합니다, ~해줘, ~인가요, ~일까 등)나 물음표로 끝내지 않는다.
- 예: "로그인 처리 방법", "JWT 토큰 개념", "결제 모듈 구조", "빌드 실패 원인"
- 공백 포함 20자 이내의 한국어.
- 따옴표, 마침표, 이모지를 쓰지 않는다.
- 제목 텍스트만 출력한다(다른 설명 없이).
"""


# ---------------------------------------------------------------------------
# 세션 CRUD (REST)
# ---------------------------------------------------------------------------

class ChatSessionCreateIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ChatSessionRenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


def _assert_session(cur, project_id: UUID, session_id: UUID) -> None:
    """세션이 이 프로젝트에 속하고 삭제되지 않았는지 확인 — 아니면 404."""
    cur.execute(
        "SELECT 1 FROM chat_session WHERE id=%s AND project_id=%s AND deleted_at IS NULL",
        (str(session_id), str(project_id)),
    )
    if cur.fetchone() is None:
        raise HTTPException(404, "chat session not found")


@router.get("/projects/{project_id}/chat/sessions")
def list_chat_sessions(project_id: UUID, member: dict = Depends(get_current_member)):
    """이 프로젝트의 채팅 세션 목록 — 최근 활동 순(메시지 없는 세션은 생성 순)."""
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at, s.last_message_at,
                   (SELECT count(*) FROM chat_message m WHERE m.session_id = s.id) AS message_count
              FROM chat_session s
             WHERE s.project_id = %s AND s.deleted_at IS NULL
             ORDER BY COALESCE(s.last_message_at, s.created_at) DESC
            """,
            (str(project_id),),
        )
        return cur.fetchall()


@router.post("/projects/{project_id}/chat/sessions", status_code=201)
def create_chat_session(project_id: UUID, body: ChatSessionCreateIn,
                        member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            """
            INSERT INTO chat_session (project_id, title, created_by)
            VALUES (%s, %s, %s)
            RETURNING id, title, created_at, updated_at, last_message_at
            """,
            (str(project_id), body.title, member["id"]),
        )
        row = cur.fetchone()
    return {**row, "message_count": 0}


@router.patch("/projects/{project_id}/chat/sessions/{session_id}")
def rename_chat_session(project_id: UUID, session_id: UUID, body: ChatSessionRenameIn,
                        member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            """
            UPDATE chat_session SET title=%s, updated_at=now()
             WHERE id=%s AND project_id=%s AND deleted_at IS NULL
            RETURNING id, title, created_at, updated_at, last_message_at
            """,
            (body.title.strip(), str(session_id), str(project_id)),
        )
        row = cur.fetchone()
        if row is None:
            raise HTTPException(404, "chat session not found")
    return row


@router.delete("/projects/{project_id}/chat/sessions/{session_id}", status_code=204)
def delete_chat_session(project_id: UUID, session_id: UUID,
                        member: dict = Depends(get_current_member)):
    """소프트 삭제 — 메시지 행은 남기고 목록에서만 가린다."""
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        cur.execute(
            "UPDATE chat_session SET deleted_at=now() WHERE id=%s AND project_id=%s AND deleted_at IS NULL "
            "RETURNING id",
            (str(session_id), str(project_id)),
        )
        if cur.fetchone() is None:
            raise HTTPException(404, "chat session not found")


@router.get("/projects/{project_id}/chat/sessions/{session_id}/messages")
def list_chat_messages(project_id: UUID, session_id: UUID, member: dict = Depends(get_current_member)):
    with tx() as cur:
        assert_project_role(cur, project_id, member["id"])
        _assert_session(cur, project_id, session_id)
        cur.execute(
            """
            SELECT id, role, content, evidence, tools_used, mentioned_project_ids, forced_tool, created_at,
                   author_member_id
              FROM chat_message WHERE session_id=%s ORDER BY created_at
            """,
            (str(session_id),),
        )
        rows = cur.fetchall()
        # 채팅방 형식 — 질문마다 보낸 사람(프로필 + 팀스페이스 역할)을 같이 내려준다.
        authors = profiles.load_authors(cur, (r["author_member_id"] for r in rows), str(project_id))
        return [{**r, "author": authors.get(str(r["author_member_id"])) if r["author_member_id"] else None}
                for r in rows]


# ---------------------------------------------------------------------------
# 아래는 전부 동기(블로킹) 함수 — WebSocket 핸들러(async)에서 직접 await할 수
# 없는 psycopg 동기 커넥션풀을 쓰므로, 호출부에서 run_in_threadpool로 던진다.
# ---------------------------------------------------------------------------

def _assert_session_access(project_id: UUID, session_id: UUID, member_id: str) -> None:
    with tx() as cur:
        assert_project_role(cur, project_id, member_id)
        _assert_session(cur, project_id, session_id)


def _resolve_chat_ctx(cur, project_id: UUID) -> dict:
    cur.execute("SELECT name, description FROM project WHERE id=%s", (str(project_id),))
    proj_row = cur.fetchone()
    project_info = {"name": proj_row["name"], "description": proj_row["description"]} if proj_row else None

    cur.execute(
        """
        SELECT repository_id FROM project_repository
         WHERE project_id=%s ORDER BY attached_at DESC LIMIT 1
        """,
        (str(project_id),),
    )
    repo = cur.fetchone()
    repository_id = repo["repository_id"] if repo else None

    scan_id = None
    if repository_id:
        cur.execute(
            """
            SELECT id FROM scan
             WHERE repository_id=%s AND status='succeeded'
             ORDER BY created_at DESC LIMIT 1
            """,
            (str(repository_id),),
        )
        scan = cur.fetchone()
        scan_id = str(scan["id"]) if scan else None

    return {"repository_id": repository_id, "scan_id": scan_id, "project_info": project_info}


def _resolve_mentioned_projects(cur, member_id: str, mentioned_project_ids: list[str]) -> list[dict]:
    """@ 멘션된 project_id 중 caller가 접근 가능한 것만 {id,name,description}으로 돌려준다 —

    접근 불가한(또는 존재하지 않는) id는 조용히 걸러낸다 — 정보 누출 방지 +
    이 모듈 전체의 fail-open 철학과 일관되게, 잘못된 멘션 하나가 질문 전체를
    막지 않는다.
    """
    resolved = []
    for raw_id in mentioned_project_ids:
        try:
            pid = UUID(raw_id)
            assert_project_role(cur, pid, member_id)
        except Exception:
            continue
        cur.execute("SELECT id, name, description FROM project WHERE id=%s AND deleted_at IS NULL", (str(pid),))
        row = cur.fetchone()
        if row:
            resolved.append(row)
    return resolved


def _touch_session(cur, session_id: str) -> None:
    cur.execute(
        "UPDATE chat_session SET last_message_at=now(), updated_at=now() WHERE id=%s",
        (session_id,),
    )


def _insert_user_message(session_id: str, project_id: str, question: str, member_id: str,
                          mentioned_project_ids: list[str], forced_tool: str | None) -> dict:
    """user 메시지 INSERT + 같은 트랜잭션에서 notify + ctx 계산까지 한 번에 묶는다."""
    with tx() as cur:
        cur.execute("SELECT 1 FROM chat_message WHERE session_id=%s LIMIT 1", (session_id,))
        is_first_message = cur.fetchone() is None
        mentioned = _resolve_mentioned_projects(cur, member_id, mentioned_project_ids)
        cur.execute(
            """
            INSERT INTO chat_message (project_id, session_id, role, content, mentioned_project_ids, forced_tool,
                                      author_member_id)
            VALUES (%s, %s, 'user', %s, %s, %s, %s) RETURNING id
            """,
            (project_id, session_id, question,
             [str(p["id"]) for p in mentioned] or None, forced_tool, member_id),
        )
        message_id = cur.fetchone()["id"]
        _touch_session(cur, session_id)
        chat_bus.notify_chat_event(cur, session_id, message_id)
        ctx = _resolve_chat_ctx(cur, project_id)
        ctx["is_first_message"] = is_first_message
        if mentioned:
            ctx["mentioned_projects"] = [{"name": p["name"], "description": p["description"]} for p in mentioned]
    return ctx


def _generate_and_store_chat_title(session_id: str, project_id: str, question: str,
                                    model: str) -> str | None:
    """첫 질문으로 짧은 세션 제목을 지어 chat_session.title에 저장한다.

    실패해도(LLM 호출 오류, 빈 응답 등) 채팅 자체엔 영향이 없어야 하므로
    호출부(`_generate_chat_title_task`)에서 fail-open으로 감싼다 — 여기서는
    "실패하면 None"만 보장한다. 사용자가 그 사이 세션 이름을 직접 바꿨으면
    (title IS NOT NULL) 덮어쓰지 않는다.
    """
    raw = ai_providers.generate(model, _CHAT_TITLE_PROMPT.format(question=question),
                                 json_mode=False, project_id=project_id, purpose="qa")
    title = raw.strip().strip('"').strip("'").strip()[:40]
    if not title:
        return None
    with tx() as cur:
        cur.execute(
            "UPDATE chat_session SET title=%s, updated_at=now() WHERE id=%s AND title IS NULL RETURNING id",
            (title, session_id),
        )
        if cur.fetchone() is None:
            return None
    return title


async def _generate_chat_title_task(session_id: str, project_id: str, question: str,
                                     model: str, queue: asyncio.Queue) -> None:
    """첫 질문 제목 생성은 답변 스트리밍과 무관한 부가 기능이라 fire-and-forget으로
    돌린다 — 실패해도(또는 느려도) 메인 답변 파이프라인을 막지 않는다."""
    try:
        title = await run_in_threadpool(_generate_and_store_chat_title, session_id, project_id,
                                         question, model)
        if title:
            await queue.put({"type": "chat_title", "session_id": session_id, "title": title})
    except Exception:
        log.exception("채팅 제목 생성 실패 — session_id=%s", session_id)


def _insert_assistant_message(session_id: str, project_id: str, content: str, evidence: dict,
                               tools_used: list[dict]) -> None:
    with tx() as cur:
        cur.execute(
            """
            INSERT INTO chat_message (project_id, session_id, role, content, evidence, tools_used)
            VALUES (%s, %s, 'assistant', %s, %s::jsonb, %s::jsonb) RETURNING id
            """,
            (project_id, session_id, content,
             json.dumps(evidence, ensure_ascii=False, default=str),
             json.dumps(tools_used, ensure_ascii=False, default=str)),
        )
        message_id = cur.fetchone()["id"]
        _touch_session(cur, session_id)
        chat_bus.notify_chat_event(cur, session_id, message_id)


def _fetch_project_model(project_id: str) -> str:
    """프로젝트에 설정된 질문용 모델 별칭(없으면 빈 문자열) — 실제 별칭 해석은 호출부에서
    `ai_providers.resolve_requested_model` 이 요청값·전역 기본과 합쳐서 한다.
    """
    with tx() as cur:
        cur.execute("SELECT qa_model_name FROM project WHERE id=%s", (str(project_id),))
        proj = cur.fetchone()
    return (proj or {}).get("qa_model_name") or ""


def _collect_evidence_symbols(evidence: dict, limit: int = 3) -> list[str]:
    """도구 근거에서 "이 답에 실제로 관련된" 심볼 키를 뽑는다(§16 Contextual Risk 대상).

    semantic_search 적중이 질문과 가장 직접적이라 먼저, 그다음 dataflow sink/source,
    call_graph 대상 순. 상위 `limit`개로 제한해 Risk 평가 비용을 통제한다.
    """
    keys: list[str] = []

    def add(k: object) -> None:
        if isinstance(k, str) and k and k not in keys:
            keys.append(k)

    for row in evidence.get("semantic_search") or []:
        add(row.get("symbol_key"))
    for f in evidence.get("dataflow_security") or []:
        add(f.get("sink_symbol_key"))
        add(f.get("source_symbol_key"))
    for f in evidence.get("dataflow_business") or []:
        add(f.get("callee_symbol_key"))
        add(f.get("caller_symbol_key"))
    for p in evidence.get("call_graph") or []:
        add(p.get("symbol_key"))
    return keys[:limit]


def _run_pipeline_sync(question: str, ctx: dict, *, model: str, project_id: str,
                        on_step=None, forced_tool: str | None = None) -> dict:
    """run_agentic + check_relevance + (스캔이 있으면) Contextual Risk — 전부 동기/
    블로킹이라 하나로 묶어서 스레드풀에 한 번만 던진다(기존 /scans/{scan_id}/ask 와 같은 구성)."""
    with tx() as cur:
        used, evidence = orchestrator.run_agentic(cur, ctx, question, model=model,
                                                    project_id=project_id, scan_id=ctx["scan_id"],
                                                    on_step=on_step, forced_tool=forced_tool)
        evidence = orchestrator.check_relevance(question, evidence, model=model,
                                                  project_id=project_id, scan_id=ctx["scan_id"])

        risk = None
        scan_id = ctx.get("scan_id")
        if scan_id:
            symbols = _collect_evidence_symbols(evidence)
            if symbols:
                try:
                    risk = risk_synthesis.evaluate_query(
                        cur, scan_id, question, symbols,
                        model=model, project_id=project_id,
                    )
                except Exception:
                    log.exception("채팅 Contextual Risk 평가 실패 — 생략하고 진행")
    return {"used": used, "evidence": evidence, "risk": risk}


@observability.observe(name="project_chat_turn")
async def _handle_question(session_id: str, project_id: str, question: str, client_msg_id,
                            queue: asyncio.Queue, member_id: str, mentioned_project_ids: list[str],
                            forced_tool: str | None, model_override: str | None = None) -> None:
    """user 메시지 저장(→ NOTIFY로 세션 전원 브로드캐스트) → 파이프라인 실행 →
    답변 스트리밍(이 연결에만 전달) → assistant 메시지 저장(→ 다시 브로드캐스트).

    이 연결로 나가는 메시지는 전부 `queue`에 넣기만 한다 — 실제
    `websocket.send_json` 호출은 `chat_socket`의 `forward()` 태스크 하나만
    한다(모듈 docstring 참고). 큐에 넣는 것 자체는 실패하지 않으므로(무제한
    asyncio.Queue), 소켓이 이미 끊긴 경우는 `forward()` 쪽에서 처리한다.
    """
    observability.tag_current_trace(session_id=session_id, project_id=project_id)
    try:
        ctx = await run_in_threadpool(_insert_user_message, session_id, project_id, question, member_id,
                                       mentioned_project_ids, forced_tool)
        project_model = await run_in_threadpool(_fetch_project_model, project_id)
        # 드롭다운에서 고른 모델(요청) → 프로젝트 기본 → 전역 기본. 게이트웨이가
        # 서빙하지 않는 값은 조용히 기본으로 폴백한다(fail-open).
        model = await run_in_threadpool(
            ai_providers.resolve_requested_model, model_override, project_default=project_model
        )

        if ctx.get("is_first_message"):
            asyncio.create_task(
                _generate_chat_title_task(session_id, project_id, question, model, queue)
            )

        # run_agentic은 스레드풀(별도 스레드)에서 도는데 매 스텝 진행 상황을
        # 이 연결에 실시간으로 보내야 한다 — app/chat_bus.py의 백그라운드
        # 리스너 스레드와 같은 스레드→루프 전달 문제라, 같은 해법(스레드-세이프
        # 콜백)을 쓴다. `queue.put_nowait`은 동기 함수라 `call_soon_threadsafe`로
        # 충분하다(코루틴을 새로 스케줄하는 `run_coroutine_threadsafe`보다 단순).
        loop = asyncio.get_running_loop()
        collected_events: list[AgentEvent] = []

        def emit_event(event: AgentEvent) -> None:
            collected_events.append(event)
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": "agent_event", "client_msg_id": client_msg_id, "event": event}
            )

        pipeline = await run_in_threadpool(_run_pipeline_sync, question, ctx,
                                            model=model, project_id=project_id, on_step=emit_event,
                                            forced_tool=forced_tool)
        evidence = pipeline["evidence"]
        tools_used = pipeline["used"]
        risk = pipeline.get("risk")

        # 아래 두 프레임은 이미 이 연결의 이벤트 루프 안(스레드풀 밖)에서 직접
        # 보내므로 emit_event(스레드-세이프 call_soon_threadsafe 경유)를 쓰지
        # 않고 queue.put을 바로 await한다 — collected_events에는 순서 보장을
        # 위해 수동으로 append한다.
        final_event = new_event(f"final-{client_msg_id}", "final_response", "running",
                                 summary="답변을 생성하는 중...")
        collected_events.append(final_event)
        await queue.put({"type": "agent_event", "client_msg_id": client_msg_id, "event": final_event})

        chunks: list[str] = []
        async for delta in orchestrator.synthesize_answer_stream(question, evidence,
                                                                    model=model, project_id=project_id,
                                                                    scan_id=ctx["scan_id"],
                                                                    executed_tools=[t["name"] for t in tools_used],
                                                                    analysis_available=bool(ctx["scan_id"])):
            chunks.append(delta)
            await queue.put({"type": "answer_chunk", "client_msg_id": client_msg_id, "delta": delta})

        answer = "".join(chunks).strip() or "답변을 생성하지 못했습니다."
        final_done = mark_done(final_event, summary="답변을 완료했습니다")
        collected_events.append(final_done)
        await queue.put({"type": "agent_event", "client_msg_id": client_msg_id, "event": final_done})
        if risk:
            # 이 답에 쓰인 코드의 Exposure 경계(§11, §16). 스트리밍 연결에만 로컬 전달 —
            # 완성된 assistant 메시지의 evidence 에 함께 저장되므로 히스토리에서도 복원된다.
            await queue.put({"type": "risk", "client_msg_id": client_msg_id, "risk": risk})
        await run_in_threadpool(_insert_assistant_message, session_id, project_id, answer,
                                 {"tool_results": evidence, "agent_events": collected_events,
                                  "risk": risk}, tools_used)
    except Exception:
        log.exception("채팅 질문 처리 실패 — session_id=%s", session_id)
        await queue.put({"type": "error", "client_msg_id": client_msg_id,
                         "error": "답변 생성 중 오류가 발생했습니다"})


@router.websocket("/ws/projects/{project_id}/chat/sessions/{session_id}")
async def chat_socket(websocket: WebSocket, project_id: UUID, session_id: UUID):
    if not ws_origin_allowed(websocket):   # 다른 사이트가 우리 쿠키로 여는 소켓(CSWSH) 차단
        await websocket.close(code=4403)
        return
    member = get_current_member_ws(websocket)
    if member is None:
        # 인증·권한 실패는 수락한 뒤 이유 코드로 닫는다 — 수락 전에 닫으면 브라우저엔 HTTP 403/1006 으로만
        # 보여 "로그인 만료(4401) → 로그인 화면" / "권한 없음(4403) → 재시도 중단" 을 구분할 수 없다.
        await websocket.accept()
        await websocket.close(code=4401)
        return
    try:
        await run_in_threadpool(_assert_session_access, project_id, session_id, member["id"])
    except Exception:
        await websocket.accept()
        await websocket.close(code=4403)
        return

    await websocket.accept()
    pid = str(project_id)
    sid = str(session_id)
    queue = chat_bus.bus.subscribe(sid)

    async def forward() -> None:
        # 이 연결에 실제로 send_json하는 유일한 태스크 — 스트리밍/브로드캐스트
        # 모두 여기로만 나간다(모듈 docstring 참고). 한 번의 전송 실패(예: 소켓이
        # 막 끊김)로 이 루프 자체가 죽으면 그 뒤로 이 연결엔 아무것도 전달되지
        # 않으므로, 실패해도 다음 이벤트를 계속 뺀다 — 어차피 소켓이 죽었으면
        # 곧 receive_json()이 WebSocketDisconnect로 바깥 루프를 끝낸다.
        while True:
            event = await queue.get()
            try:
                await websocket.send_json(event)
            except Exception:
                pass

    forwarder = asyncio.create_task(forward())
    # 비밀번호 변경·탈퇴 후에도 열린 소켓이 남지 않게 주기적으로 세션 재검증(무효면 4401 로 닫힘).
    watcher = asyncio.create_task(watch_ws_session(websocket, member))
    try:
        while True:
            try:
                data = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except Exception:
                continue  # 파싱 실패한 프레임 — 연결은 유지하고 무시

            if data.get("type") != "message":
                continue
            question = (data.get("question") or "").strip()
            client_msg_id = data.get("client_msg_id")
            if not question or len(question) > _MAX_QUESTION_LEN:
                await queue.put({"type": "error", "client_msg_id": client_msg_id,
                                 "error": "질문은 1~2000자여야 합니다"})
                continue
            # mentioned_project_ids/forced_tool은 둘 다 선택 필드 — 잘못된 값은
            # 에러로 막지 않고 조용히 무시한다(fail-open, 모듈 전체와 같은 방침).
            raw_mentions = data.get("mentioned_project_ids") or []
            mentioned_project_ids = [m for m in raw_mentions if isinstance(m, str)][:10]
            forced_tool = data.get("forced_tool")
            if forced_tool not in orchestrator._TOOL_NAMES:
                forced_tool = None
            # 잘못된 model 값은 에러로 막지 않는다 — resolve_requested_model 이 기본으로 폴백.
            model_override = data.get("model") if isinstance(data.get("model"), str) else None
            asyncio.create_task(_handle_question(sid, pid, question, client_msg_id, queue, member["id"],
                                                  mentioned_project_ids, forced_tool, model_override))
    finally:
        forwarder.cancel()
        watcher.cancel()
        chat_bus.bus.unsubscribe(sid, queue)
