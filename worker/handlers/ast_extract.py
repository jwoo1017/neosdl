"""tree-sitter 기반 AST 파싱 — WBS 1.2.

파일 하나(소스 바이트)를 받아 module/class/interface/function/method/field
심볼 목록을 뽑아낸다. 지원 언어: java, python, javascript, typescript(+tsx).

클래스 멤버 변수(필드)까지만 심볼화한다. 함수/메서드 본문 안의 지역변수는
다루지 않는다 — 그래서 이 모듈은 method_declaration/function_definition 등
함수·메서드 본문 내부로는 재귀하지 않는다 (본문 안에서 선언되는 것들은
전부 지역 스코프이기 때문).
"""

from __future__ import annotations

import hashlib

from tree_sitter import Language, Node, Parser

import tree_sitter_java as ts_java
import tree_sitter_javascript as ts_javascript
import tree_sitter_python as ts_python
import tree_sitter_typescript as ts_typescript

_LANGUAGES: dict[str, Language] = {
    ".java": Language(ts_java.language()),
    ".py": Language(ts_python.language()),
    ".js": Language(ts_javascript.language()),
    ".jsx": Language(ts_javascript.language()),
    ".ts": Language(ts_typescript.language_typescript()),
    ".tsx": Language(ts_typescript.language_tsx()),
}


def _text(node: Node | None, src: bytes) -> str:
    if node is None:
        return ""
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _symbol(kind: str, qname: str, param_types: list[str], node: Node, src: bytes) -> dict:
    body_hash = hashlib.sha256(src[node.start_byte:node.end_byte]).hexdigest()
    signature = f"{qname}({', '.join(param_types)})" if param_types else qname
    return {
        "kind": kind,
        "qualified_name": qname,
        "param_types": param_types,
        "start_line": node.start_point[0] + 1,
        "end_line": node.end_point[0] + 1,
        "body_hash": body_hash,
        "signature_text": signature,
    }


def _module_name(path: str) -> str:
    stem = path.rsplit(".", 1)[0]
    return stem.replace("\\", ".").replace("/", ".")


# --------------------------------------------------------------------- java
def _java_param_types(params: Node | None, src: bytes) -> list[str]:
    if params is None:
        return []
    out = []
    for child in params.children:
        if not child.is_named:
            continue
        type_node = child.child_by_field_name("type")
        out.append(_text(type_node, src) if type_node else "any")
    return out


def _walk_java(node: Node, src: bytes, scope: list[str], out: list[dict]) -> None:
    for child in node.children:
        t = child.type
        if t in ("class_declaration", "interface_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            kind = "class" if t == "class_declaration" else "interface"
            out.append(_symbol(kind, qname, [], child, src))
            body = child.child_by_field_name("body")
            if body is not None:
                _walk_java(body, src, scope + [name], out)
        elif t == "method_declaration":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            params = _java_param_types(child.child_by_field_name("parameters"), src)
            out.append(_symbol("method", qname, params, child, src))
        elif t == "field_declaration":
            for decl in child.children_by_field_name("declarator"):
                name_node = decl.child_by_field_name("name")
                if name_node is None:
                    continue
                name = _text(name_node, src)
                out.append(_symbol("field", ".".join(scope + [name]), [], child, src))
        else:
            _walk_java(child, src, scope, out)


# ------------------------------------------------------------------- python
def _python_param_types(params: Node | None, src: bytes) -> list[str]:
    if params is None:
        return []
    out = []
    for child in params.children:
        if not child.is_named:
            continue
        if child.type == "typed_parameter":
            type_node = child.child_by_field_name("type")
            out.append(_text(type_node, src) if type_node else "any")
        elif child.type in ("default_parameter", "typed_default_parameter"):
            name_node = child.child_by_field_name("name")
            if name_node is not None and name_node.type == "typed_parameter":
                type_node = name_node.child_by_field_name("type")
                out.append(_text(type_node, src) if type_node else "any")
            else:
                out.append("any")
        else:
            out.append("any")
    return out


def _walk_python(node: Node, src: bytes, scope: list[str], out: list[dict]) -> None:
    for child in node.children:
        t = child.type
        if t == "class_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            out.append(_symbol("class", qname, [], child, src))
            body = child.child_by_field_name("body")
            if body is not None:
                _walk_python(body, src, scope + [name], out)
        elif t == "function_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            kind = "method" if scope else "function"
            params = _python_param_types(child.child_by_field_name("parameters"), src)
            out.append(_symbol(kind, qname, params, child, src))
        elif t == "assignment" and scope:
            # 함수/메서드 본문으로는 재귀하지 않으므로, 여기 걸리는 assignment는
            # 클래스 본문에 직접 있는(=클래스 변수) 것만 남는다.
            left = child.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                name = _text(left, src)
                out.append(_symbol("field", ".".join(scope + [name]), [], child, src))
        else:
            _walk_python(child, src, scope, out)


# --------------------------------------------------------------- javascript
def _js_param_types(params: Node | None) -> list[str]:
    if params is None:
        return []
    return ["any" for child in params.children if child.is_named]


def _walk_javascript(node: Node, src: bytes, scope: list[str], out: list[dict]) -> None:
    for child in node.children:
        t = child.type
        if t == "class_declaration":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            out.append(_symbol("class", qname, [], child, src))
            body = child.child_by_field_name("body")
            if body is not None:
                _walk_javascript(body, src, scope + [name], out)
        elif t == "method_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            params = _js_param_types(child.child_by_field_name("parameters"))
            out.append(_symbol("method", qname, params, child, src))
        elif t == "field_definition":
            name_node = child.child_by_field_name("property")
            if name_node is None:
                continue
            name = _text(name_node, src)
            out.append(_symbol("field", ".".join(scope + [name]), [], child, src))
        elif t == "function_declaration":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            params = _js_param_types(child.child_by_field_name("parameters"))
            out.append(_symbol("function", qname, params, child, src))
        elif t == "variable_declarator":
            value = child.child_by_field_name("value")
            name_node = child.child_by_field_name("name")
            if (value is not None and value.type in ("arrow_function", "function_expression")
                    and name_node is not None and name_node.type == "identifier"):
                name = _text(name_node, src)
                qname = ".".join(scope + [name])
                params = _js_param_types(value.child_by_field_name("parameters"))
                out.append(_symbol("function", qname, params, value, src))
        else:
            _walk_javascript(child, src, scope, out)


# --------------------------------------------------------------- typescript
def _ts_param_types(params: Node | None, src: bytes) -> list[str]:
    if params is None:
        return []
    out = []
    for child in params.children:
        if not child.is_named:
            continue
        type_node = child.child_by_field_name("type")
        if type_node is not None:
            out.append(_text(type_node, src).lstrip(":").strip())
        else:
            out.append("any")
    return out


def _walk_typescript(node: Node, src: bytes, scope: list[str], out: list[dict]) -> None:
    for child in node.children:
        t = child.type
        if t in ("class_declaration", "interface_declaration"):
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            kind = "class" if t == "class_declaration" else "interface"
            out.append(_symbol(kind, qname, [], child, src))
            body = child.child_by_field_name("body")
            if body is not None:
                _walk_typescript(body, src, scope + [name], out)
        elif t == "method_definition":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            params = _ts_param_types(child.child_by_field_name("parameters"), src)
            out.append(_symbol("method", qname, params, child, src))
        elif t in ("field_definition", "public_field_definition"):
            name_node = (child.child_by_field_name("name")
                         or child.child_by_field_name("property"))
            if name_node is None:
                continue
            name = _text(name_node, src)
            out.append(_symbol("field", ".".join(scope + [name]), [], child, src))
        elif t == "function_declaration":
            name_node = child.child_by_field_name("name")
            if name_node is None:
                continue
            name = _text(name_node, src)
            qname = ".".join(scope + [name])
            params = _ts_param_types(child.child_by_field_name("parameters"), src)
            out.append(_symbol("function", qname, params, child, src))
        elif t == "variable_declarator":
            value = child.child_by_field_name("value")
            name_node = child.child_by_field_name("name")
            if (value is not None and value.type in ("arrow_function", "function_expression")
                    and name_node is not None and name_node.type == "identifier"):
                name = _text(name_node, src)
                qname = ".".join(scope + [name])
                params = _ts_param_types(value.child_by_field_name("parameters"), src)
                out.append(_symbol("function", qname, params, value, src))
        else:
            _walk_typescript(child, src, scope, out)


_WALKERS = {
    "java": _walk_java,
    "python": _walk_python,
    "javascript": _walk_javascript,
    "typescript": _walk_typescript,
}


def extract_symbols(lang: str, suffix: str, path: str, source: bytes) -> list[dict]:
    """`path`의 소스에서 심볼 목록을 뽑는다. 첫 항목은 항상 module 심볼이다."""
    language = _LANGUAGES[suffix]
    tree = Parser(language).parse(source)

    out: list[dict] = [_symbol("module", _module_name(path), [], tree.root_node, source)]
    _WALKERS[lang](tree.root_node, source, [], out)
    return out
