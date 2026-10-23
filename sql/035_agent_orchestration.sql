-- =====================================================================
-- NeoSDL · 035_agent_orchestration.sql
-- 자율 보안 검증(화이트박스 DAST)의 작업 계층 — Engagement → Task → SubTask →
-- Action → Artifact. 지금까지 작업은 고정 kind 의 job 큐뿐이었고, 에이전트가
-- 세운 목표/계획/행동/산출물이 영속되지 않았다(채팅 plan 은 메모리 → chat_message.evidence
-- 스냅샷뿐). 이 계층은 검증 run 을 1급 개념으로 만들어 중단·재개·감사·재현을 가능하게 한다.
--
-- 설계 원칙: 기존 job 큐(app/queue.py) 위에 얹는다(engagement.run/task.run/subtask.run
-- 이 job kind). Postgres 가 정본. 모든 보안 민감 전이는 audit_log(해시체인)에 남긴다.
-- PK 는 uuid(gen_random_uuid) — bigserial 이 아니라 시퀀스 GRANT 가 필요 없다.
-- =====================================================================
BEGIN;

CREATE TYPE engagement_mode   AS ENUM ('assistant', 'automation');
-- permission_mode: Claude Code 권한 모드와 동일(shift+tab 토글).
--   plan = 설계·정적 분석만(샌드박스 실행 안 함)
--   ask  = 기본. 샌드박스 실행/파괴 action 마다 사람 승인(→ awaiting_approval)
--   auto = 사전 승인, 자동 진행. 단 egress·비allowlist 이미지·마이그레이션/시드·레포 compose·
--          리소스 상향은 auto 라도 항상 차단/승인(하드 가드, app 레벨에서 강제).
CREATE TYPE permission_mode   AS ENUM ('plan', 'ask', 'auto');
CREATE TYPE engagement_status AS ENUM (
    'queued', 'planning', 'running', 'awaiting_approval', 'paused',
    'succeeded', 'failed', 'cancelled');
-- task/subtask 공유
CREATE TYPE work_status AS ENUM (
    'queued', 'running', 'blocked', 'succeeded', 'failed', 'skipped', 'cancelled');
CREATE TYPE agent_role AS ENUM (
    'orchestrator', 'planner', 'static_analyst', 'pentester',
    'verifier', 'reporter', 'adviser', 'reflector');
CREATE TYPE action_status AS ENUM ('running', 'done', 'error');
CREATE TYPE artifact_kind AS ENUM (
    'report', 'poc', 'log', 'capture', 'patch', 'finding', 'other');


-- Engagement = 하나의 검증 run 이자 권한/범위 엔티티. 검증 대상은 특정 scan(불변 정적 스냅샷).
-- session_id 는 나중에 채팅(assistant)을 engagement 로 흡수할 자리 — phase-1 에선 NULL.
CREATE TABLE engagement (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid        NOT NULL REFERENCES project(id)    ON DELETE CASCADE,
    repository_id   uuid                 REFERENCES repository(id) ON DELETE SET NULL,
    scan_id         uuid                 REFERENCES scan(id)       ON DELETE SET NULL,
    session_id      uuid                 REFERENCES chat_session(id) ON DELETE SET NULL,
    mode            engagement_mode   NOT NULL DEFAULT 'automation',
    permission_mode permission_mode   NOT NULL DEFAULT 'ask',
    goal            text,
    status          engagement_status NOT NULL DEFAULT 'queued',
    -- 범위/레이트 제한(검증 엔진이 대상 앱을 때릴 때)
    expires_at          timestamptz,
    rate_limit_rps      integer NOT NULL DEFAULT 2,
    max_requests        integer NOT NULL DEFAULT 200,
    allow_instrumentation boolean NOT NULL DEFAULT false,  -- IAST-lite 주입 허용 여부
    sandbox_ref     jsonb,                 -- 샌드박스(036) 핸들 요약 — 상세는 sandbox_session
    params          jsonb  NOT NULL DEFAULT '{}'::jsonb,   -- instructions 등 HITL 입력
    result          jsonb,
    error           text,
    created_by      uuid REFERENCES member(id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz
);
CREATE INDEX engagement_project_created_idx ON engagement (project_id, created_at DESC);
CREATE INDEX engagement_scan_idx ON engagement (scan_id);
CREATE INDEX engagement_active_idx ON engagement (status)
    WHERE status IN ('queued', 'planning', 'running', 'awaiting_approval', 'paused');


CREATE TABLE task (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id   uuid NOT NULL REFERENCES engagement(id) ON DELETE CASCADE,
    seq             integer NOT NULL,
    title           text,
    description     text,
    assigned_role   agent_role NOT NULL,
    status          work_status NOT NULL DEFAULT 'queued',
    depends_on      uuid[] NOT NULL DEFAULT '{}',   -- 선행 task 들(의존성 순서)
    input           jsonb,
    result          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX task_engagement_seq_idx ON task (engagement_id, seq);


-- SubTask 는 가변(HITL "서브태스크 패치" 대상). engagement_id 비정규화 — 버스 fan-out·sweep 용.
CREATE TABLE subtask (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         uuid NOT NULL REFERENCES task(id) ON DELETE CASCADE,
    engagement_id   uuid NOT NULL REFERENCES engagement(id) ON DELETE CASCADE,
    seq             integer NOT NULL,
    assigned_role   agent_role NOT NULL,
    status          work_status NOT NULL DEFAULT 'queued',
    input           jsonb,
    result          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subtask_task_seq_idx ON subtask (task_id, seq);


-- Artifact 를 agent_action 보다 먼저 만든다(action.evidence_artifact_id 가 참조).
CREATE TABLE artifact (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    engagement_id   uuid NOT NULL REFERENCES engagement(id) ON DELETE CASCADE,
    task_id         uuid REFERENCES task(id)    ON DELETE SET NULL,
    subtask_id      uuid REFERENCES subtask(id) ON DELETE SET NULL,
    kind            artifact_kind NOT NULL,
    title           text,
    content_type    text,
    -- 작은 산출물은 inline, 큰 바이너리(pcap/스크린샷)는 디스크 + 해시(변조 방지).
    body_text       text,
    body            jsonb,
    path            text,          -- {neosdl_workdir}/engagements/<engagement_id>/... 상대 경로
    sha256          char(64),
    size_bytes      bigint,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX artifact_engagement_kind_idx ON artifact (engagement_id, kind);


-- 도구/에이전트 1스텝(채팅 루프의 used/tool_results + decision_log 한 줄을 영속화한 것).
CREATE TABLE agent_action (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subtask_id      uuid NOT NULL REFERENCES subtask(id) ON DELETE CASCADE,
    engagement_id   uuid NOT NULL REFERENCES engagement(id) ON DELETE CASCADE,
    seq             integer NOT NULL,
    agent_role      agent_role NOT NULL,
    tool            text,                  -- 도구 레지스트리 이름(app/agent_tools.py)
    input           jsonb,
    output          jsonb,                 -- 작으면 inline
    evidence_artifact_id uuid REFERENCES artifact(id) ON DELETE SET NULL,  -- 크면 artifact
    status          action_status NOT NULL DEFAULT 'running',
    latency_ms      integer,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);
CREATE INDEX agent_action_engagement_created_idx ON agent_action (engagement_id, started_at);
CREATE INDEX agent_action_subtask_seq_idx ON agent_action (subtask_id, seq);


-- decision_log(034)를 engagement 에도 연결 — 실행 모니터링(StepMonitor/Reflector/Adviser)이
-- 자동화 run 의 결정도 같은 테이블에 남긴다.
ALTER TABLE decision_log ADD COLUMN engagement_id uuid REFERENCES engagement(id) ON DELETE CASCADE;
CREATE INDEX decision_log_engagement_idx ON decision_log (engagement_id, created_at DESC);


-- 운영: 앱 실행 계정(neosdl_app, DDL 권한 없음)에 필요한 권한만. uuid PK 라 시퀀스 GRANT 불필요.
-- status 전이·subtask 패치가 있어 UPDATE 포함. 삭제는 project/engagement CASCADE 로만.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neosdl_app') THEN
        GRANT SELECT, INSERT, UPDATE ON engagement, task, subtask, agent_action, artifact TO neosdl_app;
    END IF;
END
$$;

COMMIT;
