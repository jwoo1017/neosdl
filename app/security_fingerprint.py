"""security_finding 지문 — 스캔 간 동일 위험(Risk Identity)을 잇는 결정적 식별자.

Security History 의 Risk Identity 는 이 지문으로 정의된다: 같은 지문 = 여러
커밋/브랜치에서 관측된 **하나의** 위험이지, 스캔마다 새로 생기는 위험이 아니다
(`NeoSDL_Security_History_Plan.md` §2.3).

불변인 것: 함수 본문 안의 라인 이동, 로컬 변수명 변경, 커밋 SHA, 스캔 ID.
바뀌면 새 지문(문서화된 Phase 1 한계): 파일 rename, 함수 rename — `symbol_key` 에
`path`/`qualified_name` 이 들어가기 때문. `symbol_key` 가 이미 가진 한계와 같고,
`app/incremental.py` 가 rename 을 "옛 키 제거 + 새 키 추가"로 다루는 것과 같다.

DB/LLM 의존 없음 — `tests/test_security_fingerprint.py` 가 dict 만으로 검증한다.
"""

from __future__ import annotations

import hashlib
import re

FINGERPRINT_VERSION = "v1"

# app/dataflow.py SOURCE_PATTERNS 와 같은 정규식 — 여기선 "어떤 종류의 소스냐"를
# 정규화된 클래스명으로 접는다(req.body.userId 와 req.body.email 은 같은 클래스).
_SOURCE_CLASSES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"req\.(body|query|params)"), "http.req"),
    (re.compile(r"request\.(GET|POST|args|form|json|query)"), "http.request"),
    (re.compile(r"\binput\("), "stdin"),
    (re.compile(r"os\.environ"), "env"),
    (re.compile(r"process\.env"), "env"),
    (re.compile(r"sys\.argv"), "argv"),
]


def _strip_repo(symbol_key: str) -> str:
    """{lang}:{repo_uuid}:{path}#{qname}({params}) -> {lang}:{path}#{qname}({params}).

    repo UUID 는 지문의 안정성에 방해만 된다(같은 코드가 다른 레포에 복사돼도
    같은 위험). 라인/커밋/내용해시는 symbol_key 에 애초에 없다(sql/001 `symbol_key_of`).
    """
    parts = symbol_key.split(":", 2)
    return f"{parts[0]}:{parts[2]}" if len(parts) == 3 else symbol_key


def _source_class(source_var: str) -> str:
    for rx, name in _SOURCE_CLASSES:
        if rx.search(source_var or ""):
            return name
    # 클래스에 안 잡히면 숫자만 제거한 소문자 — `arg1`/`arg2` 같은 자동 이름을 합친다.
    return re.sub(r"\d+", "", (source_var or "").strip().lower())


def _normalized_path(path) -> str:
    """taint hop 배열([{symbol_key,var,line}, ...]) -> 정규화된 symbol_key 시퀀스.

    hop 의 var/line 은 버린다(편집에 취약). 연속 중복 symbol_key 는 접는다.
    insert 시점 dict 와 DB jsonb 둘 다 같은 모양이라 한 경로로 처리된다.
    """
    syms: list[str] = []
    for hop in path or []:
        if not isinstance(hop, dict):
            continue
        sk = hop.get("symbol_key")
        if not sk:
            continue
        n = _strip_repo(sk)
        if not syms or syms[-1] != n:
            syms.append(n)
    return ">".join(syms)


def compute_fingerprint(finding: dict) -> str:
    """`security_finding` 행(dict) 또는 `ensure_security_findings` 가 만드는 insert dict.

    필요한 키: `kind`, `category`, `sink_symbol_key`, `source_var`, `path`.
    """
    material = "|".join((
        FINGERPRINT_VERSION,
        finding.get("kind") or "",
        finding.get("category") or "",
        _strip_repo(finding.get("sink_symbol_key") or ""),
        _source_class(finding.get("source_var") or ""),
        _normalized_path(finding.get("path") or []),
    ))
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]
