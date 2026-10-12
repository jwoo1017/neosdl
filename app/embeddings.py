"""임베딩 — worker(symbol.embed)와 app(검색 엔드포인트) 양쪽에서 쓴다.

같은 모델로 인덱싱/질의 양쪽을 임베딩해야 코사인 거리가 의미가 있으므로,
호출 지점이 둘이어도 모델 이름과 호출 방식은 여기 한 곳에만 둔다.

다른 LLM 호출과 마찬가지로 LiteLLM 게이트웨이(OpenAI 호환 /v1/embeddings)를
통과한다. 게이트웨이의 `local-embed` 별칭이 mxbai-embed-large 로 라우팅되므로
반환 벡터 차원은 그대로 1024(`symbol_embedding vector(1024)`)다.
"""

from __future__ import annotations

import httpx

from app.config import settings


def embed_text(text: str) -> list[float]:
    key = settings().litellm_api_key
    headers = {"content-type": "application/json"}
    if key:
        headers["authorization"] = f"Bearer {key}"
    resp = httpx.post(
        f"{settings().litellm_base_url.rstrip('/')}/v1/embeddings",
        headers=headers,
        json={"model": settings().embed_model, "input": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def to_vector_literal(values: list[float]) -> str:
    """pgvector 텍스트 입력 형식. psycopg에 벡터 어댑터를 새로 등록하지 않고

    기존 코드베이스의 `%s::jsonb` 캐스트 관행처럼 `%s::vector` 캐스트로 넘긴다.
    """
    return "[" + ",".join(repr(v) for v in values) + "]"
