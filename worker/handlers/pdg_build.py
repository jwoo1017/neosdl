"""pdg.build — CFG/PDG(레이어 1~3) 를 만들어 DB에 적재한다.

scan.parse/graph.project 와 분리된 별도 잡이다(annotate/embed와 같은 원칙) —
CFG/PDG 추출이 실패하거나 느려도 구조 그래프(스캔 자체)는 이미 끝난 뒤라
영향이 없다. parse.py 의 인메모리 상태를 공유하지 않고 code_file/symbol을
다시 조회하고 저장소 워크디렉터리에서 소스를 다시 읽는다 — annotate.py/
embed.py 와 동일한 패턴.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.db import tx
from worker.handlers.ast_extract import _LANGUAGES
from worker.handlers.cfg_extract import analyze, build_function_cfgs
from worker.handlers.clone import pipeline_source_root
from worker.handlers.edge_extract import build_scan_index

log = logging.getLogger("worker.pdg_build")

_SUPPORTED_LANGS = {"python", "javascript", "typescript"}
_LANG_SUFFIX = {"python": ".py", "javascript": ".js", "typescript": ".ts"}


def handle(payload: dict) -> None:
    scan_id = payload["scan_id"]

    with tx() as cur:
        cur.execute("SELECT repository_id FROM scan WHERE id=%s", (scan_id,))
        row = cur.fetchone()
        if row is None:
            log.warning("scan %s 없음 — 건너뜀", scan_id)
            return
        repository_id = row["repository_id"]

        cur.execute(
            "SELECT path, lang FROM code_file WHERE scan_id=%s",
            (scan_id,),
        )
        files = [r for r in cur.fetchall() if r["lang"] in _SUPPORTED_LANGS]

    root = pipeline_source_root(str(scan_id), str(repository_id))
    sources: dict[str, bytes] = {}
    file_langs: dict[str, str] = {}
    for r in files:
        path = r["path"]
        full = root / path
        try:
            sources[path] = full.read_bytes()
        except OSError:
            log.warning("scan %s: %s 를 워크디렉터리에서 못 읽음 — 건너뜀", scan_id, path)
            continue
        file_langs[path] = r["lang"]

    if not sources:
        log.info("scan %s: CFG/PDG 대상 파일 없음(JS/TS/Python 아님)", scan_id)
        return

    with tx() as cur:
        # build_scan_index 는 symbol_key_exact 매칭에 scan 전체 symbol 이 필요하다
        # (edge_extract.py 와 동일 — WBS 1.3 인덱스를 그대로 재사용).
        index = build_scan_index(cur, scan_id, sources)

    block_count = 0
    data_edge_count = 0
    control_edge_count = 0
    func_count = 0

    for path, source in sources.items():
        lang = file_langs[path]
        suffix = _actual_suffix(path, lang)
        try:
            cfgs = build_function_cfgs(lang, suffix, path, source, index)
        except Exception:
            log.exception("scan %s: %s CFG 추출 실패 — 건너뜀", scan_id, path)
            continue

        for symbol_key, builder in cfgs.items():
            func_count += 1
            data_edges, control_edges = analyze(builder)

            with tx() as cur:
                cur.execute(
                    """
                    INSERT INTO pdg_function (scan_id, symbol_key, param_names)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (scan_id, symbol_key) DO UPDATE SET param_names = EXCLUDED.param_names
                    """,
                    (scan_id, symbol_key, builder.params),
                )
                for bid, blk in builder.blocks.items():
                    cur.execute(
                        """
                        INSERT INTO cfg_block (scan_id, symbol_key, block_id, start_line, end_line)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (scan_id, symbol_key, block_id) DO NOTHING
                        """,
                        (scan_id, symbol_key, bid, blk.start_line, blk.end_line),
                    )
                    block_count += 1

                for src_b, dst_b, label in builder.edges:
                    cur.execute(
                        """
                        INSERT INTO cfg_edge (scan_id, symbol_key, src_block_id, dst_block_id, branch_label)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (scan_id, symbol_key, src_b, dst_b, label),
                    )

                for var, def_line, kind, def_block, use_line, def_text in data_edges:
                    cur.execute(
                        """
                        INSERT INTO pdg_data_edge
                            (scan_id, symbol_key, var_name, def_line, def_block_id, use_line,
                             evidence_kind, def_text)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (scan_id, symbol_key, var, def_line, def_block, use_line, kind, def_text),
                    )
                    data_edge_count += 1

                for bid, calls in builder.calls.items():
                    for cline, target_text, arg_vars, arg_texts, arg_lines in calls:
                        cur.execute(
                            """
                            INSERT INTO pdg_call
                                (scan_id, symbol_key, block_id, line, target_text, arg_vars, arg_texts, arg_lines)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (scan_id, symbol_key, bid, cline, target_text, arg_vars, arg_texts, arg_lines),
                        )

                for branch_b, dep_b, label in control_edges:
                    cur.execute(
                        """
                        INSERT INTO pdg_control_edge
                            (scan_id, symbol_key, branch_block_id, dependent_block_id, branch_label)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (scan_id, symbol_key, branch_b, dep_b, label),
                    )
                    control_edge_count += 1

    log.info("scan %s: 함수 %d개 CFG/PDG 완료 — 블록 %d, 데이터엣지 %d, 제어엣지 %d",
              scan_id, func_count, block_count, data_edge_count, control_edge_count)


def _actual_suffix(path: str, lang: str) -> str:
    ext = Path(path).suffix
    if ext in _LANGUAGES:
        return ext
    return _LANG_SUFFIX[lang]
