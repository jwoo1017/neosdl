-- =====================================================================
-- NeoSDL · 006_pdg.sql
-- WBS 2 킥오프 — CPG(Code Property Graph) 개념 중 CFG/PDG 계층.
-- AST/Call Graph(symbol/symbol_edge)는 이미 001/002에 있다. 여기서는
-- 함수 본문 하나 안에서의 제어 흐름(CFG)과 그 위에서 계산되는 데이터/제어
-- 의존(PDG)을 추가한다 — 오늘 범위는 함수 내부(intra-procedural)까지다.
--
-- 수동 적용: docker exec -i neosdl-pg psql -U neosdl -d neosdl -f sql/006_pdg.sql
-- =====================================================================

BEGIN;

CREATE TABLE cfg_block (
    scan_id     uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    symbol_key  text    NOT NULL,
    block_id    integer NOT NULL,
    start_line  integer NOT NULL,
    end_line    integer NOT NULL,
    PRIMARY KEY (scan_id, symbol_key, block_id)
);

CREATE TABLE cfg_edge (
    scan_id       uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    symbol_key    text    NOT NULL,
    src_block_id  integer NOT NULL,
    dst_block_id  integer NOT NULL,
    branch_label  text    NOT NULL   -- seq|true|false|loop_back|exception|break|continue|case|fallthrough|no_match
);

CREATE INDEX cfg_edge_lookup_idx ON cfg_edge (scan_id, symbol_key);

-- 데이터 의존(reaching definitions). evidence_kind 로 불확실성의 "종류"를
-- 남긴다 — 숫자 confidence 대신, 무엇을 모델링하지 않아서 근사했는지가
-- 그대로 설명이 되게 한다(별칭/객체 필드/외부 함수 내부 동작 미상 등).
CREATE TABLE pdg_data_edge (
    scan_id       uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    symbol_key    text    NOT NULL,
    var_name      text    NOT NULL,
    def_line      integer NOT NULL,
    def_block_id  integer NOT NULL,
    use_line      integer NOT NULL,
    evidence_kind text    NOT NULL, -- DIRECT_ASSIGNMENT|ALIAS_ASSIGNMENT|FIELD_ALIAS|UNKNOWN_CALL
    def_text      text             -- 우변 원문(정리됨) — 보안 모드 source 패턴 매칭용
);

CREATE INDEX pdg_data_edge_lookup_idx ON pdg_data_edge (scan_id, symbol_key);
CREATE INDEX pdg_data_edge_var_idx ON pdg_data_edge (scan_id, symbol_key, var_name);

-- 함수 안에서 발견된 모든 호출식 — 보안 모드 sink 패턴 매칭용
-- (`db.execute(x)`가 대입문 우변이든 단독 호출문이든 상관없이 잡는다).
CREATE TABLE pdg_call (
    scan_id     uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    symbol_key  text    NOT NULL,
    block_id    integer NOT NULL,
    line        integer NOT NULL,
    target_text text    NOT NULL,
    arg_vars    text[]  NOT NULL DEFAULT '{}',  -- 인자 슬롯별 base identifier(포지션 정렬, 없으면 '?')
    arg_texts   text[]  NOT NULL DEFAULT '{}',  -- 인자 슬롯별 원문 — `db.execute(req.query.q)` 처럼
                                                 -- 변수 대입 없이 바로 넘기는 source 패턴을 직접 잡는 데 필요
    arg_lines   integer[] NOT NULL DEFAULT '{}' -- 인자 슬롯별 실제 줄 번호 — 호출식이 여러 줄에
                                                 -- 걸치면 line(호출식 시작 줄)과 달라진다. sink 매칭 시
                                                 -- pdg_data_edge.use_line과 여기를 맞춰봐야 한다.
);

CREATE INDEX pdg_call_lookup_idx ON pdg_call (scan_id, symbol_key);

-- 제어 의존. CFG 구조만으로 결정되는 그래프 알고리즘 결과라 evidence_kind가
-- 필요 없다 — 항상 결정론적이다.
CREATE TABLE pdg_control_edge (
    scan_id            uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    symbol_key         text    NOT NULL,
    branch_block_id    integer NOT NULL,
    dependent_block_id integer NOT NULL,
    branch_label       text    NOT NULL
);

CREATE INDEX pdg_control_edge_lookup_idx ON pdg_control_edge (scan_id, symbol_key);

-- 함수별 파라미터 이름(위치 순) — 레이어 4(절차간 연결)에서 호출 인자를
-- 위치로 매칭할 때 쓴다. self/this는 이미 제외됨(추출 단계에서 뺌).
CREATE TABLE pdg_function (
    scan_id     uuid NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    symbol_key  text NOT NULL,
    param_names text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (scan_id, symbol_key)
);

COMMENT ON TABLE cfg_block IS
    'CFG 기본 블록 — 함수/메서드 본문 하나(symbol_key)를 분기 경계로 쪼갠 것.';
COMMENT ON TABLE cfg_edge IS
    'CFG 제어 엣지. branch_label 로 seq/true/false/loop_back 등을 구분한다.';
COMMENT ON TABLE pdg_data_edge IS
    'Reaching-definitions 기반 데이터 의존 — def_line 의 정의가 use_line 의 '
    '사용 지점까지 재정의 없이 도달함. evidence_kind 는 불확실성의 종류.';
COMMENT ON TABLE pdg_control_edge IS
    'Post-dominator 기반 제어 의존 — dependent_block 이 branch_block 의 '
    '특정 분기(branch_label)에 의해서만 실행됨.';

COMMIT;
