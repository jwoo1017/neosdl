"""보안 도구(terminal) 실행의 안전 핵심 — 순수 검증 로직(DB/네트워크/Docker 무의존).

PentAGI 흡수(화이트박스 자율 보안 검증)에서 LLM 이 `terminal` 툴로 nmap/sqlmap 등을 샌드박스
컨테이너에 실행한다. 공격 도구라 **명령을 실제로 실행하기 전에** 여기서 전부 거른다:

1. argv[0] 바이너리가 allowlist 에 있는가(그 외는 거부).
2. 셸 메타문자(`; | & $ ( ) ` > < 개행 …)가 토큰에 섞였는가 — 우리는 셸이 아니라 argv 로 exec
   하지만, 섞여 있으면 LLM 이 셸 주입을 시도한 것이므로 거부.
3. 파괴/익스플로잇 플래그(sqlmap --os-shell, --file-write 등)가 있는가 — Phase A 는 재현·탐색까지만.
4. 명령에 등장하는 모든 host/URL 이 **인가된 표적 하나**로 고정되는가(스코프 잠금) — 다른 호스트를
   겨누면 거부. 네트워크단 egress 스코프(표적 한정)와 함께 2중 방어.

이 모듈은 결정적·순수 함수라 Docker 없이 단위테스트한다. 실제 실행·네트워크 격리·감사는
app/agent_tools.py(집행)·worker/sandbox(실행)·app/sandbox.py(표적 인가 게이트)가 맡는다.
"""

from __future__ import annotations

import ipaddress
import re
import shlex
from urllib.parse import urlsplit

from app.config import settings


class CommandRejected(ValueError):
    """명령이 안전 정책을 통과하지 못함. reason 은 거부 사유 코드."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# 허용 바이너리 → destructive(=permission_mode=auto 라도 항상 사람 승인)
# destructive=False 는 "탐색/정찰"(그래도 permission_mode=ask 면 승인 필요), True 는 침투성이 강함.
ALLOWED_TOOLS: dict[str, bool] = {
    # 정찰/스캔(비파괴)
    "nmap": False, "curl": False, "wget": False, "httpx": False,
    "whatweb": False, "wafw00f": False, "nslookup": False, "dig": False,
    "host": False, "openssl": False, "nikto": False, "dirb": False,
    "gobuster": False, "ffuf": False, "testssl.sh": False, "testssl": False,
    # 취약점 스캐너/능동(침투성 ↑ — 항상 승인)
    "sqlmap": True, "nuclei": True, "wpscan": True, "hydra": True,
    "msfconsole": True, "msfvenom": True,
}

# 어떤 토큰에도 등장하면 안 되는 셸 메타문자/시퀀스(셸 주입·리디렉션·명령연쇄).
_SHELL_META = re.compile(r"[;&|<>`$\n\r]|\$\(|\|\|")

# 바이너리별 금지 플래그(익스플로잇/파일쓰기/셸 획득 — Phase A 범위 밖).
_FORBIDDEN_FLAGS = {
    "sqlmap": ("--os-shell", "--os-pwn", "--os-cmd", "--os-bof", "--sql-shell",
               "--file-write", "--file-dest", "--file-read", "--reg-write", "--priv-esc"),
    "nmap": ("--script-args-file",),  # 임의 파일 로드 차단(스크립트 자체는 허용)
}
# 어떤 도구든 금지(공통) — 리버스셸·임의 파일 접근 흔적.
_FORBIDDEN_SUBSTR = ("/etc/passwd", "/etc/shadow", "bash -i", "/bin/sh", "nc -e", "ncat -e")

_IP_HOST = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?!-)[a-zA-Z0-9-]{1,63}(?:\.[a-zA-Z0-9-]{1,63})+$")


def parse_target(raw: str) -> tuple[str, int | None]:
    """표적 문자열(URL 또는 host[:port])에서 (host, port) 추출. 못 하면 CommandRejected."""
    raw = (raw or "").strip()
    if not raw:
        raise CommandRejected("empty_target")
    if "://" in raw:
        u = urlsplit(raw)
        host = u.hostname
        port = u.port
    else:
        # host 또는 host:port (IPv6 은 Phase A 미지원 — 사내 표적은 v4/도메인)
        if raw.count(":") == 1:
            host, _, p = raw.partition(":")
            port = int(p) if p.isdigit() else None
        else:
            host, port = raw, None
    if not host:
        raise CommandRejected("unparsable_target", raw)
    host = host.lower()
    if not (_IP_HOST.match(host) or _DOMAIN.match(host) or host in ("localhost",)):
        raise CommandRejected("invalid_host", host)
    return host, port


def host_in_scope(host: str) -> bool:
    """host 가 설정 allowlist(sandbox_target_allowlist)에 있는가. fail-closed."""
    allow = settings().sandbox_target_allowlist_set
    if not allow:
        return False
    host = host.lower()
    # allowlist 항목은 host 또는 host:port — host 부분만 비교(port 는 명령 스코프에서 별도 처리 안 함).
    allowed_hosts = {a.split(":")[0] for a in allow}
    return host in allowed_hosts


def target_allowed(raw: str) -> bool:
    """표적(raw URL/host)이 인가되었는가(순수). 파싱 실패·미등록이면 False."""
    try:
        host, _ = parse_target(raw)
    except CommandRejected:
        return False
    return host_in_scope(host)


def _extract_hosts(tokens: list[str]) -> set[str]:
    """argv 토큰들에서 host 처럼 보이는 것들을 뽑는다(URL 의 host, 혹은 IP/도메인 토큰)."""
    hosts: set[str] = set()
    for tok in tokens:
        if tok.startswith("-"):
            continue
        cand = tok
        if "://" in tok:
            cand = urlsplit(tok).hostname or ""
        elif "/" in tok and "." in tok.split("/")[0]:
            cand = tok.split("/")[0]  # host/path 형태
        cand = (cand or "").split(":")[0].lower()
        if cand and (_IP_HOST.match(cand) or _DOMAIN.match(cand) or cand == "localhost"):
            hosts.add(cand)
    return hosts


def validate_command(command: str, target: str, *, trusted: bool = False) -> list[str]:
    """terminal 명령 문자열을 검증해 argv 로 반환. 통과 못 하면 CommandRejected.

    target 은 호출측이 인가받은 표적(host/URL). 명령 안의 모든 host 는 이 표적 host 로 고정돼야 한다.

    trusted=True 는 표적이 **우리가 샌드박스에 직접 띄운 일회용 앱**(self-boot)일 때 — 그 경우
    표적 인가는 레포 부팅 게이트(assert_repo_allowed)가 이미 담당했으므로 host allowlist 검사를
    생략한다. 스코프 잠금(명령 안 다른 host 거부)·셸/익스플로잇 플래그 검사는 그대로 적용한다.
    """
    command = (command or "").strip()
    if not command:
        raise CommandRejected("empty_command")
    if _SHELL_META.search(command):
        raise CommandRejected("shell_metachar", command)
    low = command.lower()
    for bad in _FORBIDDEN_SUBSTR:
        if bad in low:
            raise CommandRejected("forbidden_substr", bad)
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise CommandRejected("unparsable_command", str(exc))
    if not argv:
        raise CommandRejected("empty_command")
    binary = argv[0].split("/")[-1]   # /usr/bin/nmap → nmap
    if binary not in ALLOWED_TOOLS:
        raise CommandRejected("tool_not_allowed", binary)
    for flag in _FORBIDDEN_FLAGS.get(binary, ()):
        if flag in argv:
            raise CommandRejected("forbidden_flag", flag)

    target_host, _ = parse_target(target)
    if not trusted and not host_in_scope(target_host):
        raise CommandRejected("target_not_authorized", target_host)
    foreign = {h for h in _extract_hosts(argv[1:]) if h != target_host}
    if foreign:
        raise CommandRejected("out_of_scope_host", ",".join(sorted(foreign)))
    return argv


def is_destructive(command: str) -> bool:
    """명령의 바이너리가 침투성이 강한가(permission_mode=auto 라도 항상 승인 필요)."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return True
    if not argv:
        return True
    return ALLOWED_TOOLS.get(argv[0].split("/")[-1], True)
