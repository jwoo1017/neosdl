"""엔드포인트 → 핸들러 → 서비스 → **실제 sink 줄** 추적 (소스 레벨, 절차간).

블랙박스 발견이 "어느 파일 몇 번째 줄"을 문자열 매칭으로 붙이던 걸 대체한다. 라우트 선언에서 핸들러를
찾고, require/import·`module.exports = new Cls()`·`this.svc.m`·`exports.m =` 를 따라 호출을 내려가며
요청 값(req.body 등)의 오염을 전파한다. sink 카탈로그(app/sinks.py)에 걸리는 호출을 만나면 **위험 인자가
어떻게 구성됐는지**(문자열 연결·템플릿 보간·바인딩·상수)를 판정해 기록한다.

DB 콜그래프(symbol_edge)나 PDG 를 쓰지 않고 스캔 커밋의 소스를 tree-sitter 로 직접 읽는다 — 기존 스캔도
재스캔 없이 쓰이고, 추출기가 놓치는 `exports.x =`·클래스 인스턴스 export·3단 체인에도 막히지 않는다.

순수: reader(path)->str 와 module_paths 만 주입받는다(DB·Docker·LLM 무의존, 오프라인 테스트).
지원: JavaScript/TypeScript(Express 류), Python(FastAPI/Flask). 못 찾으면 빈 결과(회귀 없음).
"""

from __future__ import annotations

import logging
import posixpath
import re
from dataclasses import dataclass, field

import tree_sitter_javascript as ts_javascript
import tree_sitter_python as ts_python
import tree_sitter_typescript as ts_typescript
from tree_sitter import Language, Node, Parser

from app import sinks

log = logging.getLogger("app.sink_trace")

MAX_DEPTH = 6            # 핸들러에서 sink 까지 내려갈 최대 호출 깊이
MAX_FUNCS = 120          # 엔드포인트 하나당 분석할 함수 상한(폭주 방지)

_LANG = {
    ".js": Language(ts_javascript.language()), ".jsx": Language(ts_javascript.language()),
    ".mjs": Language(ts_javascript.language()), ".cjs": Language(ts_javascript.language()),
    ".ts": Language(ts_typescript.language_typescript()), ".tsx": Language(ts_typescript.language_tsx()),
    ".py": Language(ts_python.language()),
}
_JS_EXTS = (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx")

# 오염 격자: None(깨끗) < REQ(요청 객체 통째) < INPUT(공격자가 정하는 값)
REQ, INPUT = "req", "input"
_REQ_INPUT_FIELDS = {"body", "query", "params", "headers", "cookies", "files", "file",
                     # Python(Flask/Starlette)
                     "args", "form", "json", "values", "data", "query_params", "path_params"}
_REQ_INPUT_CALLS = {"get", "header", "param", "get_json", "json", "form", "body"}
# 값을 무해하게 만드는 호출 — 결과는 깨끗하다고 본다.
_SANITIZERS = re.compile(
    r"(?:^|\.)(?:parseInt|parseFloat|Number|Boolean|int|float|bool|escape|escapeId|escapeHtml|escape_string|"
    r"encodeURIComponent|encodeURI|basename|sanitize\w*|xss|DOMPurify\.sanitize|validator\.\w+|uuid\w*|"
    r"isValid\w*|bleach\.clean|html\.escape|quote|quote_plus|literal|format_ident|Identifier)$")
# 결과가 바인딩된 안전한 SQL 문자열인 호출(mysql.format(sql,[v]) 등)
_SQL_SAFE_BUILDERS = re.compile(r"(?:^|\.)(?:mysql|SqlString|sqlstring|pgFormat|format)\.(?:format|escape)$|^(?:sql|Prisma\.sql)$")

CONCAT_INPUT, CONCAT_CONST, CONCAT_OTHER, LITERAL, PARAMETERIZED, DIRECT_INPUT, UNKNOWN = (
    "concat_input", "concat_const", "concat_other", "literal", "parameterized", "direct_input", "unknown")
CONSTRUCTION_KO = {
    CONCAT_INPUT: "요청 값을 문자열 연결/보간으로 직접 넣음",
    DIRECT_INPUT: "요청 값을 그대로 전달",
    CONCAT_CONST: "상수만 보간(요청 값 없음)",
    CONCAT_OTHER: "출처를 확정 못 한 변수를 보간",
    LITERAL: "고정 문자열",
    PARAMETERIZED: "자리표시자 + 바인딩 값(안전)",
    UNKNOWN: "구성 판단 불가",
}


# ─────────────────────────────────────────────────────────────── 파일·파서
@dataclass
class _Src:
    path: str
    data: bytes
    root: Node
    py: bool

    def text(self, n: Node | None) -> str:
        return "" if n is None else self.data[n.start_byte:n.end_byte].decode("utf-8", "replace")

    def line_text(self, line: int) -> str:
        lines = self.data.decode("utf-8", "replace").splitlines()
        return lines[line - 1].strip() if 0 < line <= len(lines) else ""


def _norm(p: str) -> str:
    return posixpath.normpath(p.replace("\\", "/")).lstrip("./") if p else p


@dataclass
class _Target:
    kind: str                 # module | func | class | instance | object
    src: _Src
    node: Node | None = None  # func/class/object 노드
    name: str = ""


@dataclass
class _Ctx:
    reader: object
    paths: set
    cache: dict = field(default_factory=dict)
    bindings: dict = field(default_factory=dict)
    seen: set = field(default_factory=set)
    sinks: list = field(default_factory=list)
    wraps: dict = field(default_factory=dict)
    funcs: int = 0

    def load(self, path: str) -> _Src | None:
        path = _norm(path)
        if path in self.cache:
            return self.cache[path]
        src = None
        ext = posixpath.splitext(path)[1].lower()
        if ext in _LANG:
            try:
                text = self.reader(path)
            except Exception:
                text = None
            if text:
                data = text.encode("utf-8")
                src = _Src(path, data, Parser(_LANG[ext]).parse(data).root_node, ext == ".py")
        self.cache[path] = src
        return src


def _line(n: Node) -> int:
    return n.start_point[0] + 1


def _str_value(src: _Src, n: Node | None) -> str | None:
    if n is None or n.type not in ("string", "template_string"):
        return None
    if any(c.type in ("template_substitution", "interpolation") for c in n.children):
        return None
    return re.sub(r"^[rbuf]*['\"`]{1,3}|['\"`]{1,3}$", "", src.text(n), flags=re.I)


def _walk(n: Node):
    stack = [n]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(reversed(cur.children))


def _is_func(n: Node | None) -> bool:
    return n is not None and n.type in ("function_declaration", "function_expression", "arrow_function",
                                        "function", "generator_function_declaration", "method_definition",
                                        "function_definition")


# ─────────────────────────────────────────────────────────── 모듈 해석
def _resolve_module(ctx: _Ctx, from_path: str, spec: str, py: bool) -> str | None:
    if py:
        dots = len(spec) - len(spec.lstrip("."))
        mod = spec.lstrip(".").replace(".", "/")
        base = posixpath.dirname(from_path)
        for _ in range(max(dots - 1, 0)):
            base = posixpath.dirname(base)
        bases = [posixpath.join(base, mod)] if dots else [mod]
        if not dots:   # 절대 import — 레포 내 패키지 루트가 하위 디렉터리일 수 있다
            bases += [p[: -len(mod) - 3] + mod for p in ctx.paths if p.endswith("/" + mod + ".py")]
        for b in bases:
            for cand in (b + ".py", b + "/__init__.py"):
                if _norm(cand) in ctx.paths:
                    return _norm(cand)
        return None
    if spec.startswith("@/") or spec.startswith("~/"):
        spec = "src/" + spec[2:]
    elif not spec.startswith("."):
        return None                                  # npm 패키지
    base = posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec)) if spec.startswith(".") else spec
    cands = [base] + [base + e for e in _JS_EXTS] + [base + "/index" + e for e in _JS_EXTS]
    if base.endswith(".js"):                         # TS 의 './x.js' import → x.ts
        cands += [base[:-3] + e for e in (".ts", ".tsx")]
    for c in cands:
        if _norm(c) in ctx.paths:
            return _norm(c)
    return None


def _require_spec(src: _Src, n: Node | None) -> str | None:
    """`require('x')` 노드면 'x'."""
    if n is None:
        return None
    if n.type == "await_expression" and n.named_children:
        n = n.named_children[0]
    if n.type == "call_expression" and src.text(n.child_by_field_name("function")) == "require":
        args = n.child_by_field_name("arguments")
        if args is not None and args.named_children:
            return _str_value(src, args.named_children[0])
    return None


def _bindings(ctx: _Ctx, src: _Src) -> dict:
    """파일 최상위 이름 → 무엇을 가리키나. ("module", path) | ("export", path, name) | ("node", Node) | ("new", Node)."""
    if src.path in ctx.bindings:
        return ctx.bindings[src.path]
    b: dict = {}
    ctx.bindings[src.path] = b
    for top in src.root.named_children:
        stmt = top.child_by_field_name("declaration") if top.type == "export_statement" else top
        if stmt is None:
            continue
        t = stmt.type
        if src.py:
            _py_bindings(ctx, src, stmt, b)
            continue
        if t == "import_statement":
            spec = _str_value(src, stmt.child_by_field_name("source"))
            mod = _resolve_module(ctx, src.path, spec or "", False)
            if not mod:
                continue
            for c in _walk(stmt):
                if c.type == "import_clause":
                    for cc in c.named_children:
                        if cc.type == "identifier":
                            b[src.text(cc)] = ("export", mod, "default")
                elif c.type == "namespace_import":
                    for cc in c.named_children:
                        if cc.type == "identifier":
                            b[src.text(cc)] = ("module", mod)
                elif c.type == "import_specifier":
                    name = c.child_by_field_name("name")
                    alias = c.child_by_field_name("alias") or name
                    b[src.text(alias)] = ("export", mod, src.text(name))
        elif t in ("lexical_declaration", "variable_declaration"):
            for d in stmt.named_children:
                if d.type != "variable_declarator":
                    continue
                name, value = d.child_by_field_name("name"), d.child_by_field_name("value")
                if value is None:
                    continue
                spec = _require_spec(src, value)
                member = None
                if spec is None and value.type == "member_expression":
                    spec = _require_spec(src, value.child_by_field_name("object"))
                    member = src.text(value.child_by_field_name("property"))
                if spec is not None:
                    mod = _resolve_module(ctx, src.path, spec, False)
                    if not mod:
                        continue
                    if name.type == "identifier":
                        b[src.text(name)] = ("export", mod, member) if member else ("module", mod)
                    elif name.type == "object_pattern":
                        for k, alias in _pattern_pairs(src, name):
                            b[alias] = ("export", mod, k)
                elif name.type == "identifier":
                    b[src.text(name)] = ("node", value)
        elif t in ("function_declaration", "generator_function_declaration", "class_declaration",
                   "abstract_class_declaration"):
            nm = stmt.child_by_field_name("name")
            if nm is not None:
                b[src.text(nm)] = ("node", stmt)
    return b


def _py_bindings(ctx: _Ctx, src: _Src, stmt: Node, b: dict) -> None:
    t = stmt.type
    if t == "decorated_definition":
        stmt = stmt.child_by_field_name("definition") or stmt
        t = stmt.type
    if t == "import_from_statement":
        modn = stmt.child_by_field_name("module_name")
        mod = _resolve_module(ctx, src.path, src.text(modn), True)
        for c in stmt.children_by_field_name("name"):
            if c.type == "aliased_import":
                name, alias = src.text(c.child_by_field_name("name")), src.text(c.child_by_field_name("alias"))
            else:
                name = alias = src.text(c)
            sub = _resolve_module(ctx, src.path, src.text(modn) + "." + name, True)
            if sub:
                b[alias] = ("module", sub)
            elif mod:
                b[alias] = ("export", mod, name)
    elif t == "import_statement":
        for c in stmt.children_by_field_name("name"):
            if c.type == "aliased_import":
                name, alias = src.text(c.child_by_field_name("name")), src.text(c.child_by_field_name("alias"))
            else:
                name = alias = src.text(c)
            mod = _resolve_module(ctx, src.path, name, True)
            if mod:
                b[alias.split(".")[0] if alias == name else alias] = ("module", mod)
    elif t in ("function_definition", "class_definition"):
        nm = stmt.child_by_field_name("name")
        if nm is not None:
            b[src.text(nm)] = ("node", stmt)
    elif t == "expression_statement" and stmt.named_children and stmt.named_children[0].type == "assignment":
        a = stmt.named_children[0]
        left, right = a.child_by_field_name("left"), a.child_by_field_name("right")
        if left is not None and left.type == "identifier" and right is not None:
            b[src.text(left)] = ("node", right)


def _pattern_pairs(src: _Src, pat: Node) -> list[tuple[str, str]]:
    """`{a, b: c, d = 1}` → [(a,a), (b,c), (d,d)]."""
    out = []
    for c in pat.named_children:
        if c.type == "shorthand_property_identifier_pattern":
            out.append((src.text(c), src.text(c)))
        elif c.type == "pair_pattern":
            k, v = c.child_by_field_name("key"), c.child_by_field_name("value")
            if v is not None and v.type == "assignment_pattern":
                v = v.child_by_field_name("left")
            if v is not None and v.type == "identifier":
                out.append((src.text(k), src.text(v)))
        elif c.type == "object_assignment_pattern":
            left = c.child_by_field_name("left")
            out.append((src.text(left), src.text(left)))
    return out


# ───────────────────────────────────────────────────── 이름 → 대상(함수/클래스…)
def _from_binding(ctx: _Ctx, src: _Src, bind, depth: int = 0) -> _Target | None:
    if bind is None or depth > 6:
        return None
    kind = bind[0]
    if kind == "module":
        s = ctx.load(bind[1])
        return _Target("module", s) if s else None
    if kind == "export":
        s = ctx.load(bind[1])
        return _export(ctx, s, bind[2], depth + 1) if s else None
    if kind == "node":
        return _eval(ctx, src, bind[1], {}, depth + 1)
    return None


def _lookup(ctx: _Ctx, src: _Src, name: str, local: dict | None = None, depth: int = 0) -> _Target | None:
    if local and name in local:
        return local[name]
    return _from_binding(ctx, src, _bindings(ctx, src).get(name), depth)


def _eval(ctx: _Ctx, src: _Src, n: Node | None, local: dict, depth: int = 0) -> _Target | None:
    """식 노드 → 대상. require/new/식별자/멤버/함수·클래스·객체 리터럴."""
    if n is None or depth > 8:
        return None
    t = n.type
    if t in ("await_expression", "parenthesized_expression", "as_expression", "non_null_expression"):
        return _eval(ctx, src, n.named_children[0] if n.named_children else None, local, depth + 1)
    if _is_func(n):
        return _Target("func", src, n)
    if t in ("class_declaration", "class", "abstract_class_declaration", "class_definition"):
        return _Target("class", src, n)
    if t == "object":
        return _Target("object", src, n)
    if t == "identifier":
        return _lookup(ctx, src, src.text(n), local, depth + 1)
    if t == "new_expression":
        cls = _eval(ctx, src, n.child_by_field_name("constructor"), local, depth + 1)
        return _Target("instance", cls.src, cls.node) if cls and cls.kind == "class" else None
    if t in ("call_expression", "call"):
        spec = _require_spec(src, n)
        if spec is not None:
            mod = _resolve_module(ctx, src.path, spec, False)
            s = ctx.load(mod) if mod else None
            return _Target("module", s) if s else None
        fn = n.child_by_field_name("function")
        callee = _eval(ctx, src, fn, local, depth + 1)
        if callee and callee.kind == "class":            # Python: Svc() 는 인스턴스 생성
            return _Target("instance", callee.src, callee.node)
        return None
    if t in ("member_expression", "attribute"):
        obj = n.child_by_field_name("object")
        prop = n.child_by_field_name("property") or n.child_by_field_name("attribute")
        base = _eval(ctx, src, obj, local, depth + 1)
        return _member(ctx, base, src.text(prop), depth + 1) if base else None
    return None


def _export(ctx: _Ctx, src: _Src, name: str, depth: int = 0) -> _Target | None:
    """모듈 src 가 name 으로 내보내는 것. CommonJS·ESM·Python 모듈 최상위."""
    if src.py:
        return _lookup(ctx, src, name, None, depth + 1)
    default_expr = None
    for top in src.root.named_children:
        if top.type == "export_statement":
            decl = top.child_by_field_name("declaration")
            val = top.child_by_field_name("value")
            is_default = any(c.type == "default" for c in top.children)
            if decl is not None:
                nm = decl.child_by_field_name("name")
                if nm is not None and src.text(nm) == name:
                    return _eval(ctx, src, decl, {}, depth + 1)
                if is_default and name == "default":
                    return _eval(ctx, src, decl, {}, depth + 1)
                for d in decl.named_children:
                    if d.type == "variable_declarator" and src.text(d.child_by_field_name("name")) == name:
                        return _eval(ctx, src, d.child_by_field_name("value"), {}, depth + 1)
            if is_default and val is not None:
                default_expr = val
            for c in _walk(top):
                if c.type == "export_specifier":
                    nm = c.child_by_field_name("name")
                    alias = c.child_by_field_name("alias") or nm
                    if src.text(alias) == name:
                        return _lookup(ctx, src, src.text(nm), None, depth + 1)
        elif top.type == "expression_statement" and top.named_children:
            a = top.named_children[0]
            if a.type != "assignment_expression":
                continue
            left, right = src.text(a.child_by_field_name("left")), a.child_by_field_name("right")
            if left in (f"exports.{name}", f"module.exports.{name}"):
                return _eval(ctx, src, right, {}, depth + 1)
            if left == "module.exports":
                default_expr = right
    if default_expr is None:
        return None
    base = _eval(ctx, src, default_expr, {}, depth + 1)
    if name == "default" or base is None:
        return base
    return _member(ctx, base, name, depth + 1)


def _class_body_members(cls: Node):
    body = cls.child_by_field_name("body")
    return body.named_children if body is not None else []


def _member(ctx: _Ctx, base: _Target | None, name: str, depth: int = 0) -> _Target | None:
    if base is None or depth > 8:
        return None
    src = base.src
    if base.kind == "module":
        return _export(ctx, src, name, depth + 1)
    if base.kind == "object" and base.node is not None:
        for c in base.node.named_children:
            if c.type == "pair" and _key(src, c.child_by_field_name("key")) == name:
                return _eval(ctx, src, c.child_by_field_name("value"), {}, depth + 1)
            if c.type == "method_definition" and src.text(c.child_by_field_name("name")) == name:
                return _Target("func", src, c, name)
            if c.type == "shorthand_property_identifier" and src.text(c) == name:
                return _lookup(ctx, src, name, None, depth + 1)
        return None
    if base.kind in ("class", "instance") and base.node is not None:
        for c in _class_body_members(base.node):
            fn = c.child_by_field_name("definition") if c.type == "decorated_definition" else c
            if fn is None:
                continue
            if fn.type in ("method_definition", "function_definition") and src.text(fn.child_by_field_name("name")) == name:
                return _Target("func", src, fn, name)
            if fn.type in ("field_definition", "public_field_definition"):
                prop = fn.child_by_field_name("property") or fn.child_by_field_name("name")
                if src.text(prop) == name:
                    return _eval(ctx, src, fn.child_by_field_name("value"), {}, depth + 1)
        heritage = next((c for c in base.node.children if c.type in ("class_heritage", "argument_list")), None)
        if heritage is not None:                       # 상속 한 단계 — extends Base
            parent = next((c for c in _walk(heritage) if c.type == "identifier"), None)
            if parent is not None:
                p = _lookup(ctx, src, src.text(parent), None, depth + 1)
                if p and p.kind == "class":
                    return _member(ctx, _Target(base.kind, p.src, p.node), name, depth + 1)
    return None


def _key(src: _Src, k: Node | None) -> str:
    return _str_value(src, k) if k is not None and k.type == "string" else src.text(k)


def _this_field(ctx: _Ctx, src: _Src, cls: Node | None, field_name: str, depth: int) -> _Target | None:
    """클래스 생성자/필드에서 `this.<field> = <식>` 을 찾아 그 대상."""
    if cls is None:
        return None
    for c in _class_body_members(cls):
        if c.type in ("field_definition", "public_field_definition"):
            prop = c.child_by_field_name("property") or c.child_by_field_name("name")
            if src.text(prop) == field_name:
                return _eval(ctx, src, c.child_by_field_name("value"), {}, depth + 1)
        for a in _walk(c):
            if a.type in ("assignment_expression", "assignment"):
                left = src.text(a.child_by_field_name("left"))
                if left in (f"this.{field_name}", f"self.{field_name}"):
                    return _eval(ctx, src, a.child_by_field_name("right"), {}, depth + 1)
    return None


# ──────────────────────────────────────────────────────────────── 오염 판정
def _join_taint(*ts):
    return INPUT if INPUT in ts else (REQ if REQ in ts else None)


def _chain(src: _Src, n: Node) -> list[str] | None:
    """`req.body.email` → ['req','body','email']. 단순 멤버 체인이 아니면 None."""
    parts = []
    while n is not None and n.type in ("member_expression", "attribute", "subscript_expression", "subscript"):
        if n.type in ("subscript_expression", "subscript"):
            idx = n.child_by_field_name("index") or n.child_by_field_name("subscript")
            parts.append(_str_value(src, idx) or "[]")
        else:
            prop = n.child_by_field_name("property") or n.child_by_field_name("attribute")
            parts.append(src.text(prop))
        n = n.child_by_field_name("object") or n.child_by_field_name("value")
    if n is None or n.type not in ("identifier", "this"):
        return None
    parts.append(src.text(n))
    return list(reversed(parts))


def _taint(src: _Src, n: Node | None, env: dict) -> str | None:
    if n is None:
        return None
    t = n.type
    if t == "identifier":
        return env.get(src.text(n))
    if t in ("string_fragment", "number", "true", "false", "null", "undefined", "integer", "float",
             "none", "comment", "regex"):
        return None
    if _is_func(n):
        return None
    if t in ("member_expression", "attribute", "subscript_expression", "subscript"):
        parts = _chain(src, n)
        if parts:
            root = env.get(parts[0])
            if root == INPUT:
                return INPUT
            if root == REQ:
                if len(parts) == 1:
                    return REQ
                return INPUT if parts[1] in _REQ_INPUT_FIELDS else None
            if parts[0] in ("this", "self"):
                return None
            return None
        obj = n.child_by_field_name("object") or n.child_by_field_name("value")
        return _taint(src, obj, env)
    if t in ("call_expression", "call"):
        fn = n.child_by_field_name("function")
        fn_text = src.text(fn)
        if _SANITIZERS.search(fn_text):
            return None
        args = n.child_by_field_name("arguments")
        if fn is not None and fn.type in ("member_expression", "attribute"):
            obj = fn.child_by_field_name("object")
            prop = src.text(fn.child_by_field_name("property") or fn.child_by_field_name("attribute"))
            ot = _taint(src, obj, env)
            if ot == REQ and prop in _REQ_INPUT_CALLS:
                return INPUT                               # req.get('x'), request.get_json()
            if ot == INPUT:
                return INPUT                               # email.trim(), body.name.toLowerCase()
        return _join_taint(*(_taint(src, a, env) for a in (args.named_children if args is not None else [])))
    if t == "pair":
        return _taint(src, n.child_by_field_name("value"), env)
    if t in ("keyword_argument",):
        return _taint(src, n.child_by_field_name("value"), env)
    return _join_taint(*(_taint(src, c, env) for c in n.named_children))


# ────────────────────────────────────────────────────────── 인자 구성 판정
def _leaves(src: _Src, n: Node, env_defs: dict, depth: int, seen: set) -> list[tuple[str, Node]]:
    """쿼리 식을 (종류, 노드) 조각으로 편다 — 'lit' 고정 문자열, 'expr' 보간/연결된 식."""
    t = n.type
    if t in ("parenthesized_expression", "await_expression") and n.named_children:
        return _leaves(src, n.named_children[0], env_defs, depth, seen)
    if t in ("string", "template_string"):
        subs = [c for c in n.named_children if c.type in ("template_substitution", "interpolation")]
        out = [("lit", n)]
        for s in subs:
            inner = next((c for c in s.named_children if c.type not in ("format_specifier", "type_conversion")), None)
            if inner is not None:
                out += _leaves(src, inner, env_defs, depth, seen)
        return out
    if t in ("binary_expression", "binary_operator"):
        op = src.text(n.child_by_field_name("operator"))
        if op in ("+", "%"):
            return (_leaves(src, n.child_by_field_name("left"), env_defs, depth, seen)
                    + _leaves(src, n.child_by_field_name("right"), env_defs, depth, seen))
    if t == "identifier" and depth < 4:
        name = src.text(n)
        defs = env_defs.get(name)
        if defs and name not in seen:
            seen = seen | {name}
            out = []
            for d in defs:
                out += _leaves(src, d, env_defs, depth + 1, seen)
            return out
    if t in ("call_expression", "call"):
        fn = n.child_by_field_name("function")
        fn_text = src.text(fn)
        args = n.child_by_field_name("arguments")
        if fn is not None and fn.type in ("member_expression", "attribute"):
            prop = src.text(fn.child_by_field_name("property") or fn.child_by_field_name("attribute"))
            if prop in ("format", "concat", "join", "replace", "replaceAll"):   # 'x {}'.format(v), a.concat(b)
                out = _leaves(src, fn.child_by_field_name("object"), env_defs, depth, seen)
                for a in (args.named_children if args is not None else []):
                    out += _leaves(src, a, env_defs, depth, seen)
                return out
        if _SQL_SAFE_BUILDERS.search(fn_text):
            return [("safe", n)]
    return [("expr", n)]


def _is_const_like(src: _Src, n: Node, env_defs: dict) -> bool:
    if n.type in ("number", "integer", "float", "true", "false"):
        return True
    txt = src.text(n)
    if re.fullmatch(r"[A-Z][A-Z0-9_]*(?:\.[A-Z0-9_]+)*", txt.split(".")[-1] if "." in txt else txt):
        return True                                   # TABLE_NAME, config.USERS_TABLE
    return False


def _construction(src: _Src, n: Node, env: dict, env_defs: dict, category: str) -> tuple[str, list[str]]:
    """위험 인자 노드 → (구성 종류, 요청 값이 섞인 식들)."""
    if category != sinks.SQL:
        tainted = _taint(src, n, env)
        if tainted == INPUT:
            leaves = _leaves(src, n, env_defs, 0, set())
            exprs = [src.text(x) for k, x in leaves if k == "expr" and _taint(src, x, env) == INPUT]
            concat = any(k == "lit" for k, _ in leaves) and exprs
            return (CONCAT_INPUT if concat else DIRECT_INPUT), (exprs or [src.text(n)])[:5]
        if tainted == REQ and category == sinks.NOSQL:
            return DIRECT_INPUT, [src.text(n)]
        return (LITERAL if n.type in ("string", "template_string") else UNKNOWN), []
    leaves = _leaves(src, n, env_defs, 0, set())
    if any(k == "safe" for k, _ in leaves) and not any(k == "expr" for k, _ in leaves):
        return PARAMETERIZED, []
    exprs = [x for k, x in leaves if k == "expr"]
    has_lit = any(k == "lit" for k, _ in leaves)
    if not exprs:
        return LITERAL, []
    tainted = [src.text(x) for x in exprs if _taint(src, x, env) == INPUT]
    if tainted:
        return (CONCAT_INPUT if has_lit else DIRECT_INPUT), tainted[:5]
    if not has_lit:
        return UNKNOWN, []
    if all(_is_const_like(src, x, env_defs) for x in exprs):
        return CONCAT_CONST, []
    return CONCAT_OTHER, [src.text(x) for x in exprs][:5]


_DANGEROUS = {CONCAT_INPUT, DIRECT_INPUT}


# ───────────────────────────────────────────────────────────── 함수 분석
def _params(src: _Src, fn: Node) -> list[Node]:
    p = fn.child_by_field_name("parameters") or fn.child_by_field_name("parameter")
    if p is None:
        return []
    if p.type == "identifier":
        return [p]
    out = []
    for c in p.named_children:
        if c.type in ("comment",):
            continue
        if src.py and c.type == "identifier" and src.text(c) in ("self", "cls"):
            continue
        out.append(c)
    return out


def _param_names(src: _Src, p: Node) -> tuple[str | None, Node | None]:
    """파라미터 노드 → (이름, 구조분해 패턴)."""
    t = p.type
    if t in ("required_parameter", "optional_parameter"):
        pat = p.child_by_field_name("pattern")
        return _param_names(src, pat) if pat is not None else (None, None)
    if t == "assignment_pattern":
        return _param_names(src, p.child_by_field_name("left"))
    if t in ("default_parameter", "typed_default_parameter"):
        return src.text(p.child_by_field_name("name")), None
    if t == "typed_parameter":
        ident = next((c for c in p.named_children if c.type == "identifier"), None)
        return src.text(ident), None
    if t == "identifier":
        return src.text(p), None
    if t == "object_pattern":
        return None, p
    return None, None


def _py_param_taint(src: _Src, p: Node) -> str | None:
    """FastAPI 핸들러 파라미터 — Depends 주입·DB 세션은 깨끗, Request 는 요청 객체, 나머지는 입력."""
    txt = src.text(p)
    if "Depends(" in txt or "Security(" in txt or re.search(r":\s*(?:Session|AsyncSession|BackgroundTasks|Response)\b", txt):
        return None
    if re.search(r":\s*(?:Request|WebSocket)\b", txt):
        return REQ
    return INPUT


def _bind_params(src: _Src, fn: Node, arg_taints: list, env: dict) -> None:
    for i, p in enumerate(_params(src, fn)):
        taint = arg_taints[i] if i < len(arg_taints) else None
        name, pat = _param_names(src, p)
        if name:
            env[name] = taint
        elif pat is not None:
            for k, alias in _pattern_pairs(src, pat):
                env[alias] = INPUT if taint == INPUT or (taint == REQ and k in _REQ_INPUT_FIELDS) else None


def _assign(src: _Src, target: Node | None, value: Node | None, env: dict, env_defs: dict, augment: bool = False):
    if target is None or value is None:
        return
    taint = _taint(src, value, env)
    if target.type == "identifier":
        name = src.text(target)
        if augment:
            env[name] = _join_taint(env.get(name), taint)
            env_defs.setdefault(name, []).append(value)
        else:
            env[name] = taint
            env_defs[name] = [value]
    elif target.type == "object_pattern":
        for k, alias in _pattern_pairs(src, target):
            env[alias] = INPUT if taint == INPUT or (taint == REQ and k in _REQ_INPUT_FIELDS) else None
    elif target.type in ("array_pattern", "pattern_list", "tuple_pattern"):
        for c in target.named_children:
            if c.type == "identifier":
                env[src.text(c)] = taint


@dataclass
class _Frame:
    src: _Src
    fn: Node
    cls: Node | None
    hops: list
    lifted: frozenset = frozenset()     # 호출자 쪽으로 승격된 sink 호출(이 함수는 래퍼) — 여기선 기록 안 함


@dataclass
class _Wrap:
    """이 함수의 index 번째 파라미터가 가공 없이 sink 의 위험 인자로 간다(= DB 헬퍼 같은 sink 래퍼).

    `export const query = (sql, params) => pool.query(sql, params)` 처럼 감싸 두면 위험한 문자열 연결은
    **호출자**에서 일어난다. 래퍼 안의 sink 줄을 가리키면 고칠 곳을 틀리게 안내하므로, 호출 지점에서
    호출자 인자의 구성으로 판정하고 위치도 호출 지점으로 잡는다. binds 는 바인딩 값이 들어오는 파라미터.
    """
    index: int
    sink: sinks.Sink
    via: list
    binds: int | None


def _plain_params(src: _Src, fn: Node) -> list[str | None]:
    return [_param_names(src, p)[0] for p in _params(src, fn)]


def _reassigned(src: _Src, body: Node) -> set[str]:
    out = set()
    for n in _walk(body):
        if n.type == "variable_declarator":
            t = n.child_by_field_name("name")
        elif n.type in ("assignment_expression", "assignment", "augmented_assignment_expression", "augmented_assignment"):
            t = n.child_by_field_name("left")
        else:
            continue
        if t is not None and t.type == "identifier":
            out.add(src.text(t))
    return out


def _call_args(n: Node) -> list[Node]:
    args_node = n.child_by_field_name("arguments")
    if args_node is None or args_node.type == "template_string":
        return []
    return [a for a in args_node.named_children if a.type != "comment"]


def _wrapper_summary(ctx: _Ctx, src: _Src, fn: Node, depth: int = 0) -> tuple[list, frozenset]:
    """함수 → (래퍼 항목들, 호출자로 승격돼 이 함수 안에선 기록하지 않을 호출 노드들). 오염과 무관해 캐시."""
    key = (src.path, fn.start_byte)
    if key in ctx.wraps:
        return ctx.wraps[key]
    ctx.wraps[key] = ([], frozenset())                  # 재귀 호출 방어
    names = _plain_params(src, fn)
    body = fn.child_by_field_name("body")
    if body is None or not any(names):
        return ctx.wraps[key]
    fixed = _reassigned(src, body)

    def param_index(a: Node | None) -> int | None:
        if a is None or a.type != "identifier":
            return None
        nm = src.text(a)
        return names.index(nm) if nm in names and nm not in fixed else None

    wraps, lifted = [], set()
    frame = _Frame(src, fn, _enclosing_class(fn), [])
    for n in _walk(body):
        if n.type not in ("call_expression", "call"):
            continue
        fn_node = n.child_by_field_name("function")
        if fn_node is None:
            continue
        args = _call_args(n)
        sink = sinks.match(src.text(fn_node))
        if sink is not None:
            for i in sink.danger_args:
                pi = param_index(args[i] if i < len(args) else None)
                if pi is not None:
                    binds = param_index(args[i + 1]) if i + 1 < len(args) else None
                    wraps.append(_Wrap(pi, sink, [{"file": src.path, "line": _line(n), "name": src.text(fn_node),
                                                   "kind": "sink"}], binds))
                    lifted.add(n.start_byte)
            continue
        if depth >= 3 or fn_node.type not in ("identifier", "member_expression", "attribute"):
            continue
        tgt = _resolve_callee(ctx, frame, fn_node, {})
        if tgt is None or tgt.kind != "func" or tgt.node is None:
            continue
        for w in _wrapper_summary(ctx, tgt.src, tgt.node, depth + 1)[0]:
            pi = param_index(args[w.index] if w.index < len(args) else None)
            if pi is not None:
                binds = param_index(args[w.binds]) if w.binds is not None and w.binds < len(args) else None
                hop = {"file": tgt.src.path, "line": _line(tgt.node), "name": _func_name(tgt.src, tgt.node),
                       "kind": "function"}
                wraps.append(_Wrap(pi, w.sink, [hop] + w.via, binds))
                lifted.add(n.start_byte)
    ctx.wraps[key] = (wraps, frozenset(lifted))
    return ctx.wraps[key]


def _enclosing_class(fn: Node) -> Node | None:
    n = fn.parent
    while n is not None:
        if n.type in ("class_declaration", "class", "abstract_class_declaration", "class_definition"):
            return n
        n = n.parent
    return None


def _func_name(src: _Src, fn: Node) -> str:
    nm = fn.child_by_field_name("name")
    if nm is not None:
        base = src.text(nm)
    else:
        par = fn.parent
        base = ""
        if par is not None and par.type == "variable_declarator":
            base = src.text(par.child_by_field_name("name"))
        elif par is not None and par.type in ("pair", "field_definition", "public_field_definition"):
            base = src.text(par.child_by_field_name("key") or par.child_by_field_name("property")
                            or par.child_by_field_name("name"))
        elif par is not None and par.type == "assignment_expression":
            base = src.text(par.child_by_field_name("left")).replace("module.exports.", "").replace("exports.", "")
        base = base or "(익명 함수)"
    cls = _enclosing_class(fn)
    if cls is not None and cls.child_by_field_name("name") is not None:
        return f"{src.text(cls.child_by_field_name('name'))}.{base}"
    return base


def _resolve_callee(ctx: _Ctx, fr: _Frame, fn_node: Node, local: dict) -> _Target | None:
    t = fn_node.type
    if t in ("member_expression", "attribute"):
        parts = _chain(fr.src, fn_node)
        if parts and parts[0] in ("this", "self") and len(parts) >= 2:
            if len(parts) == 2:                              # this.helper()
                return _member(ctx, _Target("instance", fr.src, fr.cls), parts[1]) if fr.cls else None
            base = _this_field(ctx, fr.src, fr.cls, parts[1], 0)   # this.svc.m()
            for p in parts[2:]:
                base = _member(ctx, base, p) if base else None
            return base
    return _eval(ctx, fr.src, fn_node, local)


def _analyze(ctx: _Ctx, fr: _Frame, arg_taints: list, depth: int) -> None:
    key = (fr.src.path, fr.fn.start_byte, tuple(arg_taints))
    if key in ctx.seen or ctx.funcs >= MAX_FUNCS:
        return
    ctx.seen.add(key)
    ctx.funcs += 1
    src = fr.src
    env: dict = {}
    env_defs: dict = {}
    local_targets: dict = {}
    if src.py:
        env["request"] = REQ                             # Flask 전역 request
    _bind_params(src, fr.fn, arg_taints, env)
    body = fr.fn.child_by_field_name("body")
    if body is None:
        return
    for n in _walk(body):
        t = n.type
        if t == "variable_declarator":
            _assign(src, n.child_by_field_name("name"), n.child_by_field_name("value"), env, env_defs)
            name, value = n.child_by_field_name("name"), n.child_by_field_name("value")
            if name is not None and name.type == "identifier" and value is not None and value.type in (
                    "new_expression", "call_expression"):
                tgt = _eval(ctx, src, value, local_targets)
                if tgt is not None:
                    local_targets[src.text(name)] = tgt
        elif t in ("assignment_expression", "assignment"):
            _assign(src, n.child_by_field_name("left"), n.child_by_field_name("right"), env, env_defs)
        elif t in ("augmented_assignment_expression", "augmented_assignment"):
            _assign(src, n.child_by_field_name("left"), n.child_by_field_name("right"), env, env_defs, augment=True)
        elif t in ("for_in_statement", "for_statement") and src.py:
            _assign(src, n.child_by_field_name("left"), n.child_by_field_name("right"), env, env_defs)
        elif t in ("call_expression", "call"):
            _on_call(ctx, fr, n, env, env_defs, local_targets, depth)


def _judge_arg(src: _Src, arg: Node, env: dict, env_defs: dict, category: str, has_binds: bool) -> tuple[str, list]:
    kind, tainted = _construction(src, arg, env, env_defs, category)
    if category == sinks.SQL and kind == LITERAL and has_binds:
        kind = PARAMETERIZED                                 # 'SELECT … ?' + [값] — 바인딩
    return kind, tainted


def _record(ctx: _Ctx, fr: _Frame, n: Node, sink: sinks.Sink, callee: str, kind: str, tainted: list,
            arg: Node, via: list | None = None) -> None:
    src, line = fr.src, _line(n)
    rec = {
        "category": sink.category, "label": sink.label, "callee": callee,
        "file": src.path, "line": line, "code": src.line_text(line),
        "arg": src.text(arg)[:240], "construction": kind,
        "construction_ko": CONSTRUCTION_KO.get(kind, kind),
        "dangerous": kind in _DANGEROUS, "tainted": tainted,
        "function": _func_name(src, fr.fn),
        "chain": list(fr.hops) + [{"file": src.path, "line": line, "name": callee, "kind": "sink"}],
    }
    if via:
        inner = via[-1]
        rec["via"] = via
        rec["label"] = f"{sink.label} (헬퍼 경유: {inner['name']} @ {posixpath.basename(inner['file'])}:{inner['line']})"
    ctx.sinks.append(rec)


def _on_call(ctx: _Ctx, fr: _Frame, n: Node, env: dict, env_defs: dict, local: dict, depth: int) -> None:
    src = fr.src
    fn_node = n.child_by_field_name("function")
    if fn_node is None:
        return
    callee = src.text(fn_node)
    args = _call_args(n)
    lifted = n.start_byte in fr.lifted                   # 이 함수가 래퍼 — 호출자 쪽에서 이미 판정
    sink = sinks.match(callee)
    if sink is not None and args:
        if lifted:
            return
        worst = None
        for i in sink.danger_args:
            if i >= len(args) or args[i].type == "keyword_argument":
                continue
            kind, tainted = _judge_arg(src, args[i], env, env_defs, sink.category, len(args) > i + 1)
            if sink.category == sinks.XSS and kind == DIRECT_INPUT and args[i].type in ("object", "dictionary"):
                continue                                     # res.send({...}) 은 JSON
            if worst is None or (kind in _DANGEROUS and worst[0] not in _DANGEROUS):
                worst = (kind, tainted, args[i])
        if worst is not None:
            _record(ctx, fr, n, sink, callee, *worst)
        return
    if fn_node.type not in ("identifier", "member_expression", "attribute"):
        return
    target = _resolve_callee(ctx, fr, fn_node, local)
    if target is None or target.kind != "func" or target.node is None:
        return
    callee_fn = target.node
    if not lifted:
        # sink 래퍼 호출 — 호출자 인자의 구성으로 판정하고 위치는 이 호출 줄(고칠 곳)로.
        for w in _wrapper_summary(ctx, target.src, callee_fn)[0]:
            if w.index >= len(args):
                continue
            has_binds = w.binds is not None and w.binds < len(args)
            kind, tainted = _judge_arg(src, args[w.index], env, env_defs, w.sink.category, has_binds)
            hop = {"file": target.src.path, "line": _line(callee_fn), "name": _func_name(target.src, callee_fn),
                   "kind": "function"}
            _record(ctx, fr, n, w.sink, callee, kind, tainted, args[w.index], via=[hop] + w.via)
    if depth >= MAX_DEPTH:
        return
    taints = [_taint(src, a, env) for a in args]
    hop = {"file": target.src.path, "line": _line(callee_fn), "name": _func_name(target.src, callee_fn),
           "kind": "function", "call_file": src.path, "call_line": _line(n), "call": callee}
    sub_lifted = _wrapper_summary(ctx, target.src, callee_fn)[1]
    _analyze(ctx, _Frame(target.src, callee_fn, _enclosing_class(callee_fn), fr.hops + [hop], sub_lifted),
             taints, depth + 1)


# ─────────────────────────────────────────────────────────── 핸들러 찾기
def _route_call(src: _Src, method: str, rel: str | None, line: int | None) -> Node | None:
    """라우트 선언 호출 노드 — `router.post('/x', …)` 또는 `router.route('/x').post(…)`."""
    m = method.lower()
    best = None
    for n in _walk(src.root):
        if n.type != "call_expression":
            continue
        fn = n.child_by_field_name("function")
        if fn is None or fn.type != "member_expression" or src.text(fn.child_by_field_name("property")).lower() != m:
            continue
        args = n.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        path = _str_value(src, first)
        obj = fn.child_by_field_name("object")
        if path is None and obj is not None:             # 체인: .route('/x').post(h)
            for c in _walk(obj):
                if c.type == "call_expression" and src.text(c.child_by_field_name("function")).endswith(".route"):
                    a = c.child_by_field_name("arguments")
                    path = _str_value(src, a.named_children[0]) if a is not None and a.named_children else None
                    break
        if rel is not None and path != rel:
            continue
        if line is not None and not (_line(n) <= line + 1 and n.end_point[0] + 1 >= line - 1):
            if best is None:
                best = n
            continue
        return n
    return best


def _unwrap_handler(src: _Src, n: Node | None) -> Node | None:
    """asyncHandler(ctrl.x) / catchAsync(async (req,res)=>…) 래퍼를 벗긴다."""
    for _ in range(3):
        if n is None or n.type != "call_expression":
            return n
        args = n.child_by_field_name("arguments")
        inner = [a for a in (args.named_children if args is not None else [])
                 if _is_func(a) or a.type in ("identifier", "member_expression")]
        n = inner[-1] if inner else None
    return n


def _py_handler(src: _Src, method: str, rel: str | None, line: int | None) -> Node | None:
    for n in _walk(src.root):
        if n.type != "decorated_definition":
            continue
        for d in n.children:
            if d.type != "decorator":
                continue
            txt = src.text(d)
            if (rel is None or f"'{rel}'" in txt or f'"{rel}"' in txt) and (
                    f".{method.lower()}(" in txt or ".route(" in txt or ".api_route(" in txt):
                if line is None or abs(_line(d) - line) <= 2 or rel is not None:
                    return n.child_by_field_name("definition")
    return None


def _func_at(src: _Src, line: int) -> Node | None:
    best = None
    for n in _walk(src.root):
        if _is_func(n) and _line(n) <= line <= n.end_point[0] + 1:
            if _line(n) == line:
                return n
            if best is None or n.start_byte > best.start_byte:
                best = n
    if best is None:                                      # `const f = async (…) =>` — 선언 줄이 함수 시작보다 앞
        for n in _walk(src.root):
            if _is_func(n) and _line(n) - 1 <= line <= _line(n):
                return n
    return best


def _find_handler(ctx: _Ctx, ep: dict) -> tuple[_Target | None, dict | None]:
    route_file = ep.get("route_file")
    src = ctx.load(route_file) if route_file else None
    route_hop = None
    if src is not None:
        route_hop = {"file": src.path, "line": ep.get("route_line"), "name": f"{ep.get('method')} {ep.get('path')}",
                     "kind": "route"}
        if src.py:
            fn = _py_handler(src, ep.get("method") or "GET", ep.get("route_rel"), ep.get("route_line"))
            if fn is not None:
                return _Target("func", src, fn), route_hop
        else:
            call = _route_call(src, ep.get("method") or "GET", ep.get("route_rel"), ep.get("route_line"))
            if call is not None:
                route_hop["line"] = _line(call)
                args = call.child_by_field_name("arguments")
                cands = [a for a in (args.named_children if args is not None else [])
                         if a.type not in ("string", "template_string", "comment")]
                h = _unwrap_handler(src, cands[-1]) if cands else None
                if h is not None:
                    if _is_func(h):
                        return _Target("func", src, h), route_hop
                    tgt = _eval(ctx, src, h, {})
                    if tgt is not None and tgt.kind == "func":
                        return tgt, route_hop
    # 폴백 — 열거기가 심볼 테이블로 찾아 둔 핸들러 파일:줄
    hfile, hline = ep.get("handler_file"), ep.get("handler_line")
    hsrc = ctx.load(hfile) if hfile else None
    if hsrc is not None and hline:
        fn = _func_at(hsrc, int(hline))
        if fn is not None:
            return _Target("func", hsrc, fn), route_hop
    return None, route_hop


# ──────────────────────────────────────────────────────────────── 공개 API
def trace_endpoint(reader, module_paths, ep: dict, *, _shared: dict | None = None) -> dict:
    """엔드포인트 하나 → {handler, sinks:[…], dangerous:[…], resolved:bool}.

    sinks 각 항목: category, label, callee, file, line, code(그 줄), arg, construction(_ko), dangerous,
    tainted(요청 값이 섞인 식), function, chain([route → handler → … → sink]).
    """
    ctx = _Ctx(reader, {_norm(p) for p in module_paths or []})
    if _shared is not None:          # 파싱·바인딩·래퍼 요약은 오염과 무관 — 엔드포인트 간 공유
        ctx.cache = _shared.setdefault("cache", {})
        ctx.bindings = _shared.setdefault("bindings", {})
        ctx.wraps = _shared.setdefault("wraps", {})
    out = {"handler": None, "sinks": [], "dangerous": [], "resolved": False}
    try:
        target, route_hop = _find_handler(ctx, ep)
        if target is None:
            return out
        fn = target.node
        handler_hop = {"file": target.src.path, "line": _line(fn), "name": _func_name(target.src, fn),
                       "kind": "handler"}
        out["handler"] = handler_hop
        out["resolved"] = True
        hops = ([route_hop] if route_hop else []) + [handler_hop]
        if target.src.py:
            taints = [_py_param_taint(target.src, p) for p in _params(target.src, fn)]
        else:
            taints = [REQ, None, None]                     # (req, res, next)
        _analyze(ctx, _Frame(target.src, fn, _enclosing_class(fn), hops), taints, 0)
    except Exception:
        log.warning("sink 추적 실패: %s %s", ep.get("method"), ep.get("path"), exc_info=True)
        return out
    seen, uniq = set(), []
    for s in ctx.sinks:
        k = (s["file"], s["line"], s["category"])
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    uniq.sort(key=lambda s: (not s["dangerous"], s["file"], s["line"]))
    out["sinks"] = uniq
    out["dangerous"] = [s for s in uniq if s["dangerous"]]
    return out


def trace_endpoints(reader, module_paths, endpoints: list[dict]) -> list[dict]:
    """엔드포인트마다 `sink_profile` 을 붙여 돌려준다(제자리 수정)."""
    shared: dict = {}
    for ep in endpoints or []:
        ep["sink_profile"] = trace_endpoint(reader, module_paths, ep, _shared=shared)
    return endpoints
