"""시스템 관리자 부여/회수 — 웹에서는 아무도 스스로 관리자가 될 수 없게 서버에서만 한다.

    python deploy/admin.py list
    python deploy/admin.py grant  someone@zerokleek.com
    python deploy/admin.py revoke someone@zerokleek.com

접속 계정은 deploy/migrate.py 와 같다:
    1) 환경변수 MIGRATION_DATABASE_URL
    2) --env-file 로 준 파일의 MIGRATION_DATABASE_URL   (운영: root 만 읽는 별도 파일)
    3) 환경변수 / app/.env 의 DATABASE_URL               (로컬 dev)
변경은 audit_log 에 actor=ops:admin-cli 로 남긴다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent


def _dsn(env_file: str | None) -> str:
    extra = dotenv_values(env_file) if env_file else {}
    app_env = dotenv_values(ROOT / "app" / ".env") if (ROOT / "app" / ".env").exists() else {}
    dsn = (os.environ.get("MIGRATION_DATABASE_URL") or extra.get("MIGRATION_DATABASE_URL")
           or os.environ.get("DATABASE_URL") or app_env.get("DATABASE_URL"))
    if not dsn:
        sys.exit("DB 접속 정보 없음 — MIGRATION_DATABASE_URL 또는 DATABASE_URL")
    return dsn


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["list", "grant", "revoke"])
    ap.add_argument("email", nargs="?")
    ap.add_argument("--env-file", metavar="PATH")
    args = ap.parse_args()
    if args.action != "list" and not args.email:
        ap.error("grant/revoke 에는 이메일이 필요하다")

    with psycopg.connect(_dsn(args.env_file)) as conn, conn.cursor() as cur:
        if args.action == "list":
            cur.execute("SELECT email, nickname FROM member WHERE is_system_admin AND deleted_at IS NULL "
                        "ORDER BY email")
            rows = cur.fetchall()
            print("\n".join(f"{e}\t{n}" for e, n in rows) or "(관리자 없음)")
            return 0

        grant = args.action == "grant"
        cur.execute(
            "UPDATE member SET is_system_admin=%s, updated_at=now() "
            "WHERE lower(email)=lower(%s) AND deleted_at IS NULL RETURNING id, nickname",
            (grant, args.email),
        )
        row = cur.fetchone()
        if row is None:
            print(f"회원 없음: {args.email} (먼저 가입해야 한다)")
            return 1
        cur.execute(
            "INSERT INTO audit_log (actor, action, subject, payload) VALUES (%s, %s, %s::jsonb, %s::jsonb)",
            ("ops:admin-cli", "member.system_admin." + args.action,
             json.dumps({"member_id": str(row[0])}), json.dumps({"email": args.email})),
        )
        print(f"{args.action} 완료: {args.email} ({row[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
