"""보안 sink 카탈로그 — 정적 dataflow(app/dataflow.py)와 엔드포인트 sink 추적(app/sink_trace.py)이 공유한다.

예전엔 dataflow 의 SQL sink 가 `\\.execute` 하나뿐이라 Node 앱의 `db.query`·`pool.query`·
`sequelize.query`·`knex.raw` 를 전혀 못 봤고, 블랙박스 발견은 코드 대신 문자열 매칭으로 위치를 붙였다.
sink 를 한 곳에서 정의해 두 경로가 같은 기준으로 "어디가 위험한 호출인가"를 판단하게 한다.

각 항목: 호출 대상 텍스트(`pool.query`, `this.db.execute` …)를 보는 정규식, 위험한 인자 위치(그 자리에
요청 값이 **문자열 구성으로** 섞이면 취약), 그리고 그 자리가 안전한 형태(바인딩 등)는 sink_trace 가
인자 구성(construction)으로 따로 판정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# category 는 security_finding.category / app/security_severity.py 와 같은 이름을 쓴다.
SQL, NOSQL, CMD, CODE, SSTI, LFI, XSS = ("sql_injection", "nosql_injection", "command_injection",
                                         "code_injection", "ssti", "path_traversal", "xss")

# DB 핸들로 흔히 쓰는 수신자 이름 — `.query(` 는 너무 흔한 이름이라 수신자로 한정한다.
_DB_RECV = r"(?:db|pool|conn|connection|client|sequelize|knex|trx|tx|mysql|pg|sql|database|dbconn|dbpool|cursor|cur|session|engine|manager|queryRunner|entityManager|dataSource)"


@dataclass(frozen=True)
class Sink:
    category: str
    pattern: re.Pattern
    danger_args: tuple[int, ...]      # 이 위치 인자가 요청 값으로 '구성'되면 위험
    label: str                        # 사람이 읽는 이름(설명에 그대로 씀)


def _s(category: str, rx: str, danger: tuple[int, ...], label: str) -> Sink:
    return Sink(category, re.compile(rx), danger, label)   # 대소문자 구분 — `items.find` 와 `User.find` 를 가른다


CATALOG: tuple[Sink, ...] = (
    # ── SQL: 첫 인자(쿼리 문자열)만 위험. 두 번째부터는 드라이버가 값으로 바인딩한다.
    _s(SQL, r"(?:^|\.)" + _DB_RECV + r"\.(?:query|execute|raw|prepare|unsafe)$", (0,), "SQL 쿼리 실행"),
    _s(SQL, r"(?:^|\.)(?:db|database|sqlite|sqlite3)\.(?:all|run|get|exec|each)$", (0,), "SQL 쿼리 실행(sqlite)"),
    _s(SQL, r"(?:^|\.)\$(?:queryRawUnsafe|executeRawUnsafe)$", (0,), "Prisma raw 쿼리"),
    _s(SQL, r"\.(?:raw|whereRaw|orderByRaw|havingRaw|joinRaw|selectRaw)$", (0,), "쿼리 빌더 raw 구문"),
    _s(SQL, r"(?:^|\.)execute$", (0,), "SQL 실행"),
    _s(SQL, r"(?:^|\.)(?:executemany|executescript|mogrify)$", (0,), "SQL 실행"),
    _s(SQL, r"^(?:sa\.|sqlalchemy\.)?text$", (0,), "SQLAlchemy text()"),
    # ── NoSQL(Mongo): 필터 객체 자체가 요청 값이면 연산자 주입($ne 등)
    _s(NOSQL, r"^[A-Z]\w*\.(?:find|findOne|findOneAndUpdate|findOneAndDelete|updateOne|updateMany|deleteOne|deleteMany|countDocuments|where)$",
       (0,), "MongoDB 쿼리 필터"),
    # ── 명령 실행
    _s(CMD, r"(?:^|\.)(?:exec|execSync)$", (0,), "셸 명령 실행"),
    _s(CMD, r"(?:^|\.)(?:os\.system|os\.popen|subprocess\.\w+|commands\.getoutput)$", (0,), "셸 명령 실행"),
    _s(CMD, r"(?:^|\.)(?:spawn|spawnSync|execFile|execFileSync)$", (0, 1), "프로세스 실행"),
    # ── 코드 평가
    _s(CODE, r"^(?:eval|Function|vm\.runInNewContext|vm\.runInThisContext|vm\.runInContext|setTimeout|setInterval)$",
       (0,), "코드 평가(eval)"),
    # ── 서버 템플릿 주입: 템플릿 '소스'에 요청 값이 섞이면 위험(렌더 변수로 넘기는 건 안전)
    _s(SSTI, r"(?:^|\.)(?:ejs|pug|jade|handlebars|Handlebars|nunjucks|mustache|Mustache|_|lodash|dot|doT|swig|hbs|twig|jinja2|Template|environment|env)\.(?:render|compile|renderString|template|compileFile)$",
       (0,), "템플릿 엔진 렌더/컴파일"),
    _s(SSTI, r"^(?:render_template_string|Template)$", (0,), "템플릿 문자열 렌더"),
    # ── 경로순회/파일 읽기·쓰기
    _s(LFI, r"(?:^|\.)(?:fs|fsp|fse|promises)\.(?:readFile|readFileSync|createReadStream|writeFile|writeFileSync|appendFile|unlink|unlinkSync|readdir|readdirSync|stat|access|open|rm|rmSync)$",
       (0,), "파일 시스템 접근"),
    _s(LFI, r"(?:^|\.)res\.(?:sendFile|download|render)$", (0,), "파일 응답"),
    _s(LFI, r"^(?:open|send_file|send_from_directory|FileResponse)$", (0,), "파일 열기"),
    # ── 반영 XSS: 문자열 응답 본문에 요청 값이 섞임(res.json 은 제외 — JSON 직렬화)
    _s(XSS, r"(?:^|\.)res\.(?:send|write|end)$", (0,), "HTML/텍스트 응답"),
    _s(XSS, r"(?:^|\.)(?:innerHTML|outerHTML|insertAdjacentHTML|document\.write|HTMLResponse|Markup|mark_safe)$",
       (0,), "HTML 출력"),
)

# 이름만 같은 무해한 호출 — 요청 객체 접근, JSON 응답, 정규식 exec(`/x/.exec`, `rx.exec`) 등.
_EXCLUDE = re.compile(r"^(?:req|request|JSON|console|Promise|Object|Array|Math|router|app)\.|^res\.json$|"
                      r"(?:^|\.)(?:re|rx|regex|regexp|pattern|\w*(?:Regex|RegExp|Re|Pattern))\.exec$|^/.*/\w*\.exec$")


def match(target_text: str | None) -> Sink | None:
    """호출 대상 텍스트 → 첫 매칭 sink(없으면 None). `re.exec`·`req.query` 같은 동명이인은 제외."""
    t = (target_text or "").strip()
    if not t or _EXCLUDE.search(t):
        return None
    t = re.sub(r"^(?:this|self)\.", "", t)            # this.db.query → db.query
    t = re.sub(r"^await\s+", "", t)
    for s in CATALOG:
        if s.pattern.search(t):
            return s
    return None


def category(target_text: str | None) -> str | None:
    s = match(target_text)
    return s.category if s else None


def is_dangerous_position(category_name: str, index: int) -> bool:
    """dataflow 호환 — 인자 위치가 그 카테고리에서 위험한가(카탈로그 정의가 없으면 모두 위험)."""
    danger = {i for s in CATALOG if s.category == category_name for i in s.danger_args}
    return not danger or index in danger


CATEGORY_KO = {SQL: "SQL 인젝션", NOSQL: "NoSQL 연산자 주입", CMD: "명령 주입", CODE: "코드 주입(eval)",
               SSTI: "서버사이드 템플릿 주입(SSTI)", LFI: "경로순회/파일 접근", XSS: "반영 XSS"}
