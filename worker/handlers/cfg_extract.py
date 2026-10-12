"""CFG(Control Flow Graph) + PDG(데이터/제어 의존) 추출 — Phase 2 기초 계층.

WBS 2 킥오프: CPG(Code Property Graph) 개념 중 AST/Call Graph는 이미 Phase 1
(ast_extract.py/edge_extract.py)에 있다. 이 모듈은 나머지 두 축을 새로 만든다:

  레이어 1: CFG   — 함수 본문을 기본 블록으로 쪼개고 분기마다 제어 엣지를 만든다.
  레이어 2: 데이터 의존 — CFG 위에서 reaching-definitions(고정점 반복)를 돌려
            "이 변수 정의가 재정의 없이 어느 사용 지점까지 도달하는가"를 계산한다.
  레이어 3: 제어 의존 — CFG의 post-dominator tree로 "이 블록이 어느 분기에
            의해서만 실행되는가"를 도출한다(Ferrante-Ottenstein-Warren).

타입추론은 쓰지 않는다 — 두 알고리즘 모두 CFG 구조 + 변수 이름만으로 성립하는
고전적 정적분석이다. 불확실성은 우리가 모델링하지 않은 것(별칭, 객체 필드,
외부 함수 내부 동작)에서만 나오므로, 임의의 confidence 숫자 대신 그 불확실성의
종류를 그대로 이름 붙인 evidence_kind로 남긴다.

오늘 구현 범위: JavaScript/TypeScript, Python. 함수/메서드 하나마다 독립적으로
CFG를 만든다 — 함수 경계를 넘는 절차간 연결(레이어 4)은 다음 단계.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tree_sitter import Node, Parser

from worker.handlers.ast_extract import _LANGUAGES, _js_param_types, _python_param_types, _text, _ts_param_types
from worker.handlers.edge_extract import ScanIndex, _flatten_chain

# --------------------------------------------------------------------- 상수
DIRECT_ASSIGNMENT = "DIRECT_ASSIGNMENT"
ALIAS_ASSIGNMENT = "ALIAS_ASSIGNMENT"
FIELD_ALIAS = "FIELD_ALIAS"
UNKNOWN_CALL = "UNKNOWN_CALL"
# 불확실성이 아니라 "이 값이 함수 밖(매개변수)에서 들어왔다"는 사실 표시 —
# 레이어 4(절차간 연결)가 호출 지점의 인자 오염 여부를 여기 이어 붙인다.
PARAMETER = "PARAMETER"

_CALL_TYPES = {"call", "call_expression"}
_MEMBER_TYPES = {"attribute", "member_expression", "field_access"}


# --------------------------------------------------------------- CFG 자료구조
@dataclass
class Block:
    id: int
    start_line: int
    end_line: int


@dataclass
class CFGBuilder:
    blocks: dict[int, Block] = field(default_factory=dict)
    edges: list[tuple[int, int, str]] = field(default_factory=list)
    # 블록 안의 def/use 를 등장 순서 그대로 하나의 리스트에 남긴다 — 같은 블록
    # 안에서 "정의 후 사용"이 일어나면 그 지역 정의가 블록 밖에서 들어오는
    # reaching definition보다 우선해야 하므로, 순서를 잃어버리면 안 된다
    # (defs/uses 를 따로 두면 이 순서 정보가 사라져서 같은 블록 내 def→use가
    # 통째로 누락되는 버그가 생긴다 — 실제로 초기 구현에서 이 버그가 있었다).
    events: dict[int, list[tuple]] = field(default_factory=dict)  # ("def",var,line,kind) | ("use",var,line)
    # 이 함수 안에서 발견된 모든 호출식 — sink 패턴 매칭(보안 모드)에 쓴다.
    # 대입식의 RHS든 단독 호출문이든 전부 여기 모은다.
    calls: dict[int, list[tuple[int, str, list[str], list[str], list[int]]]] = field(default_factory=dict)  # bid -> [(line, target_text, arg_vars, arg_texts, arg_lines)]
    exit_blocks: list[int] = field(default_factory=list)
    # 레이어 4(절차간 연결) 용 — 위치 기반으로 호출 지점의 인자를 이 함수의
    # 파라미터에 매칭한다(self/this는 이미 제외됨, 키워드 인자는 미지원 v1).
    params: list[str] = field(default_factory=list)
    _next_id: int = 0

    def new_block(self, line: int) -> int:
        bid = self._next_id
        self._next_id += 1
        self.blocks[bid] = Block(bid, line, line)
        self.events[bid] = []
        self.calls[bid] = []
        return bid

    def touch(self, block_id: int, line: int) -> None:
        b = self.blocks[block_id]
        if b.end_line < line:
            b.end_line = line
        if b.start_line > line or b.start_line == 0:
            b.start_line = min(b.start_line, line) if b.start_line else line

    def edge(self, src: int | None, dst: int | None, label: str = "seq") -> None:
        if src is not None and dst is not None:
            self.edges.append((src, dst, label))

    def add_def(self, block_id: int, var: str, line: int, kind: str, text: str = "") -> None:
        self.events[block_id].append(("def", var, line, kind, text))
        self.touch(block_id, line)

    def add_use(self, block_id: int, var: str, line: int) -> None:
        self.events[block_id].append(("use", var, line))
        self.touch(block_id, line)

    def add_call(self, block_id: int, line: int, target_text: str,
                 arg_vars: list[str], arg_texts: list[str], arg_lines: list[int]) -> None:
        self.calls[block_id].append((line, target_text, arg_vars, arg_texts, arg_lines))


class _Unreachable:
    """break/continue/return 뒤처럼 이 지점에 도달하는 정상 흐름이 없음을 표시."""


UNREACHABLE = _Unreachable()


# --------------------------------------------------------------- 표현식 유틸
def _identifier_uses(node: Node | None, src: bytes, out: list[tuple[str, int]]) -> None:
    """표현식 서브트리에서 "변수를 읽는" identifier만 모은다.

    member/attribute 의 속성 이름(오른쪽)은 변수가 아니므로 object 쪽만 재귀한다.
    호출식은 함수 대상 + 인자 전부를 재귀한다.
    """
    if node is None:
        return
    t = node.type
    if t == "identifier":
        out.append((_text(node, src), node.start_point[0] + 1))
        return
    if t in _MEMBER_TYPES:
        obj = node.child_by_field_name("object") or node.child_by_field_name("value")
        _identifier_uses(obj, src, out)
        return
    if t in _CALL_TYPES:
        func = node.child_by_field_name("function")
        _identifier_uses(func, src, out)
        args = node.child_by_field_name("arguments") or node.child_by_field_name("argument_list")
        if args is not None:
            for c in args.children:
                if c.is_named:
                    _identifier_uses(c, src, out)
        return
    for c in node.children:
        _identifier_uses(c, src, out)


def _clean_text(s: str, limit: int = 80) -> str:
    return " ".join(s.split())[:limit]


def _find_calls(node: Node | None, src: bytes,
                 out: list[tuple[int, str, list[str], list[str], list[int]]]) -> None:
    """서브트리 안의 모든 호출식을 (line, target_text, arg_vars, arg_texts, arg_lines) 로 모은다.

    arg_vars/arg_texts/arg_lines 는 인자 "슬롯" 단위로 하나씩(포지션 정렬) — 예전엔
    인자들 안의 identifier를 전부 한 리스트로 뭉쳐서, 인자 하나에 identifier가
    0개거나 2개 이상이면 파라미터 위치와 어긋나는 문제가 있었다(레이어 4가
    위치로 인자↔파라미터를 잇기 때문에 이 정렬이 중요하다). arg_texts는
    `db.execute(req.query.q)`처럼 변수 대입 없이 바로 넘기는 source 패턴을
    호출 지점에서 직접 잡기 위해 필요하다(원문이 없으면 base identifier
    "req"만 남아 "req.query.q"라는 텍스트 자체는 사라져 버린다). arg_lines는
    호출식이 여러 줄에 걸칠 때(`pool.execute(sql,\n  [memo, id])`) 인자
    identifier의 실제 줄을 담는다 — 호출식 자체의 시작 줄(line)만 쓰면
    마지막 인자의 use_line과 어긋나 sink 매칭이 통째로 새 나간다.
    """
    if node is None:
        return
    if node.type in _CALL_TYPES:
        func = node.child_by_field_name("function")
        target_text = _clean_text(_text(func, src)) if func is not None else _clean_text(_text(node, src))
        call_line = node.start_point[0] + 1
        args_node = node.child_by_field_name("arguments") or node.child_by_field_name("argument_list")
        arg_vars: list[str] = []
        arg_texts: list[str] = []
        arg_lines: list[int] = []
        if args_node is not None:
            for c in args_node.children:
                if not c.is_named:
                    continue
                ids: list[tuple[str, int]] = []
                _identifier_uses(c, src, ids)
                arg_vars.append(ids[0][0] if ids else "?")
                arg_texts.append(_clean_text(_text(c, src)))
                arg_lines.append(ids[0][1] if ids else call_line)
        out.append((call_line, target_text, arg_vars, arg_texts, arg_lines))
        # 인자 안에 중첩 호출(`f(g(x))`)도 계속 찾는다.
        if args_node is not None:
            for c in args_node.children:
                _find_calls(c, src, out)
        return
    for c in node.children:
        _find_calls(c, src, out)


def _classify_rhs(rhs: Node | None) -> str:
    if rhs is None:
        return DIRECT_ASSIGNMENT
    if rhs.type == "identifier":
        return ALIAS_ASSIGNMENT
    if rhs.type in _CALL_TYPES:
        return UNKNOWN_CALL
    return DIRECT_ASSIGNMENT


def _def_target_name(lhs: Node, src: bytes) -> tuple[str, str] | None:
    """대입 좌변에서 (추적할 변수명, def 자체의 evidence_kind 접두) 를 뽑는다.

    `x = ...` 이면 (x, None) — 우변에 따라 최종 kind 결정은 호출자가 함.
    `obj.field = ...` 이면 obj 를 대상 변수로 삼고 FIELD_ALIAS 로 못박는다.
    """
    if lhs.type == "identifier":
        return _text(lhs, src), None
    if lhs.type in _MEMBER_TYPES:
        chain = _flatten_chain(lhs, src)
        if chain:
            return chain[0], FIELD_ALIAS
    return None


def _extract_defuse(stmt: Node, src: bytes, builder: CFGBuilder, block_id: int) -> None:
    """구조(if/for/...)가 아닌 "평범한 한 문장"에서 def/use 를 뽑아 블록에 붙인다."""
    line = stmt.start_point[0] + 1
    found_calls: list[tuple[int, str, list[str], list[str], list[int]]] = []
    _find_calls(stmt, src, found_calls)
    for cline, target, arg_vars, arg_texts, arg_lines in found_calls:
        builder.add_call(block_id, cline, target, arg_vars, arg_texts, arg_lines)

    inner = stmt
    if inner.type == "expression_statement":
        children = [c for c in inner.children if c.is_named]
        inner = children[0] if children else inner

    if inner.type in ("assignment", "assignment_expression"):
        lhs = inner.child_by_field_name("left")
        rhs = inner.child_by_field_name("right")
        target = _def_target_name(lhs, src) if lhs is not None else None
        # 우변을 먼저 읽고(use) 그 다음에 좌변에 씀(def)한다 — 실제 실행 순서와
        # 맞춰야 `x = x + 1` 같은 자기참조 대입에서 RHS의 x가 "새로 쓰이는 값"이
        # 아니라 "이전 값"을 가리키게 된다(순서를 반대로 하면 자기 자신의
        # 새 정의를 즉시 읽어버리는 오류가 생긴다).
        uses: list[tuple[str, int]] = []
        _identifier_uses(rhs, src, uses)
        if target and target[1] == FIELD_ALIAS:
            # obj.field = rhs — obj 자체도 이 대입이 성립하려면 먼저 "읽혀야" 한다.
            uses.append((target[0], line))
        for v, ln in uses:
            builder.add_use(block_id, v, ln)
        if target:
            var, forced_kind = target
            kind = forced_kind or _classify_rhs(rhs)
            rhs_text = _clean_text(_text(rhs, src)) if rhs is not None else ""
            builder.add_def(block_id, var, line, kind, rhs_text)
        elif lhs is not None and lhs.type in ("pattern_list", "tuple_pattern", "list_pattern",
                                               "array_pattern", "object_pattern"):
            # 파이썬 튜플 언패킹(`a, b = f()`)/JS 구조분해 대입 — 값 하나하나
            # 정밀 추적 대신 보수적으로 전부 같은 소스로 취급한다(declarator와 동일 원칙).
            rhs_text = _clean_text(_text(rhs, src)) if rhs is not None else ""
            kind = _classify_rhs(rhs)
            for var in _destructured_names(lhs, src):
                builder.add_def(block_id, var, line, kind, rhs_text)
        return

    if inner.type in ("variable_declarator",):
        _handle_declarator(inner, src, builder, block_id, line)
        return

    if inner.type in ("lexical_declaration", "variable_declaration"):
        for c in inner.children:
            if c.type == "variable_declarator":
                _handle_declarator(c, src, builder, block_id, line)
        return

    if inner.type == "augmented_assignment":
        lhs = inner.child_by_field_name("left")
        rhs = inner.child_by_field_name("right")
        target = _def_target_name(lhs, src) if lhs is not None else None
        if target:
            var, forced_kind = target
            builder.add_use(block_id, var, line)  # 복합 대입은 먼저 읽는다
            rhs_text = _clean_text(_text(rhs, src)) if rhs is not None else ""
            builder.add_def(block_id, var, line, forced_kind or DIRECT_ASSIGNMENT, rhs_text)
        uses: list[tuple[str, int]] = []
        _identifier_uses(rhs, src, uses)
        for v, ln in uses:
            builder.add_use(block_id, v, ln)
        return

    if inner.type == "update_expression":
        arg = inner.child_by_field_name("argument")
        if arg is not None and arg.type == "identifier":
            var = _text(arg, src)
            builder.add_use(block_id, var, line)
            builder.add_def(block_id, var, line, DIRECT_ASSIGNMENT)
        return

    # 그 외(단순 호출문, assert 등) — 전부 use 로만 취급.
    uses = []
    _identifier_uses(inner, src, uses)
    for v, ln in uses:
        builder.add_use(block_id, v, ln)


def _destructured_names(pattern: Node, src: bytes) -> list[str]:
    """`{ a, b: renamed, ...rest }` / `[a, , b]` 같은 구조분해 패턴에서

    실제로 바인딩되는 변수 이름을 전부 뽑는다. `const { a } = req.body` 처럼
    바로 구조분해로 받는 경우가 `let x = req.body` 만큼이나 흔해서, 이걸
    못 잡으면 실제 코드에서 source 태깅이 통째로 새 나간다.
    """
    names: list[str] = []
    if pattern.type == "identifier":
        return [_text(pattern, src)]
    # object_pattern/array_pattern: JS `{a}`/`[a]`. pattern_list/tuple_pattern/
    # list_pattern: 파이썬 `a, b = ...` / `(a, b) = ...` / `[a, b] = ...`.
    if pattern.type in ("object_pattern", "array_pattern", "pattern_list", "tuple_pattern", "list_pattern"):
        for c in pattern.children:
            if not c.is_named:
                continue
            if c.type == "shorthand_property_identifier_pattern":
                names.append(_text(c, src))
            elif c.type == "pair_pattern":
                value = c.child_by_field_name("value")
                if value is not None:
                    names.extend(_destructured_names(value, src))
            elif c.type in ("rest_pattern", "rest_element", "list_splat_pattern"):
                inner = next((g for g in c.children if g.type == "identifier"), None)
                if inner is not None:
                    names.append(_text(inner, src))
            elif c.type in ("object_pattern", "array_pattern", "identifier",
                             "pattern_list", "tuple_pattern", "list_pattern"):
                names.extend(_destructured_names(c, src))
            elif c.type == "assignment_pattern":  # `{ a = defaultVal }`
                left = c.child_by_field_name("left")
                if left is not None:
                    names.extend(_destructured_names(left, src))
    return names


def _handle_declarator(decl: Node, src: bytes, builder: CFGBuilder, block_id: int, line: int) -> None:
    name_node = decl.child_by_field_name("name")
    value_node = decl.child_by_field_name("value")
    if name_node is None:
        return
    if value_node is None:
        return  # `let x;` — 아직 정의 없음
    uses: list[tuple[str, int]] = []
    _identifier_uses(value_node, src, uses)
    for v, ln in uses:
        builder.add_use(block_id, v, ln)

    if name_node.type == "identifier":
        var = _text(name_node, src)
        builder.add_def(block_id, var, line, _classify_rhs(value_node), _clean_text(_text(value_node, src)))
        return

    if name_node.type in ("object_pattern", "array_pattern"):
        # 구조분해로 뽑히는 변수 전부를, 값 하나하나를 정밀 추적하는 대신
        # 보수적으로 같은 소스(value_node) 에서 나온 것으로 취급한다 —
        # 안전 쪽으로 과잉 탐지(false positive)하는 게 놓치는 것보다 낫다.
        text = _clean_text(_text(value_node, src))
        kind = _classify_rhs(value_node)
        for var in _destructured_names(name_node, src):
            builder.add_def(block_id, var, line, kind, text)


# --------------------------------------------------------------- 공용 헬퍼
def _named(node: Node) -> list[Node]:
    return [c for c in node.children if c.is_named]


def _find_block_child(node: Node, block_types: set[str]) -> Node | None:
    for c in node.children:
        if c.type in block_types:
            return c
    return None


# ============================================================== Python 워커
_PY_BLOCK = {"block"}
_PY_LOOP_TYPES = {"for_statement", "while_statement"}


def _py_body(builder: CFGBuilder, block_node: Node, src: bytes, entry: int,
             loop_stack: list[tuple[int, int]]) -> int | _Unreachable:
    """statement 시퀀스를 처리해서 정상 흐름이 이어지는 마지막 블록(또는 UNREACHABLE)을 반환."""
    cur = entry
    for stmt in _named(block_node):
        if cur is UNREACHABLE:
            break  # return/break/continue 뒤에 오는 죽은 코드 — 그래프에서 제외
        cur = _py_stmt(builder, stmt, src, cur, loop_stack)
    return cur


def _py_stmt(builder: CFGBuilder, stmt: Node, src: bytes, cur: int,
             loop_stack: list[tuple[int, int]]) -> int | _Unreachable:
    t = stmt.type
    line = stmt.start_point[0] + 1

    if t == "if_statement":
        cond = stmt.child_by_field_name("condition")
        uses: list[tuple[str, int]] = []
        _identifier_uses(cond, src, uses)
        for v, ln in uses:
            builder.add_use(cur, v, ln)

        join = builder.new_block(line)
        branches = [("consequence", "true")]
        alt_nodes = stmt.children_by_field_name("alternative")
        exits: list[int] = []

        cons = stmt.child_by_field_name("consequence")
        b_then = builder.new_block(line)
        builder.edge(cur, b_then, "true")
        r = _py_body(builder, cons, src, b_then, loop_stack)
        if r is not UNREACHABLE:
            exits.append(r)

        if not alt_nodes:
            builder.edge(cur, join, "false")
        else:
            prev_cond_block = cur
            for i, alt in enumerate(alt_nodes):
                is_last = i == len(alt_nodes) - 1
                if alt.type == "elif_clause":
                    econd = alt.child_by_field_name("condition")
                    eu: list[tuple[str, int]] = []
                    _identifier_uses(econd, src, eu)
                    elif_block = builder.new_block(alt.start_point[0] + 1)
                    builder.edge(prev_cond_block, elif_block, "false")
                    for v, ln in eu:
                        builder.add_use(elif_block, v, ln)
                    econs = alt.child_by_field_name("consequence")
                    eb = builder.new_block(alt.start_point[0] + 1)
                    builder.edge(elif_block, eb, "true")
                    r2 = _py_body(builder, econs, src, eb, loop_stack)
                    if r2 is not UNREACHABLE:
                        exits.append(r2)
                    prev_cond_block = elif_block
                    if is_last:
                        builder.edge(elif_block, join, "false")
                elif alt.type == "else_clause":
                    ebody = alt.child_by_field_name("body")
                    eb = builder.new_block(alt.start_point[0] + 1)
                    builder.edge(prev_cond_block, eb, "false")
                    r2 = _py_body(builder, ebody, src, eb, loop_stack)
                    if r2 is not UNREACHABLE:
                        exits.append(r2)

        for e in exits:
            builder.edge(e, join, "seq")
        if not exits:
            return UNREACHABLE
        return join

    if t in _PY_LOOP_TYPES:
        header = builder.new_block(line)
        builder.edge(cur, header, "seq")
        if t == "for_statement":
            left = stmt.child_by_field_name("left")
            right = stmt.child_by_field_name("right")
            if left is not None and left.type == "identifier":
                builder.add_def(header, _text(left, src), line, DIRECT_ASSIGNMENT)
            ru: list[tuple[str, int]] = []
            _identifier_uses(right, src, ru)
            for v, ln in ru:
                builder.add_use(header, v, ln)
        else:
            cond = stmt.child_by_field_name("condition")
            cu: list[tuple[str, int]] = []
            _identifier_uses(cond, src, cu)
            for v, ln in cu:
                builder.add_use(header, v, ln)

        after = builder.new_block(line)
        body_entry = builder.new_block(line)
        builder.edge(header, body_entry, "true")
        builder.edge(header, after, "false")
        loop_stack.append((header, after))
        body = stmt.child_by_field_name("body")
        r = _py_body(builder, body, src, body_entry, loop_stack)
        loop_stack.pop()
        if r is not UNREACHABLE:
            builder.edge(r, header, "loop_back")
        return after

    if t == "try_statement":
        try_body = stmt.child_by_field_name("body")
        join = builder.new_block(line)
        try_entry = builder.new_block(line)
        builder.edge(cur, try_entry, "seq")
        r = _py_body(builder, try_body, src, try_entry, loop_stack)
        exits: list[int] = [r] if r is not UNREACHABLE else []

        for c in stmt.children:
            if c.type == "except_clause":
                handler_body = _find_block_child(c, _PY_BLOCK)
                h = builder.new_block(c.start_point[0] + 1)
                builder.edge(try_entry, h, "exception")
                if r is not UNREACHABLE:
                    builder.edge(r, h, "exception")
                if handler_body is not None:
                    hr = _py_body(builder, handler_body, src, h, loop_stack)
                    if hr is not UNREACHABLE:
                        exits.append(hr)
            elif c.type == "finally_clause":
                fbody = _find_block_child(c, _PY_BLOCK)
                if fbody is not None:
                    fin = builder.new_block(c.start_point[0] + 1)
                    for e in exits:
                        builder.edge(e, fin, "seq")
                    exits = []
                    fr = _py_body(builder, fbody, src, fin, loop_stack)
                    if fr is not UNREACHABLE:
                        exits.append(fr)

        for e in exits:
            builder.edge(e, join, "seq")
        if not exits:
            return UNREACHABLE
        return join

    if t == "break_statement":
        if loop_stack:
            builder.edge(cur, loop_stack[-1][1], "break")
        return UNREACHABLE

    if t == "continue_statement":
        if loop_stack:
            builder.edge(cur, loop_stack[-1][0], "continue")
        return UNREACHABLE

    if t == "return_statement":
        uses: list[tuple[str, int]] = []
        for c in _named(stmt):
            _identifier_uses(c, src, uses)
        for v, ln in uses:
            builder.add_use(cur, v, ln)
        builder.exit_blocks.append(cur)
        return UNREACHABLE

    _extract_defuse(stmt, src, builder, cur)
    return cur


# ================================================================ JS 워커
_JS_BLOCK = {"statement_block"}


def _js_body(builder: CFGBuilder, block_node: Node, src: bytes, entry: int,
             loop_stack: list[tuple[int, int]]) -> int | _Unreachable:
    cur = entry
    for stmt in _named(block_node):
        if cur is UNREACHABLE:
            break
        cur = _js_stmt(builder, stmt, src, cur, loop_stack)
    return cur


def _js_stmt(builder: CFGBuilder, stmt: Node, src: bytes, cur: int,
             loop_stack: list[tuple[int, int]]) -> int | _Unreachable:
    t = stmt.type
    line = stmt.start_point[0] + 1

    if t == "if_statement":
        return _js_if(builder, stmt, src, cur, loop_stack)

    if t in ("for_statement", "for_in_statement"):
        header = builder.new_block(line)
        builder.edge(cur, header, "seq")
        if t == "for_statement":
            init = stmt.child_by_field_name("initializer")
            if init is not None:
                _extract_defuse(init, src, builder, header) if init.type not in (
                    "lexical_declaration", "variable_declaration") else [
                    _handle_declarator(c, src, builder, header, line) for c in init.children
                    if c.type == "variable_declarator"]
            cond = stmt.child_by_field_name("condition")
            cu: list[tuple[str, int]] = []
            _identifier_uses(cond, src, cu)
            for v, ln in cu:
                builder.add_use(header, v, ln)
        else:
            left = stmt.child_by_field_name("left")
            right = stmt.child_by_field_name("right")
            if left is not None and left.type == "identifier":
                builder.add_def(header, _text(left, src), line, DIRECT_ASSIGNMENT)
            ru: list[tuple[str, int]] = []
            _identifier_uses(right, src, ru)
            for v, ln in ru:
                builder.add_use(header, v, ln)

        after = builder.new_block(line)
        body_entry = builder.new_block(line)
        builder.edge(header, body_entry, "true")
        builder.edge(header, after, "false")
        loop_stack.append((header, after))
        body = stmt.child_by_field_name("body")
        r = _js_body(builder, body, src, body_entry, loop_stack) if body.type in _JS_BLOCK \
            else _js_stmt(builder, body, src, body_entry, loop_stack)
        loop_stack.pop()
        if r is not UNREACHABLE:
            if t == "for_statement":
                inc = stmt.child_by_field_name("increment")
                if inc is not None:
                    _extract_defuse(inc, src, builder, r)
            builder.edge(r, header, "loop_back")
        return after

    if t == "while_statement":
        header = builder.new_block(line)
        builder.edge(cur, header, "seq")
        cond = stmt.child_by_field_name("condition")
        cu = []
        _identifier_uses(cond, src, cu)
        for v, ln in cu:
            builder.add_use(header, v, ln)
        after = builder.new_block(line)
        body_entry = builder.new_block(line)
        builder.edge(header, body_entry, "true")
        builder.edge(header, after, "false")
        loop_stack.append((header, after))
        body = stmt.child_by_field_name("body")
        r = _js_body(builder, body, src, body_entry, loop_stack) if body.type in _JS_BLOCK \
            else _js_stmt(builder, body, src, body_entry, loop_stack)
        loop_stack.pop()
        if r is not UNREACHABLE:
            builder.edge(r, header, "loop_back")
        return after

    if t == "try_statement":
        join = builder.new_block(line)
        try_entry = builder.new_block(line)
        builder.edge(cur, try_entry, "seq")
        body = stmt.child_by_field_name("body")
        r = _js_body(builder, body, src, try_entry, loop_stack)
        exits: list[int] = [r] if r is not UNREACHABLE else []

        handler = stmt.child_by_field_name("handler")
        if handler is not None:
            hbody = handler.child_by_field_name("body")
            h = builder.new_block(handler.start_point[0] + 1)
            builder.edge(try_entry, h, "exception")
            if r is not UNREACHABLE:
                builder.edge(r, h, "exception")
            hr = _js_body(builder, hbody, src, h, loop_stack)
            if hr is not UNREACHABLE:
                exits.append(hr)

        finalizer = stmt.child_by_field_name("finalizer")
        if finalizer is not None:
            fbody = finalizer.child_by_field_name("body")
            fin = builder.new_block(finalizer.start_point[0] + 1)
            for e in exits:
                builder.edge(e, fin, "seq")
            exits = []
            fr = _js_body(builder, fbody, src, fin, loop_stack)
            if fr is not UNREACHABLE:
                exits.append(fr)

        for e in exits:
            builder.edge(e, join, "seq")
        if not exits:
            return UNREACHABLE
        return join

    if t == "switch_statement":
        return _js_switch(builder, stmt, src, cur, loop_stack)

    if t == "break_statement":
        if loop_stack:
            builder.edge(cur, loop_stack[-1][1], "break")
        return UNREACHABLE

    if t == "continue_statement":
        if loop_stack:
            builder.edge(cur, loop_stack[-1][0], "continue")
        return UNREACHABLE

    if t == "return_statement":
        uses: list[tuple[str, int]] = []
        for c in _named(stmt):
            _identifier_uses(c, src, uses)
        for v, ln in uses:
            builder.add_use(cur, v, ln)
        builder.exit_blocks.append(cur)
        return UNREACHABLE

    _extract_defuse(stmt, src, builder, cur)
    return cur


def _js_if(builder: CFGBuilder, stmt: Node, src: bytes, cur: int,
           loop_stack: list[tuple[int, int]]) -> int | _Unreachable:
    line = stmt.start_point[0] + 1
    cond = stmt.child_by_field_name("condition")
    cu: list[tuple[str, int]] = []
    _identifier_uses(cond, src, cu)
    for v, ln in cu:
        builder.add_use(cur, v, ln)

    exits: list[int] = []
    cons = stmt.child_by_field_name("consequence")
    b_then = builder.new_block(line)
    builder.edge(cur, b_then, "true")
    r = _js_body(builder, cons, src, b_then, loop_stack)
    if r is not UNREACHABLE:
        exits.append(r)

    alt = stmt.child_by_field_name("alternative")
    join = builder.new_block(line)
    if alt is None:
        builder.edge(cur, join, "false")
    else:
        alt_body = next((c for c in alt.children if c.is_named), None)
        b_else = builder.new_block(alt.start_point[0] + 1)
        builder.edge(cur, b_else, "false")
        if alt_body is not None and alt_body.type == "if_statement":
            r2 = _js_if(builder, alt_body, src, b_else, loop_stack)
        elif alt_body is not None:
            r2 = _js_body(builder, alt_body, src, b_else, loop_stack)
        else:
            r2 = b_else
        if r2 is not UNREACHABLE:
            exits.append(r2)

    for e in exits:
        builder.edge(e, join, "seq")
    if not exits:
        return UNREACHABLE
    return join


def _js_switch(builder: CFGBuilder, stmt: Node, src: bytes, cur: int,
               loop_stack: list[tuple[int, int]]) -> int | _Unreachable:
    line = stmt.start_point[0] + 1
    value = stmt.child_by_field_name("value")
    vu: list[tuple[str, int]] = []
    _identifier_uses(value, src, vu)
    for v, ln in vu:
        builder.add_use(cur, v, ln)

    after = builder.new_block(line)
    body = stmt.child_by_field_name("body")
    if body is None:
        builder.edge(cur, after, "seq")
        return after

    loop_stack.append((after, after))  # switch 안 continue 는 드묾 — break 타깃만 실질적으로 씀
    exits: list[int] = []
    prev_fallthrough: int | None = None
    for case in _named(body):
        if case.type not in ("switch_case", "switch_default"):
            continue
        case_val = case.child_by_field_name("value")
        stmts = [c for c in case.children if c.is_named and c is not case_val]
        cblock = builder.new_block(case.start_point[0] + 1)
        builder.edge(cur, cblock, "case")
        if prev_fallthrough is not None:
            builder.edge(prev_fallthrough, cblock, "fallthrough")

        c_exit: int | _Unreachable = cblock
        for s in stmts:
            if c_exit is UNREACHABLE:
                break
            c_exit = _js_stmt(builder, s, src, c_exit, loop_stack)
        if c_exit is UNREACHABLE:
            prev_fallthrough = None
        else:
            exits.append(c_exit)
            prev_fallthrough = c_exit
    loop_stack.pop()

    if prev_fallthrough is not None:
        exits.append(prev_fallthrough) if prev_fallthrough not in exits else None
    for e in exits:
        builder.edge(e, after, "seq")
    builder.edge(cur, after, "no_match")  # default 없이 아무 case 도 안 맞을 수 있음
    return after


# --------------------------------------------------------------- reaching-definitions
def _reaching_definitions(builder: CFGBuilder) -> dict[int, list[tuple[str, int, str, int, str]]]:
    """블록별 IN 집합(그 블록 시작 시점에 도달 가능한 정의들)을 고정점 반복으로 계산.

    정의는 (var, def_line, evidence_kind, def_block, def_text) 로 식별한다 —
    같은 변수를 여러 줄에서 정의할 수 있으므로 변수명만으로는 안 된다.
    """
    preds: dict[int, list[int]] = {b: [] for b in builder.blocks}
    for s, d, _ in builder.edges:
        preds.setdefault(d, []).append(s)

    # GEN: 블록 안에서 "마지막으로" 정의된 (var -> (line, kind, text))
    gen: dict[int, dict[str, tuple[int, str, str]]] = {}
    for bid, evs in builder.events.items():
        last: dict[str, tuple[int, str, str]] = {}
        for ev in evs:
            if ev[0] == "def":
                _, var, line, kind, text = ev
                last[var] = (line, kind, text)
        gen[bid] = last

    IN: dict[int, set[tuple[str, int, str, int, str]]] = {b: set() for b in builder.blocks}
    OUT: dict[int, set[tuple[str, int, str, int, str]]] = {b: set() for b in builder.blocks}

    changed = True
    while changed:
        changed = False
        for bid in builder.blocks:
            new_in: set[tuple[str, int, str, int, str]] = set()
            for p in preds.get(bid, []):
                new_in |= OUT[p]
            if new_in != IN[bid]:
                IN[bid] = new_in
                changed = True

            killed_vars = set(gen[bid].keys())
            new_out = {d for d in IN[bid] if d[0] not in killed_vars}
            new_out |= {(var, line, kind, bid, text) for var, (line, kind, text) in gen[bid].items()}
            if new_out != OUT[bid]:
                OUT[bid] = new_out
                changed = True

    return IN


# --------------------------------------------------------------- post-dominator
def _post_dominators(builder: CFGBuilder) -> dict[int, set[int]]:
    """가상 종료 노드로 모든 exit_block 을 모아 역방향 CFG의 지배자 집합을 계산."""
    all_blocks = set(builder.blocks.keys())
    EXIT = -1
    succ: dict[int, list[int]] = {b: [] for b in all_blocks}
    for s, d, _ in builder.edges:
        succ.setdefault(s, []).append(d)
    for b in builder.exit_blocks:
        succ.setdefault(b, []).append(EXIT)
    # 도달 불가한 return 없는 함수 대비: 후행자 없는 블록도 EXIT 로 흘려보낸다.
    for b in all_blocks:
        if not succ.get(b):
            succ[b] = [EXIT]

    pred: dict[int, list[int]] = {EXIT: []}
    for s, dsts in succ.items():
        for d in dsts:
            pred.setdefault(d, []).append(s)

    nodes = all_blocks | {EXIT}
    pdom: dict[int, set[int]] = {n: set(nodes) for n in nodes}
    pdom[EXIT] = {EXIT}

    changed = True
    while changed:
        changed = False
        for n in nodes:
            if n == EXIT:
                continue
            succs = succ.get(n, [])
            if not succs:
                continue
            new_pdom = set(nodes)
            for s in succs:
                new_pdom &= pdom[s]
            new_pdom.add(n)
            if new_pdom != pdom[n]:
                pdom[n] = new_pdom
                changed = True

    return pdom


def _immediate_pdom(pdom: dict[int, set[int]], n: int) -> int | None:
    candidates = pdom[n] - {n}
    if not candidates:
        return None
    # strict pdom 집합 중, 다른 모든 strict pdom 을 pdom 하는 것(가장 가까운 것)을 고른다.
    for c in candidates:
        if all(c in pdom[other] for other in candidates if other != c) or len(candidates) == 1:
            return c
    return None


def _control_dependence(builder: CFGBuilder) -> list[tuple[int, int, str]]:
    """(branch_block, dependent_block, branch_label) 목록."""
    if not builder.blocks:
        return []
    pdom = _post_dominators(builder)
    result: list[tuple[int, int, str]] = []

    for src, dst, label in builder.edges:
        if label in ("seq",):
            continue  # 순차 흐름은 분기가 아니다
        ipdom_src = _immediate_pdom(pdom, src)
        # dst 에서 ipdom(src) 직전까지 post-dominator tree 를 거슬러 올라가며 전부 의존 표시.
        node = dst
        visited = set()
        while node is not None and node != ipdom_src and node not in visited:
            visited.add(node)
            if node in builder.blocks:
                result.append((src, node, label))
            node = _immediate_pdom(pdom, node) if node in pdom else None
            if node == src and src not in visited:
                # 루프 백엣지처럼 자기 자신으로 돌아오는 경우 무한루프 방지.
                break
    return result


# --------------------------------------------------------------- 파라미터 이름
def _py_param_names(params_node: Node | None, src: bytes) -> list[str]:
    if params_node is None:
        return []
    names: list[str] = []
    for child in params_node.children:
        if not child.is_named:
            continue
        if child.type == "identifier":
            names.append(_text(child, src))
        elif child.type in ("typed_parameter", "default_parameter", "typed_default_parameter",
                             "list_splat_pattern", "dictionary_splat_pattern"):
            inner = next((c for c in child.children if c.type == "identifier"), None)
            names.append(_text(inner, src) if inner else "?")
        else:
            names.append("?")
    if names and names[0] in ("self", "cls"):
        # 호출 지점(`obj.method(x)`)의 인자 목록엔 self가 안 실리므로, 위치
        # 매칭을 위해 파라미터 쪽에서도 제외해야 인덱스가 맞는다.
        names = names[1:]
    return names


def _js_param_names(params_node: Node | None, src: bytes) -> list[str]:
    if params_node is None:
        return []
    names: list[str] = []
    for child in params_node.children:
        if not child.is_named:
            continue
        if child.type == "identifier":
            names.append(_text(child, src))
        elif child.type in ("assignment_pattern", "required_parameter", "optional_parameter"):
            left = child.child_by_field_name("left") or child.child_by_field_name("pattern")
            names.append(_text(left, src) if left is not None and left.type == "identifier" else "?")
        elif child.type == "rest_pattern":
            inner = next((c for c in child.children if c.type == "identifier"), None)
            names.append(_text(inner, src) if inner else "?")
        else:
            names.append("?")  # 구조분해 등 복잡한 패턴 — v1은 위치만 비워둔다
    return names


# --------------------------------------------------------------- 진입점
def build_function_cfgs(lang: str, suffix: str, path: str, source: bytes,
                         index: ScanIndex) -> dict[str, CFGBuilder]:
    """이 파일의 모든 함수/메서드에 대해 symbol_key -> CFGBuilder 를 만든다."""
    if lang not in ("python", "javascript", "typescript"):
        return {}

    language = _LANGUAGES[suffix]
    root = Parser(language).parse(source).root_node
    results: dict[str, CFGBuilder] = {}

    def py_param_fn(params, s):
        return _python_param_types(params, s)

    def js_param_fn(params, s):
        return _js_param_types(params) if lang == "javascript" else _ts_param_types(params, s)

    def walk(node: Node, scope: list[str]):
        for child in node.children:
            t = child.type
            if t in ("class_definition", "class_declaration"):
                name_node = child.child_by_field_name("name")
                name = _text(name_node, source) if name_node else None
                new_scope = scope + [name] if name else scope
                body = child.child_by_field_name("body")
                if body is not None:
                    walk(body, new_scope)
            elif t == "function_definition" and lang == "python":
                _emit(child, scope, py_param_fn, _py_body)
            elif t in ("method_definition", "function_declaration") and lang != "python":
                _emit(child, scope, js_param_fn, _js_body)
            elif t == "variable_declarator" and lang != "python":
                value = child.child_by_field_name("value")
                name_node = child.child_by_field_name("name")
                if (value is not None and value.type in ("arrow_function", "function_expression")
                        and name_node is not None and name_node.type == "identifier"):
                    _emit_value_fn(child, value, scope, js_param_fn, _js_body)
                else:
                    walk(child, scope)
            else:
                walk(child, scope)

    def _seed_params(b: CFGBuilder, entry: int, entry_line: int, params_node: Node) -> None:
        names = _py_param_names(params_node, source) if lang == "python" else _js_param_names(params_node, source)
        b.params = names
        for n in names:
            if n != "?":
                b.add_def(entry, n, entry_line, PARAMETER, "")

    def _emit(fn_node: Node, scope: list[str], param_fn, body_walker):
        name_node = fn_node.child_by_field_name("name")
        name = _text(name_node, source) if name_node else None
        if name is None:
            return
        qname = ".".join(scope + [name])
        params_node = fn_node.child_by_field_name("parameters")
        params = tuple(param_fn(params_node, source))
        symbol_key = index.symbol_key_exact.get((path, qname, params))
        body = fn_node.child_by_field_name("body")
        if symbol_key is None or body is None:
            walk(fn_node, scope)
            return
        b = CFGBuilder()
        entry_line = fn_node.start_point[0] + 1
        entry = b.new_block(entry_line)
        _seed_params(b, entry, entry_line, params_node)
        r = body_walker(b, body, source, entry, [])
        if r is not UNREACHABLE:
            b.exit_blocks.append(r)
        results[symbol_key] = b
        # 중첩 함수(클로저)도 각자 독립된 CFG 로 뽑는다.
        walk(body, scope)

    def _emit_value_fn(decl_node: Node, value: Node, scope: list[str], param_fn, body_walker):
        name_node = decl_node.child_by_field_name("name")
        name = _text(name_node, source)
        qname = ".".join(scope + [name])
        params_node = value.child_by_field_name("parameters")
        params = tuple(param_fn(params_node, source))
        symbol_key = index.symbol_key_exact.get((path, qname, params))
        body = value.child_by_field_name("body")
        if symbol_key is None or body is None or body.type not in _JS_BLOCK:
            return
        b = CFGBuilder()
        entry_line = value.start_point[0] + 1
        entry = b.new_block(entry_line)
        _seed_params(b, entry, entry_line, params_node)
        r = body_walker(b, body, source, entry, [])
        if r is not UNREACHABLE:
            b.exit_blocks.append(r)
        results[symbol_key] = b
        walk(body, scope)

    walk(root, [])
    return results


def analyze(builder: CFGBuilder) -> tuple[list[tuple[str, int, str, int, int, str]], list[tuple[int, int, str]]]:
    """(data_edges, control_edges) 를 계산해서 반환.

    data_edges: (var, def_line, evidence_kind, def_block, use_line, def_text).

    블록 안에서는 등장 순서를 지켜 처리한다 — 어떤 use보다 "먼저" 같은 블록
    안에서 그 변수가 다시 정의됐다면, 그 지역 정의가 블록 밖에서 들어오는
    reaching definition을 가린다(그 지점에서는 지역 정의만 도달 가능하다).
    아직 블록 안에서 정의된 적 없는 use만 IN[block](외부에서 들어오는 정의)과 매칭한다.
    """
    reaching = _reaching_definitions(builder)  # IN[block]: set of (var, def_line, kind, def_block, def_text)
    data_edges: list[tuple[str, int, str, int, int, str]] = []

    for block_id, evs in builder.events.items():
        local: dict[str, tuple[int, str, str]] = {}
        external_by_var: dict[str, list[tuple[int, str, int, str]]] = {}
        for var, def_line, kind, def_block, def_text in reaching[block_id]:
            external_by_var.setdefault(var, []).append((def_line, kind, def_block, def_text))

        for ev in evs:
            if ev[0] == "def":
                _, var, line, kind, text = ev
                local[var] = (line, kind, text)
                continue
            _, var, use_line = ev
            if var in local:
                def_line, kind, text = local[var]
                data_edges.append((var, def_line, kind, block_id, use_line, text))
            else:
                for def_line, kind, def_block, text in external_by_var.get(var, []):
                    data_edges.append((var, def_line, kind, def_block, use_line, text))

    control_edges = _control_dependence(builder)
    return data_edges, control_edges
