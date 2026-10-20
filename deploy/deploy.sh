#!/usr/bin/env bash
# NeoSDL 앱 배포 — finble-server (systemd + 호스트 nginx 구조)
#
# 권한이 다른 두 단계로 나뉜다(가이드 6·16절: 개발자는 sudo 없음).
#
#   1) 개발자(jwji, neosdl 그룹)            deploy/deploy.sh build [ref]
#        코드 갱신 → pip → 프론트 빌드 → /var/www/neosdl 반영
#   2) 관리자(finbleadmin)                   sudo deploy/deploy.sh release
#        배포 전 pg_dump → DB 마이그레이션 → backend/worker 재시작 → 헬스체크
#
# 롤백: build <이전 sha> 후 release (DB 스키마는 되돌리지 않음 — 필요하면 predeploy 덤프로 복구).
# LLM 스택(deploy/compose.llm.yml)은 이 스크립트와 별개로 관리한다.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WEB_ROOT=/var/www/neosdl
BACKUP_DIR=/backup/neosdl/postgres
HEALTH_URL=http://10.0.0.5/healthz
MIGRATE_ENV=/srv/projects/neosdl/secrets/migrate.env   # root:root 600

cd "$APP_DIR"
# release 는 root 가 jwji 소유 저장소를 읽으므로 git 의 dubious-ownership 검사를 이 경로만 푼다.
git() { command git -c safe.directory="$APP_DIR" "$@"; }

build() {
  local ref="${1:-origin/main}"
  git fetch --tags origin
  git checkout --detach "$ref"
  echo "== $(git rev-parse --short HEAD) $(git log -1 --format=%s)"

  .venv/bin/pip install -q -r requirements.txt

  (cd frontend && npm ci --no-audit --no-fund && npm run build)
  rsync -a --delete frontend/dist/ "$WEB_ROOT/"

  echo "build 완료 — 관리자에게 'sudo deploy/deploy.sh release' 요청"
}

release() {
  [[ $EUID -eq 0 ]] || { echo "release 는 sudo 로 실행" >&2; exit 1; }

  local ts out
  ts="$(date +%Y%m%d-%H%M%S)"
  out="$BACKUP_DIR/predeploy_$(git rev-parse --short HEAD)_${ts}.dump"
  docker exec neosdl-postgres pg_dump -U neosdl -d neosdl -Fc > "$out"
  chown neosdl:neosdl "$out" && chmod 640 "$out"
  echo "backup $out"

  # 마이그레이션은 DDL 권한이 있는 별도 계정으로 — 접속 정보는 앱 .env 가 아니라 root 전용
  # 파일(MIGRATION_DATABASE_URL, MIGRATION_APP_ROLE)에 둔다. 앱 계정 권한이 빠진 새 테이블이
  # 있으면 migrate.py 가 0 이 아닌 코드로 끝나서 재시작 전에 멈춘다.
  [[ -f "$MIGRATE_ENV" ]] || { echo "$MIGRATE_ENV 없음 — deploy/migrate.env.example 참고" >&2; exit 1; }
  .venv/bin/python deploy/migrate.py --env-file "$MIGRATE_ENV"

  systemctl restart neosdl-backend neosdl-worker

  echo -n "health "
  for _ in $(seq 1 20); do
    if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      echo "ok — $(git rev-parse --short HEAD) 배포 완료"
      return 0
    fi
    echo -n "."; sleep 3
  done
  echo
  echo "헬스체크 실패 — tail -n 100 /data/neosdl/logs/backend.log 확인" >&2
  exit 1
}

case "${1:-}" in
  build)   shift; build "$@" ;;
  release) release ;;
  *) echo "usage: deploy/deploy.sh build [ref] | sudo deploy/deploy.sh release" >&2; exit 2 ;;
esac
