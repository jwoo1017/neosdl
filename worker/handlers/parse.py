"""scan.parse — WBS 1.2 AST 기반 코드 구조 추출 + WBS 1.3 호출/의존 관계 추출.

파일 목록 적재(WBS 1.1) 다음, tree-sitter 로 module/class/interface/
function/method/field 심볼을 뽑아 symbol 테이블에 적재하고, 이어서
(모든 파일의 심볼이 갖춰진 뒤) 호출식·import 문을 해석해 symbol_edge 에
적재한다. 크로스파일 해석이 필요해서 심볼 적재가 전부 끝난 후 두 번째
패스로 돈다.
"""

import hashlib
import json
import logging
from pathlib import Path

from app import queue
from app.config import settings
from app.db import tx
from worker.handlers.ast_extract import extract_symbols
from worker.handlers.clone import pipeline_source_root
from worker.handlers.edge_extract import build_scan_index, extract_edges

log = logging.getLogger("worker.parse")

LANG_BY_SUFFIX = {".java": "java", ".py": "python", ".ts": "typescript",
                  ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript"}

SKIP_DIRS = {".git", "node_modules", "target", "build", "dist", "__pycache__",
             ".venv", "venv"}
MAX_BYTES = 2_000_000


def handle(payload: dict) -> None:
    scan_id = payload["scan_id"]

    with tx() as cur:
        cur.execute(
            """
            UPDATE scan SET status='running', started_at=now()
            WHERE id=%s AND status='queued'
            RETURNING repository_id
            """,
            (scan_id,),
        )
        row = cur.fetchone()
    if row is None:
        log.warning("scan %s 이미 처리됨 — 건너뜀", scan_id)
        return

    repository_id = row["repository_id"]
    root = pipeline_source_root(str(scan_id), str(repository_id))

    file_count = 0
    symbol_count = 0
    edge_count = 0
    sources: dict[str, bytes] = {}      # path -> 원본 바이트, 두 번째 패스(관계 추출)에서 재사용
    file_langs: dict[str, str] = {}
    with tx() as cur:
        for path, lang, size, digest, loc, source in _walk(root):
            file_count += 1
            cur.execute(
                """
                INSERT INTO code_file (scan_id, path, lang, size_bytes, loc, content_hash)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (scan_id, path) DO UPDATE SET size_bytes = EXCLUDED.size_bytes
                RETURNING id
                """,
                (scan_id, path, lang, size, loc, digest),
            )
            file_id = cur.fetchone()["id"]

            try:
                symbols = extract_symbols(lang, Path(path).suffix, path, source)
            except Exception:
                log.exception("scan %s: %s 심볼 추출 실패 — 건너뜀", scan_id, path)
                continue

            for sym in symbols:
                cur.execute(
                    """
                    INSERT INTO symbol (scan_id, file_id, repository_id, lang, path,
                                        qualified_name, param_types, kind,
                                        start_line, end_line, body_hash, signature_text)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s::symbol_kind,%s,%s,%s,%s)
                    ON CONFLICT (scan_id, symbol_key) DO NOTHING
                    """,
                    (scan_id, file_id, repository_id, lang, path,
                     sym["qualified_name"], sym["param_types"], sym["kind"],
                     sym["start_line"], sym["end_line"], sym["body_hash"],
                     sym["signature_text"]),
                )
                symbol_count += 1

            sources[path] = source
            file_langs[path] = lang

        log.info("scan %s: 파일 %d개, 심볼 %d개", scan_id, file_count, symbol_count)

        # WBS 1.3 — 모든 파일의 심볼이 갖춰진 뒤에야 크로스파일 호출/의존 관계를
        # 해석할 수 있으므로 두 번째 패스로 돈다. 같은 트랜잭션 안에서 계속한다.
        index = build_scan_index(cur, scan_id, sources)
        for path, source in sources.items():
            lang = file_langs[path]
            try:
                edges = extract_edges(lang, Path(path).suffix, path, source, index)
            except Exception:
                log.exception("scan %s: %s 관계 추출 실패 — 건너뜀", scan_id, path)
                continue
            for e in edges:
                cur.execute(
                    """
                    INSERT INTO symbol_edge (scan_id, src_symbol_key, dst_symbol_key, dst_hint,
                                              edge_type, resolved, confidence, call_line, evidence)
                    VALUES (%s,%s,%s,%s,%s::edge_type,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (scan_id, src_symbol_key, dst_symbol_key, edge_type)
                        WHERE resolved DO NOTHING
                    """,
                    (scan_id, e["src_symbol_key"], e["dst_symbol_key"], e["dst_hint"],
                     e["edge_type"], e["resolved"], e["confidence"], e["call_line"],
                     json.dumps(e["evidence"])),
                )
                edge_count += 1

        log.info("scan %s: 관계 %d개", scan_id, edge_count)

        # 투영은 스캔 트랜잭션과 같이 커밋되지만 실행은 별도 잡이다.
        # Neo4j 가 느리거나 죽어 있어도 스캔이 붙잡히지 않는다.
        cur.execute(
            """
            INSERT INTO graph_projection (scan_id, backend, stale)
            VALUES (%s, %s::projection_backend, true)
            ON CONFLICT (scan_id) DO UPDATE SET stale = true
            """,
            (scan_id, settings().graph_backend),
        )
        queue.enqueue(cur, "graph.project", {"scan_id": scan_id},
                      dedupe_key=f"project:{scan_id}")

        # 의미 태깅(LLM)은 구조 그래프가 다 만들어진 뒤, 별도 잡으로 돈다 —
        # Ollama가 느려도(파일 수십개 기준 수 분) 이 잡 하나만 오래 걸릴 뿐
        # scan.parse/graph.project 는 이미 끝난 뒤라 스캔 결과에 영향 없다.
        queue.enqueue(cur, "symbol.annotate", {"scan_id": scan_id},
                      priority=150, dedupe_key=f"annotate:{scan_id}")

        # CFG/PDG(WBS 2 기초 계층)도 구조 그래프와 무관한 별도 잡 — 실패해도
        # 스캔 자체(심볼/호출관계)는 이미 안전하게 끝난 뒤다.
        queue.enqueue(cur, "pdg.build", {"scan_id": scan_id},
                      priority=150, dedupe_key=f"pdg:{scan_id}")

        cur.execute(
            """
            UPDATE scan SET status='succeeded', finished_at=now(),
                            stats = stats || jsonb_build_object('file_count', %s, 'symbol_count', %s,
                                                                'edge_count', %s)
            WHERE id=%s
            """,
            (file_count, symbol_count, edge_count, scan_id),
        )


def _walk(root: Path):
    if not root.exists():
        raise FileNotFoundError(f"작업 디렉터리 없음: {root} (repo.clone 이 먼저 돌아야 한다)")
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if SKIP_DIRS & set(p.relative_to(root).parts):
            continue
        lang = LANG_BY_SUFFIX.get(p.suffix)
        if lang is None:
            continue
        size = p.stat().st_size
        if size > MAX_BYTES:
            continue
        data = p.read_bytes()
        yield (
            # JS/TS 상대 import(`./foo`)가 '/' 를 쓰므로 Windows 에서도 POSIX 구분자로 통일.
            str(p.relative_to(root)).replace("\\", "/"),
            lang,
            size,
            hashlib.sha256(data).hexdigest(),
            data.count(b"\n") + 1,
            data,
        )
