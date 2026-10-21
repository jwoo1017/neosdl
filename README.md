# NeoSDL — Day 1 (2026-08-31)

Phase 1 첫날. WBS **1 Graph DB 구축**과 **1.1 Repository 연동 및 코드 스캔 API**가
동시에 열리는 날이다. 이 스켈레톤은 그 두 항목의 뼈대이고, 실제로 한 번 돈다.

**오늘이 끝나는 조건** — Repository 하나를 등록하면 클론되고, 스캔을 요청하면
파일 목록이 적재되고, 그 전 과정이 감사 로그에 해시 체인으로 남는 것.
심볼 추출(1.2)은 09.03부터다. 오늘 거기까지 가려 하지 않는다.

---

## 오늘의 순서

### 1. 먼저 결정 두 개 — 코드보다 먼저다

**① GPU 서버 사양 확정 + 발주 요청** (오늘의 진짜 마감)

Phase 3 실착수는 10.06이다. 10.05는 개천절 대체공휴일이라 WBS상 3일짜리
인프라 구축이 실제로는 2일이다. 조달·입고·설치가 5주 안에 끝나야 하는데
사양이 아직 없다. 이게 늦으면 3.1 → 3.2 → 3.3 → 3.4가 전부 밀리고,
받아줄 버퍼는 Phase 4에 없다.

사양을 좁히는 질문은 세 개다.

- Qwen3-Coder-Next는 MoE에 active 3B다. 어느 정밀도로 서빙할 것인가 (BF16 / FP8 / AWQ)
- 목표 context 길이는 얼마인가 — 262K를 다 쓸 것인가, 32K로 자를 것인가 (KV 캐시가 VRAM을 지배한다)
- 동시 요청 수 — 데모용 1~2 세션인가, 사내 개발자 다수인가

이 셋이 정해지면 VRAM이 나오고, VRAM이 정해지면 카드 수가 나온다.
확답이 어려우면 **오늘은 "10월 첫째 주 입고" 조건으로 견적부터** 올린다.
사양은 견적서 회신을 받는 동안 좁혀도 된다. 리드타임은 되돌릴 수 없다.

**② GitLab이냐 GitHub이냐 — 결정: 둘 다 구현**

WBS 1.1의 완료 기준은 "GitLab Repository 등록 및 스캔 실행"이고 BM 기획안의
시나리오도 GitLab Merge Request 기준인데, 인증·계정 설계는 GitHub App + OAuth로
잡혀 있었다. 하나만 고르면 수집 API/웹훅을 나중에 양쪽으로 다시 만들게 될
가능성이 있어, Phase 1부터 두 provider의 클론 인증을 모두 구현했다.

스키마는 이미 양쪽을 받게 해뒀다 (`repository.provider`). `worker/handlers/clone.py`가
`repository.provider` 값을 보고 GitLab(`oauth2:{GITLAB_TOKEN}@...`) 또는
GitHub App(installation access token, 1시간 만료라 클론/fetch마다 재발급)
인증을 URL에 주입한다. `.env`의 `SCM_PROVIDER`는 `POST /repositories`에서
provider를 생략했을 때의 기본값으로만 쓰인다.

> 참고: GitHub App installation_id는 저장하지 않는다. 토큰이 어차피 1시간마다
> 재발급이라 클론 시점마다 `GET /repos/{namespace}/{name}/installation`으로
> 조회한다 — 스키마에 GitHub 전용 컬럼을 추가하지 않기 위한 선택이다.

### 2. 기동

```bash
cp .env.example .env          # GITLAB_TOKEN 또는 GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY_PATH 채울 것
docker compose -f docker-compose.dev.yml up -d   # postgres + neo4j(선택) + litellm(LLM 게이트웨이)
pip install -r requirements.txt

uvicorn app.main:app --reload --port 8000    # 터미널 1
python -m worker.main                        # 터미널 2
```

`sql/*.sql`은 컨테이너 첫 기동 시 번호 순으로 자동 적용된다. 게이트웨이 전용 DB
`litellm`은 `litellm-init` 사이드카가 만든다(멱등). 이미 볼륨이 있다면 수동으로:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/001_init.sql
# ... 그리고 sql/019_ai_call_tokens.sql 까지 순서대로
```

### 2.1 LLM 게이트웨이 (LiteLLM Proxy)

모든 LLM 호출(채팅·의미 태깅·임베딩·프로젝트 스펙)은 `docker-compose`의 `litellm`
서비스(`:4000`)를 통과한다. 모델 목록은 `litellm/config.yaml`에서 정의하고,
프론트 드롭다운은 `GET /models`로 받아온다.

```bash
# 1) 프로바이더 키를 .env에 채운다 (채운 만큼만 해당 모델이 살아난다)
#    ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY / ...
#    키가 하나도 없어도 local-qwen-coder(Ollama)로 동작한다.

# 2) 게이트웨이 상태 확인
curl -s localhost:4000/health/liveliness
curl -s localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq '.data[].id'

# 3) (운영 기준) 앱 전용 virtual key 발급 → .env의 LITELLM_API_KEY 에 넣는다
curl -s -X POST localhost:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H 'content-type: application/json' \
  -d '{"models":[],"max_budget":50,"rpm_limit":120}' | jq -r .key
#    개발 중엔 LITELLM_API_KEY 에 그냥 $LITELLM_MASTER_KEY 를 넣어도 된다.

# 관리 UI: http://localhost:4000/ui  (LITELLM_MASTER_KEY 로 로그인) — 스펜드/키/로그
```

### 3. 한 바퀴 돌려보기

```bash
curl -s localhost:8000/healthz
# {"ok":true,"graph_backend":"postgres_cte"}   ← Neo4j 없이도 뜬다

REPO=$(curl -s -X POST 'localhost:8000/repositories?actor=user:jw.ji' \
  -H 'content-type: application/json' -d '{
    "remote_id":"4021","namespace":"finble","name":"core-banking",
    "clone_url":"https://gitlab.example.com/finble/core-banking.git"
  }' | jq -r .id)

curl -s -X POST "localhost:8000/repositories/$REPO/scans?actor=user:jw.ji" \
  -H 'content-type: application/json' \
  -d '{"commit_sha":"<40자 SHA>","ref":"refs/heads/main"}'

curl -s "localhost:8000/scans/<scan_id>" | jq
```

감사 체인 검증:

```sql
SELECT bool_and(ok) FROM audit_log_verify();
```

---

## 스키마가 이미 강제하고 있는 것

`sql/001_init.sql` 하나에 확정된 기술 결정이 전부 박혀 있다. 나중에 말로만
남는 규칙은 반드시 깨지므로, 깨지면 INSERT가 실패하도록 만들어 두었다.

| 결정 | 어디에 박혀 있나 |
|---|---|
| symbol_key 규격 | `symbol_key_of()` 함수 하나가 유일한 정의. `symbol.symbol_key`는 이 함수의 generated column이라 애플리케이션이 다른 규격으로 키를 만들 수 없다 |
| 라인번호·커밋SHA를 키에서 배제 | `start_line` / `end_line` / `body_hash`는 키 밖의 참고 컬럼 |
| 스캔 1회 = 커밋 1개 = 불변 스냅샷 | `scan_commit_uniq (repository_id, commit_sha)` |
| 미해석 엣지 보존 | `symbol_edge.resolved` + `confidence` + `dst_hint`, `symbol_edge_resolved_ck`가 둘 중 하나는 반드시 있도록 강제 |
| PostgreSQL 정본 / Neo4j 파생 | `graph_projection.stale`. 이 테이블이 통째로 비어도 판정은 성립해야 한다 |
| Neo4j 없이 도는 폴백 | `callers_within()` — Neo4j의 `CALLS*1..N`에 대응하는 Recursive CTE 구현 |
| 감사로그 해시 체인 | `audit_log_chain()` 트리거가 해시를 계산하고, `audit_log_immutable()`이 UPDATE/DELETE를 막는다 |
| PostgreSQL 테이블 큐 | `job_dequeue()` — `FOR UPDATE SKIP LOCKED`. Redis 없음 |

`callers_within()`이 반환하는 `path` 배열이 그대로 **Risk Evidence**가 된다.
Phase 2에서 근거 생성 로직을 따로 만들 필요가 없다는 뜻이다.

```sql
SELECT hops, symbol_key, path
FROM callers_within('<scan_id>', '<핵심 IP 심볼키>', 3)
ORDER BY hops;
```

---

## GraphStore — Neo4j 는 이 뒤에만 있다

그래프 접근은 전부 `app/graph/`의 `GraphStore` 뒤로 보냈다. 애플리케이션 코드
어디에서도 `neo4j` 를 직접 import 하지 않는다.

```
app/graph/__init__.py      GraphStore 프로토콜 · CallerPath · make_graph_store()
app/graph/postgres.py      postgres_cte 구현 (callers_within() 래핑)
app/graph/neo4j_store.py   Neo4j Bolt 구현 + 투영
worker/handlers/project.py graph.project 잡
```

`NEO4J_URI` 가 비어 있으면 `make_graph_store()` 가 조용히 `postgres_cte` 를
돌려준다. 예외를 던지지 않는다 — 그래프 계층 부재는 오류가 아니라 정상적인
배포 형태다. Neo4j 를 켜 둔 상태에서 서버가 죽어도 마찬가지다.
확인해 둔 동작:

```
WARNING graph.neo4j   Neo4j 응답 없음: Connection refused
WARNING worker.project graph backend(neo4j) 응답 없음 — 투영 건너뜀
graph_projection: {'backend':'neo4j', 'stale':True, 'error':'backend unreachable'}
scan 상태: 영향 없음
```

**계약은 하나다 — 두 구현이 같은 데이터에서 같은 답을 낸다.**
`path` 는 양쪽 모두 `[호출자 → ... → 대상]` 순서다. DB 함수는 대상에서 바깥으로
쌓아 반대 순서로 내놓으므로, 검증된 SQL 을 건드리지 않고
`PostgresGraphStore` 에서 뒤집어 계약을 맞춘다.

Community 제약을 우회한 자리 두 곳도 코드에 적어 두었다.

| 제약 | 우회 |
|---|---|
| 멀티 DB 없음 | 스캔 격리를 데이터베이스가 아니라 `scan_id` 속성으로 |
| 복합 유니크 제약의 CE 지원이 버전마다 다름 | `uid = scan_id\|symbol_key` 단일 속성 유일성 |
| RBAC 없음 | 코드 원문·민감정보 값을 아예 올리지 않는다 |

`depth` 는 Cypher 의 가변 길이 상한이라 파라미터 바인딩이 안 되고 문자열로
보간되는 유일한 자리다. `check_depth()` 를 반드시 통과시킨다. 관계 타입도
바인딩이 안 되므로 `EDGE_TYPE_TO_REL` 화이트리스트로만 보간한다.

### 동치성 테스트 — 이게 제일 중요하다

Neo4j Community 를 쓰면서 GPLv3 에 묶이지 않는 논리 전체가 "Neo4j 없이도 같은
판정이 나온다"는 한 문장 위에 서 있다. 그 문장을 검사하는 곳은 여기뿐이다.

```bash
docker compose -f docker-compose.dev.yml up -d
NEO4J_URI=bolt://localhost:7687 python -m pytest tests/test_graph_equivalence.py -v
```

검사 항목 — depth 1~4 동일 결과, 순환 종료, 미해석 엣지 미투영,
재투영 멱등성, depth 주입 차단. **09.01 실제 Neo4j 서버에 붙여서 9개 전부 통과 확인.**

> `pytest` 콘솔 스크립트를 직접 실행하면 프로젝트 루트가 `sys.path`에 안 잡혀
> `app` 모듈을 못 찾는다. 반드시 `python -m pytest`로 실행할 것.
> `NEO4J_URI` 없이 돌리면 Neo4j 항목은 skip 되므로 통과해도 의미가 없다.

## 다음 작업이 붙을 자리

TODO 주석으로 표시해 두었다. 파일을 새로 만들 필요 없이 그 자리만 채우면 된다.

- **WBS 1.2** (09.03) tree-sitter 파싱 → `symbol` INSERT — `worker/handlers/parse.py`
- **WBS 1.3** (09.09) `symbol_edge` INSERT, 미해석은 `resolved=false`로 — 같은 파일
- **WBS 1.3** Neo4j 투영 잡 `graph.project` — `worker/handlers/` 에 신규

---

## 로컬 개발에서 사내 서버 LLM 쓰기

로컬 모델(`local-qwen-coder`, `local-embed`)은 사내 서버(finble-dev-01) GPU에서 돈다.
개발 PC에서는 Ollama나 로컬 litellm 없이, 서버 게이트웨이에 SSH 터널로 붙는다.
게이트웨이는 서버의 127.0.0.1:4000 에만 열려 있어서 방화벽 개방은 필요 없다.
WireGuard 접속이 먼저 돼 있어야 한다.

```bash
# 터미널 하나를 띄워 두기 (개인 계정, docker 권한 불필요)
ssh -N -L 14000:127.0.0.1:4000 -i ~/.ssh/id_ed25519_finble_jwji jwji@<서버IP>
```

`.env`:

```
LITELLM_BASE_URL=http://localhost:14000
LITELLM_API_KEY=<서버 게이트웨이에서 발급받은 개발용 virtual key>
DEFAULT_CHAT_MODEL=local-qwen-coder
```

확인: `curl -H "Authorization: Bearer $LITELLM_API_KEY" localhost:14000/v1/models`.
이때 `docker compose -f docker-compose.dev.yml up -d postgres neo4j`만 띄우면 된다.

## 운영 배포 (finble-server)

구조는 로컬과 같다. 같은 오리진에서 `/api`는 백엔드로, 나머지는 SPA로 간다.
로컬에서는 Vite 프록시가, 운영에서는 호스트 nginx가 이 일을 한다.

| 구성 | 실행 방식 | repo 원본 |
|---|---|---|
| backend / worker | systemd `neosdl-backend`·`neosdl-worker` (User=neosdl, 127.0.0.1:8000) | `deploy/systemd/` |
| web | 호스트 nginx `10.0.0.5:80`, 정적 파일 `/var/www/neosdl` | `deploy/nginx/neosdl.conf` |
| DB | `infra/compose.yaml`의 `neosdl-postgres` (172.20.1.10, 관리자 관리) | repo 밖 |
| LLM | Docker `neosdl-llm` 프로젝트: litellm·vllm·embed·langfuse (GPU) | `deploy/compose.llm.yml` |
| 로그 | `/data/neosdl/logs/{backend,worker}.log` | `deploy/logrotate/neosdl` |

평소 배포는 main에 push한 뒤 서버에서 두 단계로 실행한다.

```bash
cd /srv/projects/neosdl/app
deploy/deploy.sh build                # jwji: git → pip → 프론트 빌드 → /var/www/neosdl
sudo deploy/deploy.sh release         # finbleadmin: pg_dump → migrate → 재시작 → /healthz
```

- 롤백은 `build <이전 sha>` 후 `release`를 한다. DB 스키마는 되돌리지 않으니, 필요하면 `/backup/neosdl/postgres/predeploy_*.dump`로 복구한다.
- 새 테이블이나 컬럼은 `sql/`에 다음 번호 파일로 추가한다. `release`가 `deploy/migrate.py`로 미적용분만 적용한다.
- 마이그레이션은 앱 계정(`neosdl_app`, DDL 권한 없음)이 아니라 별도 계정으로 실행한다.
  접속 정보는 root 전용 파일 `/srv/projects/neosdl/secrets/migrate.env`에 둔다(템플릿: `deploy/migrate.env.example`).
  새 테이블을 만들었으면 앱 계정에 필요한 권한(GRANT)도 같은 마이그레이션 파일에서 준다.
  빠뜨리면 `migrate.py`가 경고하고 실패해서, 재시작 전에 멈춘다.
  운영 DB에 처음 적용할 때는 `--baseline NNN`이 필요하다.
- 프로필 사진 같은 업로드 파일은 `UPLOAD_ROOT`(서버: `/data/neosdl/uploads`, neosdl:neosdl 2770) 아래에 저장된다.
  DB에는 키(`avatars/<uuid4>.webp`)만 저장한다. 앱 `.env`에 `UPLOAD_ROOT=/data/neosdl/uploads`를 넣는다.
- unit이나 nginx 파일을 바꿨으면 관리자가 `/etc`에 복사한 뒤 `daemon-reload`나 `nginx reload`를 한다(각 파일 상단 주석 참고).

LLM 스택은 앱 배포와 별개로 관리한다.

```bash
sudo docker compose -f deploy/compose.llm.yml --env-file deploy/.env.llm up -d
```

- 모델은 첫 기동 때 Hugging Face Hub에서 받아 `/fast/neosdl/models`에 저장된다. 서버에서 huggingface.co와 docker/ghcr 레지스트리로 나가는 HTTPS가 필요하다.
- 모델을 바꿀 때는 `.env.llm`의 `VLLM_MODEL`과 `litellm/config.internal.yaml`의 `local-qwen-coder`를 같이 바꾼다.
- 앱 `.env`에는 `LITELLM_BASE_URL=http://127.0.0.1:4000`과 게이트웨이에서 발급한 virtual key를 넣는다.
- Langfuse UI는 `ssh -N -L 3000:127.0.0.1:3000 ...` 터널을 연 뒤 `http://localhost:3000`에서 본다.
- GitHub App의 Callback/Setup URL은 `http://intranet.finble.internal/api/auth/github/...`로 등록한다.

### 실시간(WebSocket)

채팅(`/api/ws/projects/.../chat/sessions/...`)과 마스코트(`/api/ws/repositories/.../activity`)는
페이지와 같은 주소의 `wss://`로 붙는다. 전체 경로는 이렇다.

브라우저 → WireGuard → pfSense(443 허용) → 호스트 nginx(TLS 종단, `/api/` Upgrade) → uvicorn

별도 포트나 방화벽 규칙은 필요 없다.

- **nginx**: `/api/`에 `proxy_http_version 1.1`, `Upgrade`/`Connection` 헤더, `proxy_read_timeout 3600s`가
  있어야 한다(`deploy/nginx/neosdl.conf`). uvicorn이 20초마다 ping을 보내 유휴 연결을 유지한다.
- **사내 인증서 신뢰**: 페이지는 인증서 경고를 무시하고 열 수 있어도, wss는 경고 없이 실패할 수 있다.
  직원 PC에 사내 CA(또는 서버 인증서)를 신뢰 루트로 설치한다.
- **보안**: 핸드셰이크 `Origin`이 `FRONTEND_URL`과 다르면 거절한다(CSWSH 방어). 열린 소켓도 60초마다
  세션을 재검증해서, 비밀번호 변경·탈퇴 후에는 닫는다(close 4401).
- **복구**: 백엔드의 Postgres LISTEN 연결은 DB가 재시작돼도 자동으로 재연결한다(`app/pg_listener.py`).
  브라우저는 지수 백오프(최대 30초)로 재접속하고, 4401이면 로그인 화면, 4403이면 재시도를 멈춘다.
- **점검**
  ```bash
  # nginx → 백엔드 Upgrade 경로. 응답이 101 이면 경로 정상(쿠키가 없어 곧바로 4401 로 닫힘).
  # 404/502 면 nginx 설정 문제, 403 이면 Origin 불일치.
  curl -k -i -N --http1.1 -H "Connection: Upgrade" -H "Upgrade: websocket" \
       -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==" \
       -H "Origin: https://intranet.finble.internal" \
       https://intranet.finble.internal/api/ws/repositories/00000000-0000-0000-0000-000000000000/activity
  sudo grep ' /api/ws/' /var/log/nginx/access.log | tail -5   # 실제 연결은 101 로 찍힌다
  grep -E "chat_bus|repo_activity_bus" /data/neosdl/logs/backend.log | tail   # LISTEN 시작/재연결 로그
  ```
- **향후 외부 공개**(UTM Reverse Proxy/HAProxy 경유): HAProxy에 `timeout tunnel 1h`를 설정한다.
  IPS(Suricata) 웹 룰이 WS 프레임을 오탐하지 않는지도 확인한다.

---

## 라이선스 주의

`docker-compose.dev.yml`의 Neo4j는 **개발자 로컬 전용**이다.
Neo4j Community는 GPLv3이므로 제품 설치 번들이나 컨테이너 이미지에 동봉하면
GPL 의무가 발생한다. 납품 시에는 고객사가 자체 기동한 인스턴스의 접속 정보를
`NEO4J_URI`로 받는다. `NEO4J_URI`가 비어 있으면 `graph_backend`는 자동으로
`postgres_cte`로 떨어지고, 제품은 그대로 동작한다.
