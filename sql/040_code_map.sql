-- =====================================================================
-- NeoSDL · 040_code_map.sql
-- 코드 지도(Code Map) 캐시 — graphify 클러스터링 결과(모듈 = 커뮤니티)와 요약 리포트.
-- 스캔 결과(symbol/symbol_edge)는 스캔 후 바뀌지 않으므로 스캔당 한 번 계산해 둔다
-- (app/graphify_adapter.py, GET /scans/{id}/code-map 첫 호출 시 lazy 계산).
-- engine 이 바뀌면(graphify 버전 변경) 다시 계산한다.
-- =====================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS code_map (
    scan_id      uuid        PRIMARY KEY REFERENCES scan(id) ON DELETE CASCADE,
    engine       text        NOT NULL,
    communities  jsonb       NOT NULL,   -- [{id, name, hub, domain, size, cohesion, top_dir, file_count}]
    membership   jsonb       NOT NULL,   -- {symbol_key: community_id}
    links        jsonb       NOT NULL,   -- [{source, target, weight}] 모듈 간 연결
    report       jsonb       NOT NULL,   -- {key_symbols, surprising, questions}
    created_at   timestamptz NOT NULL DEFAULT now()
);

COMMIT;
