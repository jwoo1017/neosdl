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
    args = ap.parse_args()

    # 서버에서는 systemd 와 같은 app/.env 를 그대로 읽는다(환경변수가 있으면 그게 우선).
    dsn = os.environ.get("DATABASE_URL") or dotenv_values(ROOT / ".env").get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL 이 없다", file=sys.stderr)
        return 2

    files = _files()
    with psycopg.connect(dsn, autocommit=True) as conn:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
