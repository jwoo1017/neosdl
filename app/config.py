from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://neosdl:neosdl_dev@localhost:5432/neosdl"

    # 파생 조회 계층. 비어 있으면 GraphStore 는 postgres_cte 로 폴백한다.
    neo4j_uri: str | None = None
    neo4j_user: str | None = None
    neo4j_password: str | None = None

    # GitLab / GitHub 둘 다 구현됨. POST /repositories 에서 provider 를 생략했을 때의 기본값.
    scm_provider: Literal["gitlab", "github"] = "gitlab"
    gitlab_base_url: str = "https://gitlab.com"
    gitlab_token: str | None = None
    github_app_id: str | None = None
    github_app_private_key_path: str | None = None

    # --- GitHub OAuth 로그인 (App 설정의 "Identifying and authorizing users") ---
    github_oauth_client_id: str | None = None
    github_oauth_client_secret: str | None = None
    github_oauth_callback_url: str = "http://localhost:5173/api/auth/github/callback"
    github_app_webhook_secret: str | None = None
    github_app_slug: str | None = None  # https://github.com/apps/{slug}/installations/new

    # --- 가입 정책(app/signup_policy.py) ---
    # 이 도메인의 이메일은 바로 가입할 수 있다(정확히 일치, 하위 도메인 불허). 그 외 주소는
    # 그 주소로 온 초대가 있을 때만 게스트로 가입한다. 콤마로 구분.
    allowed_signup_domains: str = "aeonlab.kr,finble.io,zerokleek.com"
    # 도메인 밖이지만 회사 계정으로 취급할 개별 주소(시연용 등). 콤마로 구분, 기본은 비어 있음.
    allowed_signup_emails: str = ""

    # --- 팀 초대 링크 ---
    invite_ttl_days: int = 7

    # --- 초대 메일 발송 (SMTP) ---
    # smtp_host가 비어 있으면 발송을 건너뛰고 경고 로그만 남긴다 — 로컬 개발 환경에서
    # 메일 서버 없이도 초대 생성/링크 응답은 그대로 동작해야 한다.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "NeoSDL <no-reply@neosdl.local>"
    smtp_use_tls: bool = True

    # 세션 JWT 서명 키. 기본값 없음 — 없으면 시작 실패해야 한다(하드코딩 폴백은 키 노출).
    session_secret: str

    frontend_url: str = "http://localhost:5173"
    # 브라우저가 직접 보는 공개 주소 — 프론트/백엔드가 같은 오리진(Vite 프록시/nginx)
    # 뒤에 있으므로 frontend_url과 같은 호스트:포트다. 백엔드 프로세스 자체의
    # 내부 바인딩 주소(uvicorn --port)와 혼동하지 말 것.
    backend_base_url: str = "http://localhost:5173"

    # --- 로컬 LLM (Ollama) — WBS 2.2 의미 계층 프로토타입 ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:7b"
    # vector(1024) 컬럼(symbol_embedding)과 차원이 맞는 임베딩 모델.
    ollama_embed_model: str = "mxbai-embed-large"

    # --- LLM 게이트웨이 (LiteLLM Proxy) ---
    # 모든 LLM egress 는 이 게이트웨이의 OpenAI 호환 /v1 엔드포인트를 통과한다
    # (app/ai_providers.py). 모델은 litellm/config.yaml 의 model_name(공개 별칭)으로
    # 지정하고, 게이트웨이가 provider 호출 규격으로 번역한다.
    litellm_base_url: str = "http://localhost:4000"
    litellm_api_key: str = ""            # 게이트웨이 virtual key (없으면 인증 없이 시도)
    # 요청에 모델이 없거나 "auto" 일 때 쓰는 전역 기본값. 로컬 모델이라 키가 없어도 동작.
    default_chat_model: str = "local-qwen-coder"
    # 로컬 모델(local-*)의 컨텍스트 길이(토큰). vLLM --max-model-len(deploy/compose.llm.yml)과
    # dev Ollama num_ctx(litellm/config.yaml)에 맞춘다 — app/context_budget.py 예산의 기준.
    local_model_context: int = 8192
    # 임베딩 전용 게이트웨이 별칭 (vector(1024) 유지 위해 mxbai-embed-large 로 라우팅).
    embed_model: str = "local-embed"

    # --- 업로드 파일(아바타 등) — app/storage.py. DB 엔 이 루트 기준 키만 저장한다. ---
    # 서버: /data/neosdl/uploads (neosdl:neosdl 2770). 로컬 dev 는 repo 아래 uploads/(gitignore).
    upload_root: str = "./uploads"
    avatar_max_bytes: int = 2 * 1024 * 1024

    neosdl_workdir: str = "/var/tmp/neosdl"
    worker_name: str = "worker-1"
    # 이 워커 프로세스가 집을 job kind(쉼표 구분). 비면 전체. engagement 전용 풀을 분리해
    # (worker_kinds="engagement.run,task.run,subtask.run,dynamic.verify,sandbox.boot") 긴 검증
    # 작업이 빠른 스캔 파이프라인을 굶기지 않게 한다(job_dequeue 의 kind 필터 재사용).
    worker_kinds: str = ""

    # --- 샌드박스 자율 보안 검증(화이트박스 DAST) — worker/sandbox/*, app/dynamic/* ---
    # 기본 off: 켜기 전까지 기존 배포 동작은 완전히 동일하다.
    sandbox_enabled: bool = False
    sandbox_docker_host: str = "tcp://127.0.0.1:2376"   # 서버 dind(루프백, TLS). 개발 PC는 로컬 Docker.
    sandbox_docker_tls_cert_path: str | None = None     # ca/cert/key 디렉터리(없으면 TLS 없이 — 로컬 전용)
    sandbox_allowed_images: str = (
        "node:20-bookworm-slim,python:3.12-slim,eclipse-temurin:21-jdk,"
        "postgres:16-alpine,mysql:8,redis:7")
    sandbox_image_selection_mode: Literal["fixed", "automatic"] = "fixed"
    sandbox_ports_base: int = 28000
    sandbox_ports_end: int = 29999
    sandbox_mem_limit: str = "2g"
    sandbox_cpus: float = 2.0
    sandbox_pids_limit: int = 512
    sandbox_build_timeout_s: int = 600
    sandbox_boot_timeout_s: int = 180
    # 툴박스 명령(nmap/nikto/sqlmap 등) 1건의 하드 타임아웃. 부팅 타임아웃과 분리 — 버전탐지
    # 스캔은 부팅보다 오래 걸릴 수 있다. timeout --signal=KILL 로 초과 시 강제 종료(exit 137).
    sandbox_exec_timeout_s: int = 300
    sandbox_session_ttl_s: int = 1800
    sandbox_egress_mode: Literal["none", "build_only", "proxy"] = "build_only"
    sandbox_artifact_root: str = "./sandbox"            # upload_root 패턴. 서버: /data/neosdl/sandbox
    # 샌드박스에서 "코드를 실행해도 되는" 저장소 allowlist(쉼표 구분 "namespace/name").
    # privileged dind = 호스트 root 이므로 fail-closed: 비어 있으면 아무 레포도 실행 못 한다.
    # 외부/고객 레포는 Phase 2.5 격리 경계(KVM VM) 전까지 여기 넣지 않는다 — 자사·검증 레포만.
    sandbox_repo_allowlist: str = ""
    # 레포 부팅(=코드 실행) 인가 방식.
    #  - "workspace"(기본): 워크스페이스 멤버십으로 인가. engagement 는 manager 권한자만 생성하므로
    #    그 프로젝트/워크스페이스에 속한 사내 레포는 별도 allowlist 등록 없이 부팅할 수 있다.
    #    (사내 코드 전제. 외부/고객 레포는 애초에 그 워크스페이스에 없어야 하며 KVM 경계 전까지 금지.)
    #  - "allowlist": 보수적. sandbox_repo_allowlist 에 명시된 레포만 부팅(기존 동작).
    sandbox_repo_authz: Literal["workspace", "allowlist"] = "workspace"
    # --- PentAGI 흡수: 보안 도구 실행(terminal) ---
    # 보안 도구(nmap/sqlmap/...) 20+ 가 내장된 툴박스 이미지. 자체 curated slim 으로 교체 가능.
    sandbox_toolbox_image: str = "vxcontrol/kali-linux"
    # 공격 도구를 쏴도 되는 "인가된 표적"(host 또는 host:port) allowlist. fail-closed: 비면 아무 표적도 불가.
    # 법적 권한이 있는 사내 표적만. 외부/고객 표적은 Phase 2.5 격리 경계 전까지 넣지 않는다.
    sandbox_target_allowlist: str = ""
    # 툴박스 컨테이너에 부여할 capability — 보안 도구(nmap 등)는 raw 소켓 등 네트워크 권한이 필요하다.
    # cap_drop=ALL 위에 이것만 cap_add 한다. no-new-privileges 는 툴박스에서만 해제(fscap 바이너리 실행용).
    # 대상 앱 컨테이너(화이트박스 boot)는 이 완화와 무관하게 완전 하드닝 유지.
    sandbox_toolbox_caps: str = "NET_RAW,NET_ADMIN,NET_BIND_SERVICE"
    sandbox_toolbox_user: str = "1000:1000"   # 비root. 이미지가 root 를 요구하면 빈 문자열로(이미지 기본).
    # 펜테스터 루프 1회 최대 스텝(= 도구 실행 횟수). 열거한 라우트를 끝까지 점검하려면 크게 잡는다.
    # 스텝마다 queue.heartbeat 로 job 리스를 갱신하므로 10분 넘는 긴 점검도 안전(이중 실행 없음).
    pentest_max_steps: int = 30
    # sqlmap 플래그. --threads 로 병렬, --technique=BEUS 로 time-based(sleep) 블라인드를 빼 엔드포인트당
    # 수 분씩 걸리던 병목을 없앤다. 더 깊게(느리게) 보려면 --technique 에 T 추가 또는 --level/--risk 상향.
    pentest_sqlmap_flags: str = "--batch --level=2 --risk=2 --threads=4 --technique=BEUS"
    # nuclei 템플릿 스캔 플래그. jsonl 로 파싱, info 심각도는 추출기에서 버린다(보안헤더 노이즈 억제).
    pentest_nuclei_flags: str = "-silent -jsonl -severity low,medium,high,critical -timeout 8 -retries 1"
    # ffuf 콘텐츠 디스커버리 워드리스트. Kali(vxcontrol/kali-linux)에 기본 존재하는 dirb common.
    pentest_ffuf_wordlist: str = "/usr/share/wordlists/dirb/common.txt"

    # --- LLM 실행 추적(Langfuse, 자체호스팅, 개발 전용) ---
    # 둘 다 비어 있으면 app/observability.py 전체가 no-op(fail-open) — 앱 동작 무영향.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "http://localhost:3000"

    @property
    def cookie_secure(self) -> bool:
        """쿠키 Secure 속성 — 브라우저는 Secure 쿠키를 https(와 localhost)에서만 저장한다.

        사내 서버는 아직 http://intranet.finble.internal 이라 Secure 를 붙이면 세션/OAuth
        state 쿠키가 통째로 버려진다. 공개 주소가 https 로 바뀌면 자동으로 다시 켜진다.
        """
        return self.frontend_url.startswith("https://")

    @property
    def graph_backend(self) -> Literal["neo4j", "postgres_cte"]:
        return "neo4j" if self.neo4j_uri else "postgres_cte"

    @property
    def worker_kind_list(self) -> list[str] | None:
        """이 워커가 집을 kind 목록(None = 전체). worker/main.py 가 job_dequeue 에 넘긴다."""
        kinds = [k.strip() for k in self.worker_kinds.split(",") if k.strip()]
        return kinds or None

    @property
    def sandbox_allowed_image_list(self) -> list[str]:
        return [i.strip() for i in self.sandbox_allowed_images.split(",") if i.strip()]

    @property
    def sandbox_repo_allowlist_set(self) -> set[str]:
        """샌드박스 실행 허용 저장소("namespace/name") 집합. 비면 아무것도 허용 안 함(fail-closed)."""
        return {r.strip() for r in self.sandbox_repo_allowlist.split(",") if r.strip()}

    @property
    def sandbox_target_allowlist_set(self) -> set[str]:
        """공격 도구 실행이 인가된 표적(host 또는 host:port) 집합. 비면 아무 표적도 불가(fail-closed)."""
        return {t.strip().lower() for t in self.sandbox_target_allowlist.split(",") if t.strip()}

    @property
    def sandbox_toolbox_cap_list(self) -> list[str]:
        """툴박스에 cap_add 할 capability 목록(쉼표 구분)."""
        return [c.strip().upper() for c in self.sandbox_toolbox_caps.split(",") if c.strip()]


@lru_cache
def settings() -> Settings:
    return Settings()
