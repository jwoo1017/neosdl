"""tree-sitter 기반 호출/의존 관계 추출 — WBS 1.3.

ast_extract.py 는 함수/메서드 본문 안으로 재귀하지 않는다(지역변수를 field로
잘못 잡지 않기 위해). 호출식은 본문 안에 있으므로 이 모듈이 별도로 담당한다.

같은 파일 내 호출은 물론, import 문을 추적해 크로스파일 호출까지 해석한다.
해석 못 하면 버리지 않고 resolved=false + dst_hint 로 보존한다 — symbol_edge
스키마의 설계 원칙 그대로다.
"""

from __future__ import annotations

import posixpath
from collections import defaultdict
from dataclasses import dataclass, field

from tree_sitter import Node, Parser

from worker.handlers.ast_extract import (
    _LANGUAGES,
    _js_param_types,
    _java_param_types,
    _python_param_types,
    _text,
    _ts_param_types,
)

_JS_SUFFIXES = [".ts", ".tsx", ".js", ".jsx"]


# --------------------------------------------------------------------- index
@dataclass
class AliasTarget:
    kind: str  # module | class | symbol | unknown_in_module | external
    path: str | None = None
    qname: str | None = None
    symbol_key: str | None = None
    raw: str | None = None


@dataclass
class ScanIndex:
    files_present: set[str] = field(default_factory=set)
    symbol_key_exact: dict[tuple[str, str, tuple[str, ...]], str] = field(default_factory=dict)
    symbol_key_by_name: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    top_level_by_path: dict[str, dict[str, str]] = field(default_factory=dict)
    class_methods: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)
    module_key_by_path: dict[str, str] = field(default_factory=dict)
    module_key_by_modname: dict[str, str] = field(default_factory=dict)
    module_path_by_modname: dict[str, str] = field(default_factory=dict)
    java_fqcn_index: dict[str, tuple[str, str]] = field(default_factory=dict)


def _java_package_of(source: bytes) -> str:
    tree = Parser(_LANGUAGES[".java"]).parse(source)
    for child in tree.root_node.children:
        if child.type == "package_declaration":
            for c in child.children:
                if c.type in ("scoped_identifier", "identifier"):
                    return _text(c, source)
    return ""


def build_scan_index(cur, scan_id: str, sources: dict[str, bytes]) -> ScanIndex:
    cur.execute(
        """
        SELECT path, lang, kind::text AS kind, qualified_name, param_types, symbol_key
          FROM symbol WHERE scan_id=%s ORDER BY start_line
        """,
        (scan_id,),
    )
    rows = cur.fetchall()

    idx = ScanIndex(files_present=set(sources.keys()))
    symbol_key_by_name: dict[tuple[str, str], list[str]] = defaultdict(list)
    top_level_by_path: dict[str, dict[str, str]] = defaultdict(dict)
    class_methods: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)

    for r in rows:
        path = r["path"]
        kind = r["kind"]
        qname = r["qualified_name"]
        param_types = tuple(r["param_types"] or [])
        symbol_key = r["symbol_key"]

        idx.symbol_key_exact[(path, qname, param_types)] = symbol_key
        simple_name = qname.rsplit(".", 1)[-1]
        symbol_key_by_name[(path, simple_name)].append(symbol_key)

        if kind == "module":
            idx.module_key_by_path[path] = symbol_key
            idx.module_key_by_modname[qname] = symbol_key
            idx.module_path_by_modname[qname] = path
        elif kind in ("class", "interface", "function") and "." not in qname:
            top_level_by_path[path][qname] = symbol_key
        elif kind == "method" and "." in qname:
            class_qname, method_name = qname.rsplit(".", 1)
            class_methods[(path, class_qname)][method_name] = symbol_key

    idx.symbol_key_by_name = dict(symbol_key_by_name)
    idx.top_level_by_path = dict(top_level_by_path)
    idx.class_methods = dict(class_methods)

    for path, source in sources.items():
        if not path.endswith(".java"):
            continue
        pkg = _java_package_of(source)
        for qname, symbol_key in idx.top_level_by_path.get(path, {}).items():
            fqcn = f"{pkg}.{qname}" if pkg else qname
            idx.java_fqcn_index[fqcn] = (path, qname)

    return idx


# ------------------------------------------------------------------ helpers
def _edge(src_key, dst_key, dst_hint, edge_type, resolved, confidence, call_line, evidence) -> dict:
    return {
        "src_symbol_key": src_key,
        "dst_symbol_key": dst_key,
        "dst_hint": dst_hint,
        "edge_type": edge_type,
        "resolved": resolved,
        "confidence": confidence,
        "call_line": call_line,
        "evidence": evidence,
    }


def _clean_text(s: str, limit: int = 80) -> str:
    """여러 줄에 걸친 표현식(JSX 콜백 등)이 그대로 dst_hint 라벨이 되지 않도록 정리."""
    return " ".join(s.split())[:limit]


def _find_all(node: Node, types: set[str]):
    if node.type in types:
        yield node
    for c in node.children:
        yield from _find_all(c, types)


def _flatten_chain(node: Node | None, src: bytes) -> list[str] | None:
    if node is None:
        return []
    if node.type == "this":
        return ["this"]
    if node.type == "identifier":
        return [_text(node, src)]
    if node.type == "attribute":
        obj, attr = node.child_by_field_name("object"), node.child_by_field_name("attribute")
    elif node.type == "member_expression":
        obj, attr = node.child_by_field_name("object"), node.child_by_field_name("property")
    elif node.type == "field_access":
        obj, attr = node.child_by_field_name("object"), node.child_by_field_name("field")
    else:
        return None
    base = _flatten_chain(obj, src) if obj is not None else []
    if base is None or attr is None:
        return None
    return base + [_text(attr, src)]


# --------------------------------------------------------------- resolution
def _resolve_call(parts, lang, path, class_qname, alias_map, index: ScanIndex, wildcard_pkgs):
    if len(parts) == 1:
        name = parts[0]
        if lang == "java" and class_qname:
            dst = index.class_methods.get((path, class_qname), {}).get(name)
            if dst:
                return dst, True, 0.95, "implicit_this"
        dst_list = index.symbol_key_by_name.get((path, name))
        if dst_list:
            conf = 1.0 if len(dst_list) == 1 else 0.6
            return dst_list[0], True, conf, "same_file"
        target = alias_map.get(name)
        if target:
            if target.symbol_key:
                return target.symbol_key, True, 0.9, "import_bare"
            if target.kind == "external":
                return None, False, 0.2, "external_bare"
        return None, False, 0.15, "unknown_bare"

    if len(parts) == 2:
        obj, member = parts
        self_names = ("self", "cls") if lang == "python" else ("this",)
        if obj in self_names and class_qname:
            dst = index.class_methods.get((path, class_qname), {}).get(member)
            if dst:
                return dst, True, 0.95, "self_call"
            return None, False, 0.3, "self_call_not_found"

        target = alias_map.get(obj)
        if target:
            if target.kind == "module" and target.path:
                dst = index.top_level_by_path.get(target.path, {}).get(member)
                if dst:
                    return dst, True, 0.9, "import_module_member"
                return None, False, 0.3, "import_module_member_not_found"
            if target.kind in ("class", "symbol") and target.path and target.qname:
                dst = index.class_methods.get((target.path, target.qname), {}).get(member)
                if dst:
                    return dst, True, 0.85, "import_member_class"
                return None, False, 0.3, "import_member_class_not_found"
            if target.kind == "external":
                return None, False, 0.2, "external_member"
            return None, False, 0.3, "unknown_in_module_member"

        if obj in index.top_level_by_path.get(path, {}):
            dst = index.class_methods.get((path, obj), {}).get(member)
            if dst:
                return dst, True, 0.9, "same_file_class"
            return None, False, 0.3, "same_file_class_no_method"

        return None, False, 0.2, "unknown_receiver"

    owner_chain, member = ".".join(parts[:-1]), parts[-1]
    if lang == "java":
        if owner_chain in index.java_fqcn_index:
            target_path, class_q = index.java_fqcn_index[owner_chain]
            dst = index.class_methods.get((target_path, class_q), {}).get(member)
            if dst:
                return dst, True, 0.9, "fqcn"
            return None, False, 0.3, "fqcn_method_not_found"
        for wc in wildcard_pkgs:
            candidate_fqcn = f"{wc}.{parts[-2]}"
            if candidate_fqcn in index.java_fqcn_index:
                target_path, class_q = index.java_fqcn_index[candidate_fqcn]
                dst = index.class_methods.get((target_path, class_q), {}).get(member)
                if dst:
                    return dst, True, 0.75, "wildcard_import"
    if lang == "python" and owner_chain in index.module_key_by_modname:
        target_path = index.module_path_by_modname[owner_chain]
        dst = index.top_level_by_path.get(target_path, {}).get(member)
        if dst:
            return dst, True, 0.9, "dotted_module"
        return None, False, 0.3, "dotted_module_member_not_found"

    return None, False, 0.2, "chain_unsupported"


# --------------------------------------------------------------------- java
def _extract_java_imports(root: Node, src: bytes, path: str, index: ScanIndex):
    edges, alias_map, wildcard_pkgs = [], {}, []
    src_key = index.module_key_by_path.get(path)
    for node in _find_all(root, {"import_declaration"}):
        is_wildcard = any(c.type == "asterisk" for c in node.children)
        scoped = next((c for c in node.children if c.type in ("scoped_identifier", "identifier")), None)
        if scoped is None:
            continue
        fqcn_or_pkg = _text(scoped, src)
        if is_wildcard:
            wildcard_pkgs.append(fqcn_or_pkg)
            continue
        simple_name = fqcn_or_pkg.rsplit(".", 1)[-1]
        if fqcn_or_pkg in index.java_fqcn_index:
            target_path, class_qname = index.java_fqcn_index[fqcn_or_pkg]
            dst_key = index.top_level_by_path.get(target_path, {}).get(class_qname)
            target_module_key = index.module_key_by_path.get(target_path)
            if src_key and target_module_key:
                edges.append(_edge(src_key, target_module_key, None, "imports", True, 1.0, None, {}))
            alias_map[simple_name] = AliasTarget("class", path=target_path, qname=class_qname, symbol_key=dst_key)
        else:
            if src_key:
                edges.append(_edge(src_key, None, fqcn_or_pkg, "imports", False, 0.1, None, {}))
            alias_map[simple_name] = AliasTarget("external", raw=fqcn_or_pkg)
    return edges, alias_map, wildcard_pkgs


def _java_call_parts(node: Node, src: bytes):
    object_field = node.child_by_field_name("object")
    name_field = node.child_by_field_name("name")
    if name_field is None:
        return None
    base = _flatten_chain(object_field, src) if object_field is not None else []
    if base is None:
        return None
    return base + [_text(name_field, src)]


def _collect_java(node, src, path, scope, func_key, index, calls_out):
    for child in node.children:
        t = child.type
        if t in ("class_declaration", "interface_declaration"):
            name_node = child.child_by_field_name("name")
            name = _text(name_node, src) if name_node else None
            new_scope = scope + [name] if name else scope
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_java(body, src, path, new_scope, func_key, index, calls_out)
        elif t == "method_declaration":
            name_node = child.child_by_field_name("name")
            name = _text(name_node, src) if name_node else None
            new_func_key = func_key
            if name:
                qname = ".".join(scope + [name])
                params = tuple(_java_param_types(child.child_by_field_name("parameters"), src))
                key = index.symbol_key_exact.get((path, qname, params))
                if key:
                    new_func_key = key
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_java(body, src, path, scope, new_func_key, index, calls_out)
        elif t == "method_invocation":
            parts = _java_call_parts(child, src)
            obj_field = child.child_by_field_name("object")
            name_field = child.child_by_field_name("name")
            start = obj_field.start_byte if obj_field is not None else child.start_byte
            end = name_field.end_byte if name_field is not None else child.end_byte
            raw_text = _clean_text(src[start:end].decode("utf-8", errors="replace"))
            calls_out.append((func_key, scope[-1] if scope else None, parts,
                               child.start_point[0] + 1, raw_text))
            _collect_java(child, src, path, scope, func_key, index, calls_out)
        else:
            _collect_java(child, src, path, scope, func_key, index, calls_out)


# ------------------------------------------------------------------- python
def _python_dotted_and_alias(name_node: Node, src: bytes):
    if name_node.type == "aliased_import":
        dotted_node = name_node.child_by_field_name("name")
        alias_node = name_node.child_by_field_name("alias")
        return _text(dotted_node, src), _text(alias_node, src)
    dotted = _text(name_node, src)
    return dotted, dotted.split(".")[0]


def _python_base_parts(module_node: Node | None, src: bytes, importer_path: str):
    if module_node is None:
        return [], 0
    if module_node.type == "relative_import":
        prefix_node = next((c for c in module_node.children if c.type == "import_prefix"), None)
        dots = len(_text(prefix_node, src)) if prefix_node else 0
        dotted_node = next((c for c in module_node.children if c.type == "dotted_name"), None)
        rel_dotted = _text(dotted_node, src) if dotted_node else ""

        from worker.handlers.ast_extract import _module_name
        cur_parts = _module_name(importer_path).split(".")
        anchor = cur_parts[:-dots] if 0 < dots <= len(cur_parts) else []
        if rel_dotted:
            anchor = anchor + rel_dotted.split(".")
        return anchor, dots
    return _text(module_node, src).split("."), 0


def _extract_python_imports(root: Node, src: bytes, path: str, index: ScanIndex):
    edges, alias_map = [], {}
    src_key = index.module_key_by_path.get(path)

    for node in _find_all(root, {"import_statement", "import_from_statement"}):
        if node.type == "import_statement":
            for name_node in node.children_by_field_name("name"):
                dotted, local = _python_dotted_and_alias(name_node, src)
                dst_key = index.module_key_by_modname.get(dotted)
                if dst_key:
                    if src_key:
                        edges.append(_edge(src_key, dst_key, None, "imports", True, 1.0, None, {}))
                    alias_map[local] = AliasTarget("module", path=index.module_path_by_modname[dotted])
                else:
                    if src_key:
                        edges.append(_edge(src_key, None, dotted, "imports", False, 0.1, None, {}))
                    alias_map[local] = AliasTarget("external", raw=dotted)
        else:
            module_node = node.child_by_field_name("module_name")
            base_parts, dots = _python_base_parts(module_node, src, path)
            base_modname = ".".join(base_parts)
            for name_node in node.children_by_field_name("name"):
                imported_name, local = _python_dotted_and_alias(name_node, src)
                submodule_name = f"{base_modname}.{imported_name}" if base_modname else imported_name
                if submodule_name in index.module_key_by_modname:
                    dst_key = index.module_key_by_modname[submodule_name]
                    if src_key:
                        edges.append(_edge(src_key, dst_key, None, "imports", True, 1.0, None, {}))
                    alias_map[local] = AliasTarget("module", path=index.module_path_by_modname[submodule_name])
                    continue
                if base_modname in index.module_key_by_modname:
                    target_path = index.module_path_by_modname[base_modname]
                    dst_key = index.module_key_by_modname[base_modname]
                    if src_key:
                        edges.append(_edge(src_key, dst_key, None, "imports", True, 1.0, None, {}))
                    member_key = index.top_level_by_path.get(target_path, {}).get(imported_name)
                    alias_map[local] = AliasTarget(
                        "symbol" if member_key else "unknown_in_module",
                        path=target_path, qname=imported_name, symbol_key=member_key)
                else:
                    hint = f"{base_modname}.{imported_name}" if base_modname else imported_name
                    conf = 0.3 if dots > 0 else 0.1
                    if src_key:
                        edges.append(_edge(src_key, None, hint, "imports", False, conf, None, {}))
                    alias_map[local] = AliasTarget("external", raw=hint)
    return edges, alias_map


def _collect_python(node, src, path, scope, func_key, index, calls_out):
    for child in node.children:
        t = child.type
        if t == "class_definition":
            name_node = child.child_by_field_name("name")
            name = _text(name_node, src) if name_node else None
            new_scope = scope + [name] if name else scope
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_python(body, src, path, new_scope, func_key, index, calls_out)
        elif t == "function_definition":
            name_node = child.child_by_field_name("name")
            name = _text(name_node, src) if name_node else None
            new_func_key = func_key
            if name:
                qname = ".".join(scope + [name])
                params = tuple(_python_param_types(child.child_by_field_name("parameters"), src))
                key = index.symbol_key_exact.get((path, qname, params))
                if key:
                    new_func_key = key
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_python(body, src, path, scope, new_func_key, index, calls_out)
        elif t == "call":
            func_field = child.child_by_field_name("function")
            parts = _flatten_chain(func_field, src)
            raw_text = _clean_text(_text(func_field, src) if func_field is not None
                                    else _text(child, src))
            calls_out.append((func_key, scope[-1] if scope else None, parts,
                               child.start_point[0] + 1, raw_text))
            _collect_python(child, src, path, scope, func_key, index, calls_out)
        else:
            _collect_python(child, src, path, scope, func_key, index, calls_out)


# ------------------------------------------------------------- js/typescript
def _require_source(value: Node, src: bytes) -> str | None:
    """`require('path')` 형태의 call_expression이면 경로 문자열을, 아니면 None을 반환."""
    if value.type != "call_expression":
        return None
    func = value.child_by_field_name("function")
    if func is None or func.type != "identifier" or _text(func, src) != "require":
        return None
    args = value.child_by_field_name("arguments")
    if args is None:
        return None
    str_args = [c for c in args.children if c.type == "string"]
    if len(str_args) != 1:
        return None
    return _text(str_args[0], src)[1:-1]


def _object_pattern_names(pattern: Node, src: bytes) -> list[tuple[str, str]]:
    """`const { a, b: renamed } = require(...)` 에서 (local, imported) 쌍을 뽑는다.

    `_destructured_names`(cfg_extract.py)와 달리 원래 이름(imported)도 필요해서
    별도 헬퍼로 둔다 — require 구조분해에서 rest/default 는 드물어 생략한다.
    """
    pairs: list[tuple[str, str]] = []
    for c in pattern.children:
        if not c.is_named:
            continue
        if c.type == "shorthand_property_identifier_pattern":
            name = _text(c, src)
            pairs.append((name, name))
        elif c.type == "pair_pattern":
            key = c.child_by_field_name("key")
            value = c.child_by_field_name("value")
            if key is not None and value is not None and value.type == "identifier":
                pairs.append((_text(value, src), _text(key, src)))
    return pairs


def _extract_js_imports(root: Node, src: bytes, path: str, index: ScanIndex):
    edges, alias_map = [], {}
    src_key = index.module_key_by_path.get(path)

    def resolve_relative(raw_source: str) -> str | None:
        if not (raw_source.startswith("./") or raw_source.startswith("../")):
            return None
        base = posixpath.normpath(posixpath.join(posixpath.dirname(path), raw_source))
        if base in index.files_present:
            return base
        for suf in _JS_SUFFIXES:
            if base + suf in index.files_present:
                return base + suf
        for suf in _JS_SUFFIXES:
            cand = f"{base}/index{suf}"
            if cand in index.files_present:
                return cand
        return None

    for node in _find_all(root, {"import_statement"}):
        source_node = node.child_by_field_name("source")
        if source_node is None:
            continue
        raw_source = _text(source_node, src)[1:-1]
        type_only = any(c.type == "type" for c in node.children)
        clause = next((c for c in node.children if c.type == "import_clause"), None)
        specs: list[tuple[str, str, str | None]] = []
        if clause is not None:
            for c in clause.children:
                if c.type == "identifier":
                    specs.append((_text(c, src), "default", None))
                elif c.type == "namespace_import":
                    alias_node = c.children[-1]
                    specs.append((_text(alias_node, src), "namespace", None))
                elif c.type == "named_imports":
                    for spec in c.children:
                        if spec.type != "import_specifier":
                            continue
                        name_n = spec.child_by_field_name("name")
                        alias_n = spec.child_by_field_name("alias")
                        local = _text(alias_n, src) if alias_n else _text(name_n, src)
                        specs.append((local, "named", _text(name_n, src)))

        resolved_path = resolve_relative(raw_source)
        if resolved_path:
            dst_key = index.module_key_by_path.get(resolved_path)
            if src_key and dst_key:
                edges.append(_edge(src_key, dst_key, None, "imports", True, 1.0, None,
                                    {"raw_source": raw_source}))
            if not type_only:
                for local, kind, imported_name in specs:
                    if kind == "namespace":
                        alias_map[local] = AliasTarget("module", path=resolved_path)
                    elif kind == "named":
                        member_key = index.top_level_by_path.get(resolved_path, {}).get(imported_name)
                        alias_map[local] = AliasTarget(
                            "symbol" if member_key else "unknown_in_module",
                            path=resolved_path, qname=imported_name, symbol_key=member_key)
                    elif kind == "default":
                        cands = list(index.top_level_by_path.get(resolved_path, {}).items())
                        alias_map[local] = (AliasTarget("symbol", path=resolved_path, symbol_key=cands[0][1])
                                             if len(cands) == 1 else
                                             AliasTarget("unknown_in_module", path=resolved_path))
        else:
            is_relative = raw_source.startswith(".")
            if src_key:
                edges.append(_edge(src_key, None, raw_source, "imports", False,
                                    0.3 if is_relative else 0.1, None, {}))
            if not type_only:
                for local, kind, imported_name in specs:
                    alias_map[local] = AliasTarget("external", raw=raw_source, qname=imported_name)

    # CommonJS `require()` — import_statement 트리를 안 만들어서 위 루프에
    # 안 걸린다. `const adminService = require('../services/admin.service')`
    # 같은 네임스페이스 스타일이 흔하고, ES import 처리와 똑같은 모양으로
    # alias_map을 채워야 _resolve_call()이 두 경로를 구분 없이 처리한다.
    for decl in _find_all(root, {"variable_declarator"}):
        value = decl.child_by_field_name("value")
        name_node = decl.child_by_field_name("name")
        if value is None or name_node is None:
            continue
        raw_source = _require_source(value, src)
        if raw_source is None:
            continue

        resolved_path = resolve_relative(raw_source)
        if resolved_path:
            dst_key = index.module_key_by_path.get(resolved_path)
            if src_key and dst_key:
                edges.append(_edge(src_key, dst_key, None, "imports", True, 1.0, None,
                                    {"raw_source": raw_source, "kind": "require"}))
        elif src_key:
            is_relative = raw_source.startswith(".")
            edges.append(_edge(src_key, None, raw_source, "imports", False,
                                0.3 if is_relative else 0.1, None, {"kind": "require"}))

        if name_node.type == "identifier":
            local = _text(name_node, src)
            alias_map[local] = (AliasTarget("module", path=resolved_path) if resolved_path
                                 else AliasTarget("external", raw=raw_source))
        elif name_node.type == "object_pattern":
            for local, imported_name in _object_pattern_names(name_node, src):
                if resolved_path:
                    member_key = index.top_level_by_path.get(resolved_path, {}).get(imported_name)
                    alias_map[local] = AliasTarget(
                        "symbol" if member_key else "unknown_in_module",
                        path=resolved_path, qname=imported_name, symbol_key=member_key)
                else:
                    alias_map[local] = AliasTarget("external", raw=raw_source, qname=imported_name)

    return edges, alias_map


def _collect_js(node, src, path, scope, func_key, index, calls_out, param_fn):
    for child in node.children:
        t = child.type
        if t in ("class_declaration", "interface_declaration"):
            name_node = child.child_by_field_name("name")
            name = _text(name_node, src) if name_node else None
            new_scope = scope + [name] if name else scope
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_js(body, src, path, new_scope, func_key, index, calls_out, param_fn)
        elif t == "method_definition":
            name_node = child.child_by_field_name("name")
            name = _text(name_node, src) if name_node else None
            new_func_key = func_key
            if name:
                qname = ".".join(scope + [name])
                params = tuple(param_fn(child.child_by_field_name("parameters"), src))
                key = index.symbol_key_exact.get((path, qname, params))
                if key:
                    new_func_key = key
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_js(body, src, path, scope, new_func_key, index, calls_out, param_fn)
        elif t == "function_declaration":
            name_node = child.child_by_field_name("name")
            name = _text(name_node, src) if name_node else None
            new_func_key = func_key
            if name:
                qname = ".".join(scope + [name])
                params = tuple(param_fn(child.child_by_field_name("parameters"), src))
                key = index.symbol_key_exact.get((path, qname, params))
                if key:
                    new_func_key = key
            body = child.child_by_field_name("body")
            if body is not None:
                _collect_js(body, src, path, scope, new_func_key, index, calls_out, param_fn)
        elif t == "variable_declarator":
            value = child.child_by_field_name("value")
            name_node = child.child_by_field_name("name")
            if (value is not None and value.type in ("arrow_function", "function_expression")
                    and name_node is not None and name_node.type == "identifier"):
                name = _text(name_node, src)
                qname = ".".join(scope + [name])
                params = tuple(param_fn(value.child_by_field_name("parameters"), src))
                key = index.symbol_key_exact.get((path, qname, params))
                new_func_key = key if key else func_key
                body = value.child_by_field_name("body")
                if body is not None:
                    _collect_js(body, src, path, scope, new_func_key, index, calls_out, param_fn)
            else:
                _collect_js(child, src, path, scope, func_key, index, calls_out, param_fn)
        elif t == "call_expression":
            func_field = child.child_by_field_name("function")
            parts = _flatten_chain(func_field, src)
            raw_text = _clean_text(_text(func_field, src) if func_field is not None
                                    else _text(child, src))
            calls_out.append((func_key, scope[-1] if scope else None, parts,
                               child.start_point[0] + 1, raw_text))
            _collect_js(child, src, path, scope, func_key, index, calls_out, param_fn)
        else:
            _collect_js(child, src, path, scope, func_key, index, calls_out, param_fn)


def _js_param_fn(params, src):
    return _js_param_types(params)


# --------------------------------------------------------------------- main
def extract_edges(lang: str, suffix: str, path: str, source: bytes, index: ScanIndex) -> list[dict]:
    language = _LANGUAGES[suffix]
    root = Parser(language).parse(source).root_node
    module_key = index.module_key_by_path.get(path)

    calls: list[tuple] = []
    wildcard_pkgs: list[str] = []

    if lang == "java":
        edges, alias_map, wildcard_pkgs = _extract_java_imports(root, source, path, index)
        _collect_java(root, source, path, [], module_key, index, calls)
    elif lang == "python":
        edges, alias_map = _extract_python_imports(root, source, path, index)
        _collect_python(root, source, path, [], module_key, index, calls)
    elif lang == "javascript":
        edges, alias_map = _extract_js_imports(root, source, path, index)
        _collect_js(root, source, path, [], module_key, index, calls, _js_param_fn)
    elif lang == "typescript":
        edges, alias_map = _extract_js_imports(root, source, path, index)
        _collect_js(root, source, path, [], module_key, index, calls, _ts_param_types)
    else:
        return []

    for func_key, class_qname, parts, call_line, raw_text in calls:
        if func_key is None:
            continue
        if not parts:
            edges.append(_edge(func_key, None, raw_text, "calls", False, 0.1, call_line,
                                {"kind": "dynamic"}))
            continue
        dst_key, resolved, confidence, kind_label = _resolve_call(
            parts, lang, path, class_qname, alias_map, index, wildcard_pkgs)
        if resolved:
            edges.append(_edge(func_key, dst_key, None, "calls", True, confidence, call_line,
                                {"kind": kind_label}))
        else:
            edges.append(_edge(func_key, None, ".".join(parts), "calls", False, confidence, call_line,
                                {"kind": kind_label}))

    return edges
