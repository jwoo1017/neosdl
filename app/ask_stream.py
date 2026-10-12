"""`/scans/{scan_id}/ask`를 SSE로 스트리밍하기 위한 파이프라인 + 프레임 인코딩.

`app/chat.py`(WebSocket 프로젝트 채팅)와 같은 `orchestrator.run_agentic(...,
on_step=...)` 훅과 `AgentEvent` 스키마(`app/agent_events.py`)를 그대로 쓰되,
이쪽은 단발성 request/response라 `chat_bus`의 다중 뷰어 브로드캐스트나
`chat_message` 영속화는 가져오지 않는다 — 이 엔드포인트를 부르는 연결은 항상
하나뿐이고, 질문마다 상태를 남기지 않는다(오늘의 동기 버전과 동일).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from app import ai_providers, observability, orchestrator
from app.agent_events import mark_done, new_event
from app.auth import assert_scan_access
from app.db import tx

log = logging.getLogger("app.ask_stream")


def load_ask_context(scan_id: UUID, member_id: str, requested_model: str | None = None) -> dict:
    """접근 권한 확인 + 질문에 필요한 컨텍스트(provider/model 포함) 조회.

    스트림을 열기 전에(라우트 코루틴 안에서) 호출해야 한다 — 여기서 던지는
    HTTPException(403/404)이 정상적인 HTTP 에러 응답이 되게 하려면
    `StreamingResponse`가 만들어지기 전에 실행되어야 한다(스트림이 한 번
    시작되면 이후 실패는 SSE 에러 프레임으로만 알릴 수 있고, 상태 코드를
    바꿀 수 없다).

    모델은 요청값(`requested_model`) → 프로젝트 기본(`qa_model_name`) → 전역
    기본 순으로 게이트웨이 별칭을 고른다. 게이트웨이가 서빙하지 않는 값은
    조용히 기본으로 폴백한다(과거의 501 하드 실패를 대체).
    """
    with tx() as cur:
        assert_scan_access(cur, scan_id, member_id, "viewer")
        cur.execute(
            """
            SELECT p.id AS project_id, p.name AS project_name, p.description AS project_description,
                   p.qa_model_provider, p.qa_model_name, s.repository_id
              FROM scan s
              JOIN project_repository pr ON pr.repository_id = s.repository_id
              JOIN project p ON p.id = pr.project_id
             WHERE s.id=%s LIMIT 1
            """,
            (str(scan_id),),
        )
        proj = cur.fetchone()
        project_id = str(proj["project_id"]) if proj else None
        provider = (proj or {}).get("qa_model_provider") or "gateway"
        model = ai_providers.resolve_requested_model(
            requested_model, project_default=(proj or {}).get("qa_model_name"),
        )

        project_info = {"name": proj["project_name"], "description": proj["project_description"]} if proj else None
        return {
            "scan_id": str(scan_id),
            "repository_id": (proj or {}).get("repository_id"),
            "project_info": project_info,
            "project_id": project_id,
            "provider": provider,
            "model": model,
        }


def _run_pipeline_sync(question: str, ask_ctx: dict, *, on_step) -> dict:
    """run_agentic + check_relevance — 동기/블로킹이라 스레드풀에 한 번만 던진다."""
    with tx() as cur:
        used, evidence = orchestrator.run_agentic(
            cur, ask_ctx, question,
            provider=ask_ctx["provider"], model=ask_ctx["model"],
            project_id=ask_ctx["project_id"], scan_id=ask_ctx["scan_id"],
            on_step=on_step,
        )
        evidence = orchestrator.check_relevance(
            question, evidence, provider=ask_ctx["provider"], model=ask_ctx["model"],
            project_id=ask_ctx["project_id"], scan_id=ask_ctx["scan_id"],
        )
    return {"used": used, "evidence": evidence}


def _sse(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


@observability.observe(name="scan_ask")
async def stream_answer(question: str, ask_ctx: dict) -> AsyncIterator[bytes]:
    """도구 루프 진행 상황(AgentEvent) → 답변 토큰 → 최종 결과 순으로 SSE 프레임을 흘려보낸다.

    `run_agentic`은 동기(psycopg) 코드라 스레드풀에서 돌아야 하는데, 그
    콜백(`on_step`)이 내보내는 이벤트는 이 async 제너레이터가 실시간으로
    떠서 내보내야 한다 — `app/chat.py`의 스레드→루프 브릿지
    (`call_soon_threadsafe`)와 같은 문제라 같은 해법을 쓴다. 다만 chat.py는
    별도의 `forward()` 태스크가 실제 전송을 전담하지만, 여기는 소비자가
    이 스트림 하나뿐이라 이 제너레이터 자신이 그 역할을 겸한다.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    result: dict = {}

    def emit_event(event) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "agent_event", "event": event})

    async def run_pipeline() -> None:
        try:
            result["pipeline"] = await run_in_threadpool(_run_pipeline_sync, question, ask_ctx, on_step=emit_event)
        except Exception:
            log.exception("질문 파이프라인 실행 실패 — 에러 프레임으로 종료")
            result["error"] = True
        finally:
            queue.put_nowait({"type": "__done__"})

    task = asyncio.create_task(run_pipeline())
    try:
        while True:
            item = await queue.get()
            if item["type"] == "__done__":
                break
            yield _sse(item)
    finally:
        # 클라이언트가 연결을 끊으면 이 제너레이터는 GeneratorExit으로 여기서
        # 닫힌다 — 그 상태에서 task를 await하면
        # "async generator ignored GeneratorExit"가 나므로 cancel만 하고
        # 끝낸다. 이미 스레드풀에서 돌고 있는 run_agentic 자체는 계속 실행돼
        # 완료되지만(취소 신호가 닿지 않음), 오늘의 완전 동기 버전도 마찬가지라
        # 새로운 제약은 아니다.
        if not task.done():
            task.cancel()

    if result.get("error") or "pipeline" not in result:
        yield _sse({"type": "error", "error": "질문 처리 중 오류가 발생했습니다"})
        return

    pipeline = result["pipeline"]
    evidence = pipeline["evidence"]

    final_event = new_event("final-response", "final_response", "running", summary="답변을 생성하는 중...")
    yield _sse({"type": "agent_event", "event": final_event})

    chunks: list[str] = []
    async for delta in orchestrator.synthesize_answer_stream(
        question, evidence, provider=ask_ctx["provider"], model=ask_ctx["model"],
        project_id=ask_ctx["project_id"], scan_id=ask_ctx["scan_id"],
        executed_tools=[t["name"] for t in pipeline["used"]],
        analysis_available=bool(ask_ctx["scan_id"]),
    ):
        chunks.append(delta)
        yield _sse({"type": "answer_chunk", "delta": delta})

    answer = "".join(chunks).strip() or "답변을 생성하지 못했습니다."
    yield _sse({"type": "agent_event", "event": mark_done(final_event, summary="답변을 완료했습니다")})
    yield _sse({
        "type": "done",
        "answer": answer,
        "tools_used": [t["name"] for t in pipeline["used"]],
        "evidence": evidence,
    })
