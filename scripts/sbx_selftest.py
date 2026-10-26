"""샌드박스 Phase 2 e2e self-test — 실제 컨테이너 부팅 + HTTP + IAST + oracle 전 경로 검증.

tests/fixtures/sbx_target(안전 /safe = ? 바인딩, 취약 /vuln = 문자열 조립)을 **실제로 빌드·기동**
하고, 각 엔드포인트를 때려 IAST(nonce 가 쿼리문자열 vs 바인딩 어디로 갔는지)로 oracle 판정한다.
기대: /safe → false_positive, /vuln → confirmed. 이게 Phase 2 가 "진짜 도는가"의 증명이다.

HTTP 는 **컨테이너 내부에서 exec**로 보낸다(호스트 포트 publish·네트워킹 불필요) → 로컬 Docker 든
서버 dind 든 동일하게 동작. 로그는 docker SDK 로 읽는다.

로컬:   python scripts/sbx_selftest.py
서버 dind: python scripts/sbx_selftest.py --docker-host tcp://127.0.0.1:2376 --tls-cert-path /srv/.../sbx-client-certs
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.dynamic import oracle  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "sbx_target"
_IAST_RE = re.compile(r"NEOSDL_IAST reached=(\d) where=(\S+)")


def _client(args):
    import docker
    if args.docker_host:
        tls = None
        if args.tls_cert_path:
            from docker.tls import TLSConfig
            p = args.tls_cert_path.rstrip("/")
            tls = TLSConfig(client_cert=(f"{p}/cert.pem", f"{p}/key.pem"), ca_cert=f"{p}/ca.pem", verify=True)
        return docker.DockerClient(base_url=args.docker_host, tls=tls, timeout=60)
    return docker.from_env(timeout=60)


def _exec_get(container, path: str) -> int:
    """컨테이너 안에서 http://localhost:8000{path} GET(파이썬 urllib). 종료코드 반환(0=성공)."""
    code = (
        "import urllib.request,sys\n"
        f"try:\n urllib.request.urlopen('http://localhost:8000{path}',timeout=5).read()\n"
        " sys.exit(0)\n"
        "except Exception as e:\n sys.stderr.write(str(e));sys.exit(1)\n"
    )
    res = container.exec_run(["python", "-c", code])
    return res.exit_code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docker-host", default=None)
    ap.add_argument("--tls-cert-path", default=None)
    ap.add_argument("--keep", action="store_true", help="실패 디버깅용: 컨테이너 안 지움")
    args = ap.parse_args()

    client = _client(args)
    assert client.ping(), "docker ping 실패"
    nonce = f"neosdl-selftest-{secrets.token_hex(3)}"
    tag = "neosdl-sbx/selftest:latest"

    print(f"[1] 이미지 빌드: {FIXTURE}")
    client.images.build(path=str(FIXTURE), tag=tag, rm=True, forcerm=True, pull=False)

    print(f"[2] 컨테이너 기동 (nonce={nonce}), 보안 기본값(비root·cap_drop·no-new-priv)")
    container = client.containers.run(
        tag, detach=True, environment={"NEOSDL_IAST_NONCE": nonce},
        cap_drop=["ALL"], security_opt=["no-new-privileges:true"], user="1000:1000",
        mem_limit="512m", pids_limit=256, labels={"neosdl.selftest": "1"})
    try:
        # 준비 대기(/health 가 0 될 때까지)
        for _ in range(60):
            if _exec_get(container, "/health") == 0:
                break
            time.sleep(1)
        else:
            print("기동 실패 — 로그:\n", container.logs().decode("utf-8", "replace")[-2000:])
            return 2
        print("[3] 준비 완료. 안전/취약 엔드포인트 프로브")

        results = {}
        for endpoint, label in (("/safe", "safe(바인딩)"), ("/vuln", "vuln(조립)")):
            before = len(container.logs(stdout=False, stderr=True).decode("utf-8", "replace").splitlines())
            _exec_get(container, f"{endpoint}?id={nonce}")
            time.sleep(0.5)
            lines = container.logs(stdout=False, stderr=True).decode("utf-8", "replace").splitlines()[before:]
            reached = None
            for ln in lines:
                m = _IAST_RE.search(ln)
                if m:
                    reached = bool(int(m.group(1)))
            v = oracle.decide("sql_injection", probe_body="", iast_reached=reached,
                              entry_confidence=0.9)
            results[endpoint] = (reached, v.verdict)
            print(f"    {label:14} iast_reached={reached}  →  verdict={v.verdict} (conf {v.confidence:.2f})")

        ok = (results["/safe"][1] == "false_positive" and results["/vuln"][1] == "confirmed")
        print("\n" + ("✅ PASS — Phase 2 e2e 동작: 안전=오탐제거, 취약=confirmed"
                      if ok else "❌ FAIL — 아래 결과 확인"), results)
        return 0 if ok else 1
    finally:
        if not args.keep:
            try:
                container.remove(force=True)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
