"""프로젝트 생성 마법사의 "자동 스펙 정리" — 가벼운 방식.

레포지토리를 실제로 스캔(app/orchestrator.py의 질의응답 파이프라인과 달리
scan/worker가 전혀 필요 없다)하지 않고, 레포 선택 단계에서 이미 받아온
GitHub 메타데이터(주 언어/설명/토픽)와 사용자가 입력한 프로젝트 소개만으로
LLM에게 태그/요약을 맡긴다. 프로젝트 생성 도중 곧바로 끝나야 하므로 이
방식을 골랐다 — 실제 코드 기반 요약(스캔 완료를 기다려야 함)은 범위 밖.
"""

from __future__ import annotations

import json
import logging

from app import ai_providers, llm_schemas
from app.config import settings

log = logging.getLogger("app.project_spec")

_PROMPT = """아래 정보를 보고 이 소프트웨어 프로젝트의 스펙을 정리하라.

{context}

아래 JSON 스키마로만 답하라 (다른 설명 금지, 코드블록 금지):
{{"summary": "2~3문장 한국어 요약", "tags": ["태그1", "태그2", ...]}}

규칙:
- summary는 이 프로젝트가 무엇을 하는지 한국어 2~3문장으로 요약한다. 정보가 부족하면
  아는 척하지 말고 알 수 있는 만큼만 적는다.
- tags는 기술 스택/도메인을 나타내는 3~6개의 짧은 단어. 한국어 또는 널리 쓰이는 영문
  고유명사(예: React, PostgreSQL, FastAPI)로 적는다. 중복/모호한 태그는 넣지 않는다.
"""


def _build_context(intro: str, repo_language: str | None, repo_description: str | None,
                    repo_topics: list[str]) -> str:
    lines = []
    if intro:
        lines.append(f"작성자가 입력한 프로젝트 소개: {intro}")
    if repo_language:
        lines.append(f"연결된 레포지토리의 주 언어: {repo_language}")
    if repo_description:
        lines.append(f"연결된 레포지토리의 GitHub 설명: {repo_description}")
    if repo_topics:
        lines.append(f"연결된 레포지토리의 GitHub 토픽: {', '.join(repo_topics)}")
    return "\n".join(lines) if lines else "(제공된 정보 없음)"


def generate_project_spec(*, intro: str, repo_language: str | None = None,
                           repo_description: str | None = None,
                           repo_topics: list[str] | None = None) -> dict:
    context = _build_context(intro, repo_language, repo_description, repo_topics or [])
    prompt = _PROMPT.format(context=context)

    try:
        raw = ai_providers.generate(
            "gateway", settings().default_chat_model, prompt,
            response_schema=llm_schemas.ProjectSpec, timeout=60, purpose="project_spec",
        )
        parsed = json.loads(raw)
        summary = str(parsed.get("summary") or "").strip()
        tags = [str(t).strip() for t in (parsed.get("tags") or []) if str(t).strip()][:8]
        return {"summary": summary, "tags": tags}
    except Exception:
        log.exception("프로젝트 자동 스펙 생성 실패 — 빈 결과로 대체")
        return {"summary": "", "tags": [repo_language] if repo_language else []}
