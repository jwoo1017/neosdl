"""symbol.embed — symbol_semantic.summary 를 벡터로 만들어 symbol_embedding 에 채운다.

symbol.annotate 가 만든 한국어 요약을 임베딩 소스로 쓴다(심볼 이름/시그니처
그 자체보다 "무슨 역할을 하는가"가 자연어 질의와의 유사도 매칭에 더
유효하다). annotate 완료 시점에만 의미 있는 요약이 존재하므로, 이 잡은
scan.parse 가 아니라 annotate.py 의 handle() 끝에서 체이닝된다.
"""

from __future__ import annotations

import logging

from app import incremental
from app.db import tx
from app.embeddings import embed_text, to_vector_literal
from app.config import settings

log = logging.getLogger("worker.embed")


def handle(payload: dict) -> None:
    scan_id = payload["scan_id"]
    model = f"ollama/{settings().ollama_embed_model}"

    with tx() as cur:
        # 증분: parent 에서 안 바뀐 파일 심볼의 임베딩을 그대로 복사(임베딩은 결정론적이라
        # 재계산과 동일). 그 뒤 아래 쿼리가 임베딩 없는 심볼(=새로 태깅된 변경 파일)만 뽑는다.
        parent_scan_id, changed_paths = incremental.read_incremental_context(cur, scan_id)
        if parent_scan_id is not None:
            cur.execute(
                """
                INSERT INTO symbol_embedding (scan_id, symbol_key, model, embedding, created_at)
                SELECT %s, se.symbol_key, se.model, se.embedding, se.created_at
                  FROM symbol_embedding se
                  JOIN symbol s ON s.scan_id = %s AND s.symbol_key = se.symbol_key
                 WHERE se.scan_id = %s AND s.path <> ALL(%s)
                ON CONFLICT (scan_id, symbol_key, model) DO NOTHING
                """,
                (scan_id, scan_id, parent_scan_id, list(changed_paths or [])),
            )

        cur.execute(
            """
            SELECT ss.symbol_key, ss.summary FROM symbol_semantic ss
             WHERE ss.scan_id=%s AND ss.summary IS NOT NULL
               AND NOT EXISTS (SELECT 1 FROM symbol_embedding se
                                WHERE se.scan_id=ss.scan_id AND se.symbol_key=ss.symbol_key
                                  AND se.model=%s)
            """,
            (scan_id, model),
        )
        rows = cur.fetchall()

    embedded = 0
    for row in rows:
        try:
            vector = embed_text(row["summary"])
        except Exception:
            log.exception("scan %s: symbol %s 임베딩 실패 — 건너뜀", scan_id, row["symbol_key"])
            continue

        with tx() as cur:
            cur.execute(
                """
                INSERT INTO symbol_embedding (scan_id, symbol_key, model, embedding)
                VALUES (%s, %s, %s, %s::vector)
                ON CONFLICT (scan_id, symbol_key, model) DO UPDATE
                    SET embedding = EXCLUDED.embedding, created_at = now()
                """,
                (scan_id, row["symbol_key"], model, to_vector_literal(vector)),
            )
        embedded += 1

    log.info("scan %s: 심볼 %d개 임베딩 완료", scan_id, embedded)
