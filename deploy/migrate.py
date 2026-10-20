"""운영 DB 스키마 마이그레이션 — sql/NNN_*.sql 을 번호순으로 한 번씩만 적용한다.

로컬 dev 는 docker-compose.dev.yml 이 sql/ 를 initdb 로 마운트해 "최초 기동 시 전부"
적용하지만, 운영 DB 는 계속 살아 있으므로 그 뒤에 추가된 파일을 반영할 수단이
필요하다. 적용 이력은 schema_migrations 테이블에 남긴다.

    python deploy/migrate.py                 # 미적용 파일 적용
    python deploy/migrate.py --status        # 적용/미적용 목록만 출력
    python deploy/migrate.py --baseline 026  # 이력 테이블이 없는 기존 DB: 001~026 을
                                             # "이미 적용됨"으로 기록만 하고 SQL 은 안 돌림

이력 테이블이 없는데 DB 에 이미 테이블이 있으면(= initdb 로 만든 기존 DB) 어디까지
적용됐는지 추측하지 않고 멈춘다 — --baseline 으로 명시해야 한다.

접속 계정 — 앱 실행 계정과 마이그레이션(DDL) 계정을 분리한다:
    1) 환경변수 MIGRATION_DATABASE_URL
    2) --env-file 로 준 파일의 MIGRATION_DATABASE_URL   (운영: root 만 읽는 별도 파일)
    3) 환경변수 / app/.env 의 DATABASE_URL               (로컬 dev — 계정이 하나뿐)
운영의 app/.env 는 CREATE 권한이 없는 앱 계정(neosdl_app)이라, 슈퍼유저 접속 정보는
앱 프로세스가 읽는 .env 에 두지 않는다.

MIGRATION_APP_ROLE(환경변수 또는 --env-file)을 주면, 적용 후 그 앱 계정이 SELECT/INSERT
권한을 못 받은 public 테이블을 경고한다 — 마이그레이션 계정이 만든 새 테이블은 기본적으로
앱 계정이 못 쓰므로(DEFAULT PRIVILEGES 미설정 시) 배포 직후 500 을 미리 잡기 위함.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
_NAME = re.compile(r"^(\d{3})_.+\.sql$")


def _files() -> list[tuple[str, Path]]:
    out = []
    for p in sorted(SQL_DIR.iterdir()):
        m = _NAME.match(p.name)
        if m:
            out.append((m.group(1), p))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--baseline", metavar="NNN")
    ap.add_argument("--env-file", metavar="PATH",
                    help="MIGRATION_DATABASE_URL / MIGRATION_APP_ROLE 을 읽을 파일(운영: root 전용)")
    args = ap.parse_args()

    extra = dotenv_values(args.env_file) if args.env_file else {}
    if args.env_file and not extra:
        print(f"{args.env_file} 를 읽지 못했거나 비어 있다", file=sys.stderr)
        return 2
    dsn = (os.environ.get("MIGRATION_DATABASE_URL") or extra.get("MIGRATION_DATABASE_URL")
           or os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL"))
    app_role = os.environ.get("MIGRATION_APP_ROLE") or extra.get("MIGRATION_APP_ROLE")
    if not dsn:
        print("MIGRATION_DATABASE_URL / DATABASE_URL 이 없다", file=sys.stderr)
        return 2

    files = _files()
    with psycopg.connect(dsn, autocommit=True) as conn:
        user, db, superuser = conn.execute(
            "SELECT current_user, current_database(), current_setting('is_superuser')"
        ).fetchone()
        # 잘못된 계정(앱 계정 등)으로 도는 걸 바로 알아보게 접속 정보를 먼저 찍는다.
        print(f"connected: user={user} db={db} superuser={superuser}", flush=True)
        has_table = conn.execute(
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
        ).fetchone()[0]
        if not has_table:
            n_tables = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
            ).fetchone()[0]
            if n_tables and not args.baseline:
                print(
                    f"schema_migrations 없이 기존 테이블 {n_tables}개가 있다 — "
                    "어디까지 적용된 DB 인지 확인 후 --baseline NNN 으로 시작할 것",
                    file=sys.stderr,
                )
                return 3
            conn.execute(
                "CREATE TABLE schema_migrations ("
                " version text PRIMARY KEY, filename text NOT NULL,"
                " applied_at timestamptz NOT NULL DEFAULT now())"
            )

        if args.baseline:
            for version, path in files:
                if version <= args.baseline:
                    conn.execute(
                        "INSERT INTO schema_migrations(version, filename) VALUES (%s,%s)"
                        " ON CONFLICT DO NOTHING",
                        (version, path.name),
                    )
            print(f"baseline: {args.baseline} 까지 적용됨으로 기록")

        applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
        pending = [(v, p) for v, p in files if v not in applied]

        if args.status:
            for v, p in files:
                print(("  applied " if v in applied else "  PENDING ") + p.name)
            return 0

        for version, path in pending:
            print(f"apply {path.name}", flush=True)
            # sql/ 파일들은 각자 BEGIN; ... COMMIT; 으로 원자성을 스스로 보장한다 —
            # 여기서 다시 트랜잭션으로 감싸면 파일 안의 COMMIT 이 바깥을 먼저 끝내
            # 버리므로 autocommit 으로 그대로 흘려보낸다. 실패하면 예외로 즉시 멈추고
            # 이력은 기록되지 않는다.
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations(version, filename) VALUES (%s,%s)",
                (version, path.name),
            )
        print(f"done — {len(pending)}개 적용" if pending else "최신 상태")

        if app_role:
            missing = [r[0] for r in conn.execute(
                """
                SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND c.relname <> 'schema_migrations'
                   AND NOT (has_table_privilege(%s, c.oid, 'SELECT')
                        AND has_table_privilege(%s, c.oid, 'INSERT'))
                 ORDER BY c.relname
                """,
                (app_role, app_role),
            )]
            if missing:
                print(f"WARNING: 앱 계정 {app_role} 이 SELECT/INSERT 를 못 하는 테이블: {', '.join(missing)}\n"
                      f"  필요한 권한만 부여할 것 — 예: GRANT SELECT, INSERT, UPDATE ON <table> TO {app_role};",
                      file=sys.stderr)
                return 4
            print(f"app role {app_role}: 모든 테이블 SELECT/INSERT 가능")
    return 0


if __name__ == "__main__":
    sys.exit(main())
