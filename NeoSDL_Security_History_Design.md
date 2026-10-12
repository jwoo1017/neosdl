# NeoSDL Security History — 설계 문서 (Phase 1)

> 대상 계획서: `NeoSDL_Security_History_Plan.md`
> 이 문서는 계획서 §10("DB 스키마를 먼저 확정하지 말고 개념과 UX를 먼저 정렬")의 산출물이다.
> 데이터 모델은 **제안**으로 서술한다. Phase 1 에서 실제 구현한 것은 §6 표의 첫 3개뿐이다.

---

## 1. 목적과 범위

오늘의 NeoSDL 은 **"커밋 X 시점에 무엇이 취약한가"**를 답한다(`security_finding`,
`/scans/{id}/security/findings`). Security History 는 여기에 **시간축과 브랜치축**을
얹어 다음을 답하게 한다.

| | 질문 |
|---|---|
| Q1 | 이번 커밋 때문에 보안 상태가 얼마나 나빠졌나? |
| Q2 | 이 취약점은 언제 처음 만들어졌나? |
| Q3 | 어느 브랜치에서 발생했나? |
| Q4 | 어떤 커밋/PR 을 거쳐 다른 브랜치로 전파됐나? |
| Q5 | 왜 main 까지 들어왔나 / 언제 감지됐나? |
| Q6 | 언제 어떤 변경으로 해결됐나? |
| Q7 | 현재 main 의 보안 상태는 과거보다 좋아졌나 나빠졌나? |

### Phase 1 명시적 비목표

- Risk Identity **생애주기 상태기계**(`risk_identity`/`risk_observation`/
  `risk_lifecycle_event` 테이블, Detected→…→Resolved 전이 기록) — Phase 2.
- **전파 그래프 UI**, Branch Security Map, Risk Lifecycle 상세 화면 — Phase 2.
- **커밋 DAG**(`git log --all --parents` 동기화) — 안 만든다. 전파는 PR merge 단위로만.
- **phase2 Risk**(business_ip / source_sensitivity / pii / secret / Exposure) — 제외.
  Security History 의 "Security" 는 `security_finding`(taint: SQLi / command injection /
  XSS / domain_crossing)만 다룬다.

### 사용자 결정 (요약)

| 결정 | 값 |
|---|---|
| 이번 라운드 | 이 설계 문서 + 백엔드 얇은 인프라 슬라이스 (프론트 없음) |
| 전파 추적 | **PR merge 단위** — `pull_request` 재사용 |
| Risk Identity | **결정론적 지문** — LLM 없음. rename → 새 identity(문서화된 한계) |
| "Security" + 점수 | `security_finding`만 + 타임라인 UI 전용 **합성 점수**(가중 severity 합) |

---

## 2. 개념 정의

각 개념: 정의 / 아닌 것 / 어느 기존 NeoSDL 객체를 확장하나.

### 2.1 Security Snapshot

`scan`(= 커밋 1개, 불변 스냅샷) 하나의 taint-finding 상태 **롤업**. `1 scan : 1 snapshot`.
담는 것: severity 카운트(critical/high/medium/low), 합성 점수, evidence 총량, 브랜치,
커밋 시각.

- **아닌 것**: finding 원본의 사본이 아니다(원본은 `security_finding` 그대로). 판정/근거를
  다시 계산하지 않는다 — `ensure_security_findings` 결과를 집계만 한다.
- **기반**: 신규 `security_snapshot` 테이블. `ensure_security_findings` + `scan.stats`
  플래그 멱등 패턴(`security_analyzed` → `snapshot_built`)을 그대로 따른다.
- **물질화 시점**: `worker/handlers/risk_build.py`(파이프라인). 웹훅 스캔은 전부
  `risk.build` 를 거치므로 매 커밋 스냅샷이 자동으로 쌓인다. 타임라인/디프 API 는
  과거 스캔에 대해 lazy 하게도 물질화한다.

### 2.2 Security Diff

스냅샷 두 개 사이의 **fingerprint 단위** 델타.

```
new[]        head 에만 있는 지문
resolved[]   base 에만 있는 지문
unchanged[]  양쪽에 있는 지문
severity_delta  {critical: Δ, high: Δ, medium: Δ, low: Δ}
score_delta     head 합성점수 − base 합성점수
affected_paths  new/resolved 지문의 sink·source·경유 심볼이 걸린 파일 경로
```

- **아닌 것**: git diff 가 아니다. 코드 라인 변경이 아니라 **위험의 등장/소멸**을 보여준다.
- **기반**: 신규 `security_finding.fingerprint` + `compute_diff()`
  (`app/security_history.py`). `app/releases.py:get_release_detail` 의 coarse
  finding-count 델타(`_finding_counts`)를 fingerprint 단위로 정밀화한 것.
- Phase 1 은 온디맨드 계산(미저장). base 기본값 = `scan.parent_scan_id`(sql/021).

### 2.3 Risk Identity

여러 커밋/브랜치에서 관측된 **하나의 지문**. 스캔마다 새로 생기는 위험이 아니라,
"같은 위험이 여기서도 관측됐다".

- Phase 1: `security_finding.fingerprint` 컬럼만. 별도 집계 테이블 없음.
- Phase 2: `risk_identity(repository_id, fingerprint, first_seen_*, category, severity,
  status, resolved_*, …)` — 지문당 한 행, 생애주기 상태 보유.

### 2.4 Risk Lifecycle

Risk Identity 가 프로젝트 시간축에서 거치는 순서 있는 상태.

```
Detected → Introduced → Observed → Propagated → Merged → Fixed → Resolved
```

Phase 1 은 **개념 + 각 전이가 어느 데이터에서 도출되는지**만 정의한다(§4). 테이블 없음.

| 상태 | 도출 근거 (Phase 2 쿼리) |
|---|---|
| Detected | 이 지문이 처음 나타난 스냅샷 (committed_at 최소) |
| Introduced | 그 스냅샷의 `branch` + `commit_sha` |
| Observed | 그 지문을 포함한 스냅샷들 (브랜치/커밋 목록) |
| Propagated | `head_ref = X` 인 merged `pull_request` 로 그 지문이 `base_ref` 브랜치 스냅샷에 등장 |
| Merged | `base_ref` = 기본 브랜치인 merged PR |
| Fixed | 기본 브랜치 스냅샷에서 그 지문이 사라진 첫 스냅샷 |
| Resolved | 기본 브랜치에서 N개 연속 스냅샷 동안 부재 확인 |

### 2.5 Branch Security Map

살아있는 브랜치별 현재 보안 상태(head 스냅샷 + open Risk Identity 수).

- Phase 1: 신규 `branch` 테이블(생애주기). 맵 뷰는 Phase 2.
- **삭제돼도 행 유지** — `deleted_at` 만 채운다. 과거 스냅샷이 "어느 브랜치였나"를 잃지 않게.

### 2.6 Security Timeline

브랜치별 스냅샷 시계열(**커밋 시각** 정렬 — 기존 `risk-history` 는 스캔 시각 정렬).

- Phase 1: `GET /repositories/{id}/security/timeline` API. 차트는 Phase 2.

---

## 3. 결정론적 fingerprint 스킴 (load-bearing)

`app/security_fingerprint.compute_fingerprint(finding: dict) -> str` 가 유일한 정의.

```
material = "v1" | kind | category
         | repo-UUID 제거한 sink symbol_key
         | source 패턴 클래스
         | 정규화된 dataflow 경로
fingerprint = sha1(material)[:16]
```

### 정규화 규칙

| 필드 | 정규화 | 이유 |
|---|---|---|
| sink `symbol_key` | `{lang}:{repo_uuid}:{path}#{qname}({params})` → `{lang}:{path}#{qname}({params})` | repo UUID 는 안정성에 방해(같은 코드가 다른 레포에 복사돼도 같은 위험). 라인/커밋/내용해시는 `symbol_key` 에 애초에 없음 (sql/001 `symbol_key_of`) |
| `source_var` | 소스 패턴 클래스(`http.req`, `http.request`, `stdin`, `env`, `argv`) 또는 숫자 제거 소문자 | `req.body.userId` 와 `req.body.email` 은 같은 위험. `arg1`/`arg2` 자동 이름 합침 |
| `path` (taint hop 배열) | hop `symbol_key` 시퀀스만(각각 repo-UUID 제거), 연속 중복 접기, `>` 조인. hop 의 `var`/`line` 버림 | 편집으로 라인/변수명이 바뀌어도 "같은 함수들을 거쳐 같은 sink 로" 는 유지 |

### 불변 / 가변

| 코드 변경 | 지문 |
|---|---|
| 함수 본문 안 라인 이동, 코드 삽입 | **불변** |
| 로컬 변수명 변경 (`userId` → `uid`) | **불변** |
| 커밋 SHA, 스캔 ID | **불변** |
| 다른 레포로 복사 | **불변** |
| **파일 rename** (`app/db.py` → `app/database.py`) | **새 지문** |
| **함수 rename** (`run_query` → `exec_query`) | **새 지문** |

### rename 한계 (문서화된 Phase 1 한계)

파일/함수 rename 시 `symbol_key` 의 `path`/`qualified_name` 이 바뀌므로 새 지문이 나온다.
그 Risk Identity 는 타임라인에서 "해결됨 + 새로 생김"으로 보인다.

- `symbol_key` 가 **이미** 가진 한계와 동일하다(sql/001: "코드가 리팩토링되어도 같은 함수는
  같은 키" 는 라인 이동까지만, path/qname 변경은 아님).
- 증분 스캔(`app/incremental.py`)이 rename 을 "옛 키 제거 + 새 키 추가"로 다루는 것과 동일.
- `v1` 접두사로 스킴 진화(Phase 3 의 rename 교차 식별 휴리스틱) 시 옛 지문과 조용히
  섞이지 않게 한다.

---

## 4. PR-merge 전파 모델

전파는 **PR merge 단위**로만 추적한다. 커밋 하나하나 체인은 만들지 않는다.

### Phase 1 이 저장하는 원재료

| | 어디 |
|---|---|
| 브랜치 생애주기 (name, head_sha, head_scan_id, first/last_seen, deleted_at, is_default) | 신규 `branch` |
| 위험 지문 | `security_finding.fingerprint` |
| 커밋별 위험 상태 (branch + committed_at + 지문 집합) | `security_snapshot` + `security_finding` |
| merge 사실 (base_ref, head_ref, head_sha, merged, merged_at) | 기존 `pull_request` (웹훅이 채움) |

### 도출 (Phase 2 쿼리, 지금 문서화)

지문 `F`가 **"브랜치 X 에서 introduced"**
: `branch = X` 인 가장 이른 스냅샷(committed_at 기준) 중 `F`를 포함하고, 그 시각 이전
  기본 브랜치 스냅샷에는 `F`가 없던 것.

`F`가 **"PR #N 으로 기본 브랜치 도달"**
: `base_ref` = 기본 브랜치, `head_ref` = X 인 merged `pull_request` 로,
  `F`가 그 PR 의 `head_sha` 스냅샷에 있고, `merged_at` **직후** 기본 브랜치 스냅샷에는
  있으나 **직전**에는 없던 것.

**다단계 전파** ("feature/pay → develop (PR #42) → main (PR #57)")
: merged PR 들을 `merged_at` 순서로 hop 하며 위 규칙을 반복.

### Risk Lifecycle 뷰 문장 (예시)

```
이 취약점은 9/3 feature/pay 의 a81c92f 커밋에서 처음 관측됐습니다.
req.body → parsed → query → db.execute() dataflow 가 새로 형성됐고,
PR #42 로 develop(9/5), PR #57 로 main(9/6)에 병합되며 main 에 전파됐습니다.
해당 기간 동안 같은 지문에 대한 차단/수정 커밋은 확인되지 않았습니다.
```

---

## 5. 합성 타임라인 점수

`app/security_score.synthetic_score(counts) = 10·n_critical + 5·n_high + 2·n_medium + 1·n_low`
(가중치는 `SCORE_WEIGHTS` 상수 한 곳, `open` finding 만).

> **phase2 Risk Vector 가 아니다.** phase2 는 의도적으로 단일 점수를 만들지 않는다
> (5개 차원 독립 — `NeoSDL_Security_History_Plan.md` 가 참조하는 phase2 §1.2).
> 이 점수는 "이번 커밋으로 보안이 좋아졌나 나빠졌나"를 **트렌드 라인 하나**로 보기
> 위한 롤업일 뿐, 정책 결정(외부 AI 노출 등)에 쓰지 않는다. UI/문서에서 항상
> "타임라인 롤업" 이라고 라벨한다.

---

## 6. 제안 데이터 모델

| 테이블 | Phase | 요지 |
|---|---|---|
| `branch` | **1 (구현됨)** | 웹훅 관측 브랜치 생애주기. `UNIQUE(repository_id, name)`, `deleted_at` |
| `security_snapshot` | **1 (구현됨)** | `scan_id` PK. severity 카운트 + score + branch + committed_at + n_evidence |
| `security_finding.fingerprint` | **1 (구현됨)** | `text` 컬럼 + `(repository_id, fingerprint)` 인덱스. 앱 계산 |
| `scan_branch(scan_id, branch_id)` | 2 (제안) | 같은 커밋이 여러 브랜치에서 스캔될 때의 m:n. Phase 1 은 `scan.ref` 파싱 + `pull_request.head_sha` 폴백으로 충분 |
| `risk_identity(repository_id, fingerprint, first_seen_scan_id, first_seen_at, category, severity, status, resolved_at, …)` | 2 (제안) | 지문당 한 행 + 생애주기 상태 |
| `risk_observation(fingerprint, scan_id, branch, severity, committed_at)` | 2 (제안) | 비정규화 fact — 타임라인/생애주기 빠른 조회용 |
| `risk_lifecycle_event(fingerprint, kind, scan_id, pr_id, at)` | 2 (제안) | Introduced/Propagated/Merged/Fixed 이벤트 로그 |

전부 ADD-only(sql/013·021 규칙과 동일).

---

## 7. UX 와이어프레임

각 뷰: **Phase 1 = API only, Phase 2 = UI**.

### 7.1 Security Timeline (1차 중심 화면)

```
┌─ SECURITY TIMELINE ─────────────────────── repo ▾   branch: [all ▾] ─┐
│  score                                                                │
│  200┤                                            ╭──●  main           │
│  120┤                        ╭──────●────────────╯                    │
│   60┤        ╭──●────●───────╯          ○╌╌╌╌╌╌●    develop (dashed)   │
│    0┼──●─────╯                                                        │
│     └──┬─────┬─────┬─────┬─────┬─────┬─────┬──►  committed_at         │
│      c1a2  9f3   4d1   ab8   77e   e12   55c                          │
│  ●=snapshot   hover → crit/high/med/low + score                      │
│  click point → Commit Diff view (그 스캔 vs parent)                   │
└─────────────────────────────────────────────────────────────────────┘
```
API: `GET /repositories/{id}/security/timeline?branch=<name>?` → `{branches, series}`.

### 7.2 Branch Security Map

```
┌─ BRANCH SECURITY MAP ──────────────────────────── repo ▾ ────────────┐
│  branch      head        score  crit high med low   open identities  │
│  main    ●   55c9f (now)   42     1    3   4   2          7           │
│  develop ●   e12aa (2d)    48     1    4   4   1          8  ▲ vs main │
│  feat/x  ⊘   deleted        —     —    —   —   —          —           │
│  click branch → Timeline (그 브랜치 필터)                             │
└─────────────────────────────────────────────────────────────────────┘
```
데이터: `branch` + 각 브랜치 `head_scan_id` 의 `security_snapshot`.

### 7.3 Commit Security Diff

```
┌─ COMMIT SECURITY DIFF ───────────  base 9f3c1a ▸ head 4d10ee ────────┐
│  score  120 → 128   (Δ +8)     crit +1   high 0   med +1   low 0     │
│                                                                      │
│  NEW (2)                                                             │
│   ⬤ critical  sql_injection      orders/repo.py#save_order ← req.body│
│   ⬤ medium    xss                web/render.js#tpl        ← req.query│
│  RESOLVED (1)                                                        │
│   ⬤ high      command_injection  jobs/run.py#exec_task              │
│  UNCHANGED (5)  ▸ expand                                             │
│                                                                      │
│  Affected files: orders/repo.py, web/render.js, jobs/run.py         │
└─────────────────────────────────────────────────────────────────────┘
```
API: `GET /repositories/{id}/security/diff?head_scan=<id>&base_scan=<id>?`.

### 7.4 Risk Lifecycle 상세

```
┌─ RISK LIFECYCLE ──────────  R:a1b2c3d4e5f6  sql_injection / critical ┐
│  Detected     scan 4d10ee   2026-08-14 09:22                        │
│  Introduced   branch feat/orders   commit 4d10ee                    │
│  Observed     6 snapshots  (feat/orders, develop)                   │
│  Propagated   feat/orders → develop   via PR #42  (08-18)           │
│  Merged       develop → main          via PR #57  (08-25)           │
│  Fixed        scan 77ee01   main      09-02                         │
│  Resolved     main 3개 연속 스냅샷에서 부재 확인                     │
│                                                                     │
│  Dataflow:  order.py#create ▸ repo.py#save_order ▸ .execute()       │
│  각 단계 → 해당 Commit / Branch / Evidence / Graph 로 이동           │
└────────────────────────────────────────────────────────────────────┘
```
Phase 2 — `risk_identity` + `risk_lifecycle_event` + 전파 도출 필요.

---

## 8. 기존 NeoSDL 매핑

| 개념 | 재사용 / 확장하는 것 |
|---|---|
| Snapshot 물질화 | `ensure_security_findings`(app/scan_queries.py) + `scan.stats` 플래그 패턴 |
| Diff | `app/releases.py:get_release_detail` coarse diff + `_finding_counts`; `github_app.compare_commits`(파일 델타) |
| Timeline | `GET /repositories/{id}/security/risk-history`(app/main.py:743) + `SecurityRiskHistoryPage.jsx` SVG stacked bar |
| Branch | `app/installations.py` 웹훅 `push`/`create`/`delete`; `scan.ref` 시맨틱; `repo_queries.list_repo_branches`(라이브, 캐시 없음) |
| 전파 | `pull_request` 테이블 + `_upsert_pull_request` + `POST /repositories/{id}/pull-requests/sync` |
| 증분 base | `scan.parent_scan_id`(sql/021), `incremental.resolve_parent_scan` |
| 지문 소스 클래스 | `app/dataflow.py SOURCE_PATTERNS` |

---

## 9. Phase 분해

### Phase 1 (구현 완료)

- `sql/022_security_history.sql` — `branch`, `security_snapshot`, `security_finding.fingerprint`
- `app/security_fingerprint.py` (순수), `app/security_score.py` (순수)
- `app/branches.py` — `parse_branch_name` / `record_branch_event` / `link_branch_head_scan` / `resolve_scan_branch`
- `app/security_snapshot.py` — `ensure_security_snapshot` / `backfill_fingerprints`
- `app/security_history.py` — `compute_diff` + `GET /security/diff` + `GET /security/timeline`
- `app/installations.py` — 웹훅 `push`/`create`/`delete` → `branch` + 최소 `git_commit`
- `app/scan_queries.py` — `ensure_security_findings` insert 시 fingerprint
- `worker/handlers/risk_build.py` — fail-safe 스냅샷 물질화
- 테스트: `test_security_fingerprint` (순수), `test_branches`, `test_security_snapshot`,
  `test_security_diff` (`-m integration`)

### Phase 2

`risk_identity` / `risk_observation` / `risk_lifecycle_event` 테이블 + populate 잡 ·
PR-merge 전파 도출 · 4뷰 UI(Timeline / Branch Map / Commit Diff / Risk Lifecycle) ·
`scan_branch` m:n · whitelist 반영 점수(`finding_whitelist` join).

### Phase 3+

rename 교차 식별 휴리스틱(`v2` 지문) · SCA/의존성 취약점을 History 범위에 포함 ·
보안 상태 급변 시 알림 · LLM History Reasoning ("왜 main 까지 들어갔나?" 자연어 답변).

---

## 10. 성공 기준 (Phase 1)

- [ ] `sql/022` 적용, `branch`/`security_snapshot`/`fingerprint` 존재
- [ ] 웹훅 push → `branch` 행 생성(브랜치 무관), 삭제 → `deleted_at` (행 유지)
- [ ] 웹훅 스캔 파이프라인 → `security_snapshot` 자동 물질화, `scan.stats.snapshot_built`
- [ ] `GET /security/timeline` → 브랜치별 스냅샷 시계열, `committed_at` 정렬
- [ ] `GET /security/diff?head_scan=<id>` → fingerprint 단위 new/resolved/unchanged +
      severity_delta + score_delta + affected_paths
- [ ] fingerprint: 라인 이동/변수명 변경엔 불변, 파일/함수 rename 엔 새 지문 (유닛테스트)
- [ ] 스냅샷/지문 계산 실패해도 스캔 파이프라인 무영향 (fail-safe)
- [ ] 기존 `/security/risk-history` + `SecurityRiskHistoryPage.jsx` 불변

Phase 2 이후 §12 의 Q1~Q7 에 실제 데이터로 답할 수 있게 된다.
