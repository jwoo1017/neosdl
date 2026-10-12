-- =====================================================================
-- NeoSDL · 003_semantics.sql
-- WBS 2.2 조기 프로토타입 — 로컬 LLM(Ollama)으로 만드는 의미 계층.
--
-- symbol/symbol_edge(구조 계층, 001/002)는 그대로 정본으로 두고, 그 위에
-- "이 심볼이 무슨 역할/도메인인가"를 얹는 파생 테이블이다. LLM 판정은
-- 언제든 다시 낼 수 있어야 하므로 scan_id 단위로 붙고, 어떤 모델이
-- 냈는지(model)를 반드시 남긴다 — parser_version 을 스캔에 남기는 것과
-- 같은 이유(재현성/재검증 근거).
--
-- 수동 적용: docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/003_semantics.sql
-- =====================================================================

BEGIN;

CREATE TABLE symbol_semantic (
    scan_id      uuid        NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    symbol_key   text        NOT NULL,   -- symbol.symbol_key 참조(FK 아님 — symbol_key 는 텍스트 키)
    role         text,                   -- module 심볼에만 채움: 'controller'|'service'|'repository'|'util'|'test'|'config'|'other'
    domain       text,                   -- module 심볼에만 채움: 짧은 소문자 태그 (예: 'auth', 'payment')
    summary      text,                   -- module/function 둘 다: 한국어 1문장 요약
    confidence   real,
    model        text        NOT NULL,   -- 예: 'ollama/qwen2.5-coder:7b' — 나중에 큰 모델로 재검증할 근거
    generated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scan_id, symbol_key)
);

CREATE INDEX symbol_semantic_domain_idx ON symbol_semantic (scan_id, domain) WHERE domain IS NOT NULL;

COMMENT ON TABLE symbol_semantic IS
    '구조(symbol/symbol_edge) 위에 LLM이 얹는 파생 의미 계층. module 심볼 행은 '
    'role/domain/summary 모두 채우고, function/method 심볼 행은 summary만 채운다 '
    '(role/domain은 그 파일의 module 행을 따라간다 — 조회 시 JOIN).';

COMMIT;
