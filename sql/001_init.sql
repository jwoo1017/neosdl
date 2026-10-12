-- =====================================================================
-- NeoSDL · 001_init.sql
-- Phase 1 / WBS 1 · Graph DB 구축 — 초기 스키마
--
-- 설계 전제 (확정된 기술 결정)
--   · PostgreSQL 17 + pgvector 가 정본(System of Record).
--     Neo4j 는 여기서 언제든 재구축 가능한 파생 조회 계층이므로
--     이 스키마만으로 모든 판정이 성립해야 한다.
--   · 스캔 1회 = 커밋 1개 = 불변 스냅샷. 스캔 결과는 UPDATE 하지 않는다.
--   · symbol_key = {lang}:{repo_id}:{path}#{qualified_name}({param_types})
--     라인번호 · 커밋SHA · 내용해시는 키에 넣지 않는다.
--     코드가 바뀌어도 같은 함수는 같은 키로 남아야 정책 자동화가 성립한다.
--   · 미해석 Call Graph 엣지는 버리지 않고 resolved=false + confidence 로 보존한다.
--   · 감사 로그는 append-only 해시 체인.
-- =====================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- ---------------------------------------------------------------------
-- 열거형
-- ---------------------------------------------------------------------
CREATE TYPE scm_provider   AS ENUM ('gitlab', 'github');
CREATE TYPE scan_status    AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');
CREATE TYPE scan_trigger   AS ENUM ('manual', 'webhook', 'schedule', 'backfill');
CREATE TYPE symbol_kind    AS ENUM ('module', 'class', 'interface', 'function', 'method', 'field');
CREATE TYPE edge_type      AS ENUM ('calls', 'imports', 'extends', 'implements', 'references');
CREATE TYPE job_status     AS ENUM ('queued', 'running', 'done', 'failed', 'dead');
CREATE TYPE projection_backend AS ENUM ('neo4j', 'postgres_cte');

-- ---------------------------------------------------------------------
-- 1. Repository
--    provider 컬럼으로 GitLab / GitHub 를 모두 받는다.
--    진입점 결정이 늦어져도 스키마는 막히지 않는다.
-- ---------------------------------------------------------------------
CREATE TABLE repository (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider        scm_provider NOT NULL,
    remote_id       text         NOT NULL,          -- provider 쪽 project/repo id
    namespace       text         NOT NULL,          -- group / org
    name            text         NOT NULL,
    clone_url       text         NOT NULL,
    default_branch  text         NOT NULL DEFAULT 'main',
    is_active       boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT repository_remote_uniq UNIQUE (provider, remote_id)
);

COMMENT ON COLUMN repository.remote_id IS
    'GitLab project id 또는 GitHub repository node id. provider 와 함께 유일.';

-- ---------------------------------------------------------------------
-- 2. Scan — 불변 스냅샷
--    같은 커밋을 두 번 스캔하지 않는다 (UNIQUE 로 강제).
-- ---------------------------------------------------------------------
CREATE TABLE scan (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repository_id   uuid        NOT NULL REFERENCES repository(id) ON DELETE CASCADE,
    commit_sha      char(40)    NOT NULL,
    ref             text        NOT NULL,           -- refs/heads/main, MR source branch 등
    trigger         scan_trigger NOT NULL DEFAULT 'manual',
    status          scan_status  NOT NULL DEFAULT 'queued',
    parser_version  text        NOT NULL,           -- tree-sitter 문법 버전. 재현성의 근거
    started_at      timestamptz,
    finished_at     timestamptz,
    stats           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    error           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT scan_commit_uniq UNIQUE (repository_id, commit_sha)
);

CREATE INDEX scan_repo_created_idx ON scan (repository_id, created_at DESC);
CREATE INDEX scan_status_idx       ON scan (status) WHERE status IN ('queued', 'running');

-- ---------------------------------------------------------------------
-- 3. Code File
-- ---------------------------------------------------------------------
CREATE TABLE code_file (
    id            bigserial PRIMARY KEY,
    scan_id       uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    path          text    NOT NULL,
    lang          text    NOT NULL,                  -- java | python | typescript | other
    size_bytes    integer NOT NULL,
    loc           integer NOT NULL DEFAULT 0,
    content_hash  char(64) NOT NULL,                 -- sha256. 스캔 간 변경 감지용
    is_parsed     boolean NOT NULL DEFAULT false,
    parse_error   text,
    CONSTRAINT code_file_path_uniq UNIQUE (scan_id, path)
);

CREATE INDEX code_file_lang_idx ON code_file (scan_id, lang);
CREATE INDEX code_file_hash_idx ON code_file (content_hash);

-- ---------------------------------------------------------------------
-- 4. Symbol — symbol_key 가 그래프의 노드 키가 된다
--    규격을 함수 하나에 가두고, generated column 이 그것을 강제한다.
--    애플리케이션도 같은 함수를 호출한다. 규격이 두 군데에 적히지 않는다.
-- ---------------------------------------------------------------------
CREATE FUNCTION symbol_key_of(
    p_lang   text,
    p_repo   uuid,
    p_path   text,
    p_qname  text,
    p_params text[]
) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT p_lang || ':' || p_repo::text || ':' || p_path
        || '#' || p_qname
        || '(' || coalesce(array_to_string(p_params, ','), '') || ')';
$$;

COMMENT ON FUNCTION symbol_key_of IS
    'symbol_key 규격의 유일한 정의. {lang}:{repo_id}:{path}#{qualified_name}({param_types}). '
    '라인번호·커밋SHA·내용해시는 들어가지 않는다.';

CREATE TABLE symbol (
    id              bigserial PRIMARY KEY,
    scan_id         uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    file_id         bigint  NOT NULL REFERENCES code_file(id) ON DELETE CASCADE,
    repository_id   uuid    NOT NULL REFERENCES repository(id) ON DELETE CASCADE,

    lang            text    NOT NULL,
    path            text    NOT NULL,
    qualified_name  text    NOT NULL,               -- com.finble.fraud.Scorer.score
    param_types     text[]  NOT NULL DEFAULT '{}',  -- ['String','BigDecimal']
    kind            symbol_kind NOT NULL,

    symbol_key text GENERATED ALWAYS AS (
        symbol_key_of(lang, repository_id, path, qualified_name, param_types)
    ) STORED,

    -- 위치와 내용은 참고 정보. 키에 관여하지 않는다.
    start_line      integer NOT NULL,
    end_line        integer NOT NULL,
    body_hash       char(64),                        -- 본문 변경 감지 (등급 재평가 트리거)
    signature_text  text,

    CONSTRAINT symbol_key_uniq UNIQUE (scan_id, symbol_key)
);

CREATE INDEX symbol_key_idx      ON symbol (symbol_key);
CREATE INDEX symbol_repo_key_idx ON symbol (repository_id, symbol_key);
CREATE INDEX symbol_file_idx     ON symbol (file_id);
CREATE INDEX symbol_qname_idx   ON symbol (qualified_name text_pattern_ops);

COMMENT ON COLUMN symbol.symbol_key IS
    '정책 자동화의 물리적 전제. 코드가 리팩토링되어도 같은 함수는 같은 키로 남는다.';

-- ---------------------------------------------------------------------
-- 5. Symbol Edge — Call / Dependency Graph (WBS 1.3)
--    dst 를 해석하지 못해도 버리지 않는다.
--    resolved=false 인 엣지는 나중에 정확도를 올릴 때 되살릴 재료다.
-- ---------------------------------------------------------------------
CREATE TABLE symbol_edge (
    id              bigserial PRIMARY KEY,
    scan_id         uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    src_symbol_key  text    NOT NULL,
    dst_symbol_key  text,                            -- 미해석이면 NULL
    dst_hint        text,                            -- 미해석일 때 원문 표기 (scorer.score)
    edge_type       edge_type NOT NULL,
    resolved        boolean NOT NULL DEFAULT false,
    confidence      real    NOT NULL DEFAULT 0.0,
    call_line       integer,
    evidence        jsonb   NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT symbol_edge_resolved_ck
        CHECK ( (resolved AND dst_symbol_key IS NOT NULL)
             OR (NOT resolved AND dst_hint IS NOT NULL) ),
    CONSTRAINT symbol_edge_confidence_ck
        CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

-- 해석된 엣지만 순회에 쓰인다
CREATE UNIQUE INDEX symbol_edge_uniq
    ON symbol_edge (scan_id, src_symbol_key, dst_symbol_key, edge_type)
    WHERE resolved;

CREATE INDEX symbol_edge_src_idx ON symbol_edge (scan_id, src_symbol_key) WHERE resolved;
CREATE INDEX symbol_edge_dst_idx ON symbol_edge (scan_id, dst_symbol_key) WHERE resolved;
CREATE INDEX symbol_edge_unresolved_idx
    ON symbol_edge (scan_id, dst_hint) WHERE NOT resolved;

-- ---------------------------------------------------------------------
-- 6. Symbol Embedding (pgvector)
--    Phase 2 의 유사 케이스 매칭, Phase 3 의 Graph RAG 순위 매기기용.
--    차원은 실제 임베딩 모델 확정 후 조정한다.
-- ---------------------------------------------------------------------
CREATE TABLE symbol_embedding (
    scan_id     uuid    NOT NULL REFERENCES scan(id) ON DELETE CASCADE,
    symbol_key  text    NOT NULL,
    model       text    NOT NULL,
    embedding   vector(1024) NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scan_id, symbol_key, model)
);

CREATE INDEX symbol_embedding_hnsw
    ON symbol_embedding USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------
-- 7. Graph Projection — Neo4j 는 파생이라는 사실을 스키마로 남긴다
-- ---------------------------------------------------------------------
CREATE TABLE graph_projection (
    scan_id      uuid PRIMARY KEY REFERENCES scan(id) ON DELETE CASCADE,
    backend      projection_backend NOT NULL,
    node_count   integer NOT NULL DEFAULT 0,
    edge_count   integer NOT NULL DEFAULT 0,
    projected_at timestamptz,
    stale        boolean NOT NULL DEFAULT true,
    error        text
);

COMMENT ON TABLE graph_projection IS
    'Neo4j 로의 투영 상태. 이 테이블이 비어 있어도 판정은 PostgreSQL Recursive CTE 로 성립해야 한다.';

-- ---------------------------------------------------------------------
-- 8. Job Queue — Redis 없이 PostgreSQL 테이블 큐
--    FOR UPDATE SKIP LOCKED 로 워커 다중화.
-- ---------------------------------------------------------------------
CREATE TABLE job (
    id            bigserial PRIMARY KEY,
    kind          text        NOT NULL,     -- repo.clone / scan.parse / graph.project ...
    payload       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status        job_status  NOT NULL DEFAULT 'queued',
    priority      integer     NOT NULL DEFAULT 100,
    run_after     timestamptz NOT NULL DEFAULT now(),
    attempts      integer     NOT NULL DEFAULT 0,
    max_attempts  integer     NOT NULL DEFAULT 5,
    locked_by     text,
    locked_at     timestamptz,
    last_error    text,
    dedupe_key    text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- 같은 작업이 큐에 두 번 쌓이지 않게
CREATE UNIQUE INDEX job_dedupe_idx ON job (dedupe_key)
    WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running');

CREATE INDEX job_pickup_idx ON job (priority, run_after, id)
    WHERE status = 'queued';

-- 워커가 부르는 단 하나의 함수
CREATE FUNCTION job_dequeue(p_worker text, p_kinds text[] DEFAULT NULL)
RETURNS SETOF job
LANGUAGE sql VOLATILE AS $$
    UPDATE job SET
        status    = 'running',
        locked_by = p_worker,
        locked_at = now(),
        attempts  = attempts + 1,
        updated_at = now()
    WHERE id = (
        SELECT id FROM job
         WHERE status = 'queued'
           AND run_after <= now()
           AND (p_kinds IS NULL OR kind = ANY(p_kinds))
         ORDER BY priority, run_after, id
         FOR UPDATE SKIP LOCKED
         LIMIT 1
    )
    RETURNING *;
$$;

-- 죽은 워커가 물고 있던 작업 회수
CREATE FUNCTION job_reap(p_stale interval DEFAULT interval '10 minutes')
RETURNS integer
LANGUAGE sql VOLATILE AS $$
    WITH reaped AS (
        UPDATE job SET
            status     = CASE WHEN attempts >= max_attempts THEN 'dead'::job_status
                              ELSE 'queued'::job_status END,
            locked_by  = NULL,
            locked_at  = NULL,
            run_after  = now() + (interval '30 seconds' * attempts),
            last_error = coalesce(last_error, 'worker lease expired'),
            updated_at = now()
        WHERE status = 'running' AND locked_at < now() - p_stale
        RETURNING 1
    )
    SELECT count(*)::integer FROM reaped;
$$;

-- ---------------------------------------------------------------------
-- 9. Audit Log — append-only 해시 체인
--    UPDATE / DELETE 를 트리거로 막고, hash 는 DB 가 계산한다.
--    애플리케이션이 체인을 만들면 애플리케이션 버그가 곧 증거 훼손이다.
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    seq         bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor       text        NOT NULL,     -- user:jw.ji / system:worker-1
    action      text        NOT NULL,     -- repository.register / scan.start / ai.request.blocked
    subject     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    payload     jsonb       NOT NULL DEFAULT '{}'::jsonb,
    prev_hash   bytea,
    hash        bytea       NOT NULL
);

CREATE FUNCTION audit_log_chain() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_prev bytea;
BEGIN
    SELECT hash INTO v_prev FROM audit_log ORDER BY seq DESC LIMIT 1;
    NEW.prev_hash := v_prev;
    NEW.hash := digest(
        coalesce(encode(v_prev, 'hex'), '')
        || NEW.occurred_at::text || '|' || NEW.actor || '|' || NEW.action
        || '|' || NEW.subject::text || '|' || NEW.payload::text,
        'sha256'
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER audit_log_chain_trg
    BEFORE INSERT ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_chain();

CREATE FUNCTION audit_log_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (attempted %)', TG_OP;
END;
$$;

CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();

-- 체인 검증 — 감사 대응 시 이 한 줄이 증거가 된다
CREATE FUNCTION audit_log_verify()
RETURNS TABLE (seq bigint, ok boolean)
LANGUAGE sql STABLE AS $$
    SELECT a.seq,
           a.hash = digest(
               coalesce(encode(lag(a.hash) OVER (ORDER BY a.seq), 'hex'), '')
               || a.occurred_at::text || '|' || a.actor || '|' || a.action
               || '|' || a.subject::text || '|' || a.payload::text,
               'sha256')
    FROM audit_log a
    ORDER BY a.seq;
$$;

-- ---------------------------------------------------------------------
-- 10. 위험도 전파 — Neo4j 없이도 도는 폴백 (GraphStore 의 postgres_cte 구현)
--     Neo4j 의 CALLS*1..N 에 대응한다.
-- ---------------------------------------------------------------------
CREATE FUNCTION callers_within(
    p_scan_id uuid,
    p_target  text,
    p_depth   integer DEFAULT 3
)
RETURNS TABLE (symbol_key text, hops integer, path text[])
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE up AS (
        SELECT e.src_symbol_key AS symbol_key,
               1 AS hops,
               ARRAY[e.dst_symbol_key, e.src_symbol_key] AS path
          FROM symbol_edge e
         WHERE e.scan_id = p_scan_id
           AND e.resolved
           AND e.edge_type = 'calls'
           AND e.dst_symbol_key = p_target
        UNION ALL
        SELECT e.src_symbol_key,
               up.hops + 1,
               up.path || e.src_symbol_key
          FROM symbol_edge e
          JOIN up ON e.dst_symbol_key = up.symbol_key
         WHERE e.scan_id = p_scan_id
           AND e.resolved
           AND e.edge_type = 'calls'
           AND up.hops < p_depth
           AND NOT e.src_symbol_key = ANY(up.path)   -- 순환 차단
    )
    SELECT DISTINCT ON (symbol_key) symbol_key, hops, path
      FROM up
     ORDER BY symbol_key, hops;
$$;

COMMENT ON FUNCTION callers_within IS
    'Neo4j 의 MATCH p=(:Symbol)-[:CALLS*1..N]->(target) 에 대응하는 폴백 구현. '
    '반환된 path 가 곧 Risk Evidence 다.';

COMMIT;
