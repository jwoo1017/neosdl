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
    # 임베딩 전용 게이트웨이 별칭 (vector(1024) 유지 위해 mxbai-embed-large 로 라우팅).
    embed_model: str = "local-embed"

    # --- 업로드 파일(아바타 등) — app/storage.py. DB 엔 이 루트 기준 키만 저장한다. ---
    # 서버: /data/neosdl/uploads (neosdl:neosdl 2770). 로컬 dev 는 repo 아래 uploads/(gitignore).
    upload_root: str = "./uploads"
    avatar_max_bytes: int = 2 * 1024 * 1024

    neosdl_workdir: str = "/var/tmp/neosdl"
    worker_name: str = "worker-1"

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


@lru_cache
def settings() -> Settings:
    return Settings()
