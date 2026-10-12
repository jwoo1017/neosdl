"""GraphStore — 그래프 조회의 유일한 진입점.

이 인터페이스가 존재하는 이유는 성능이 아니라 라이선스다.

Neo4j Community 는 GPLv3 다. 제품이 Neo4j 에 직접 의존하는 순간 고객사의
라이선스 정책 하나로 납품이 막힌다. 그래서 그래프 접근을 전부 이 뒤로 보내고,
Neo4j 는 "있으면 빨라지는 것"으로만 둔다.

지켜야 하는 성질은 하나다 — **두 구현이 같은 데이터에서 같은 결과를 낸다.**
이게 깨지면 폴백은 폴백이 아니라 다른 제품이 된다.
tests/test_graph_equivalence.py 가 이 성질을 검사한다.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class CallerPath:
    """위험도 전파 1건. path 가 그대로 Risk Evidence 가 된다.

    path 는 항상 **호출자 → ... → 대상** 순서다.
    "이 파일은 FraudScoring.score() 를 2단계 거쳐 호출한다" 를 그대로 읽을 수 있게.
    두 백엔드가 내부적으로 다른 순서로 순회하더라도 이 계약은 동일하다.
    """

    symbol_key: str
    hops: int
    path: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"symbol_key": self.symbol_key, "hops": self.hops, "path": list(self.path)}


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    backend: str
    node_count: int = 0
    edge_count: int = 0
    skipped: bool = False
    detail: str = ""


@runtime_checkable
class GraphStore(Protocol):
    backend: str

    def health(self) -> bool:
        """백엔드가 실제로 응답하는가. Neo4j 가 죽어 있으면 False."""

    def callers_within(
        self, scan_id: str, target_symbol_key: str, depth: int = 3
    ) -> list[CallerPath]:
        """target 을 depth 단계 안에서 호출하는 심볼들. 심볼당 최단 경로 하나."""

    def project(self, scan_id: str) -> ProjectionResult:
        """정본(PostgreSQL)의 스냅샷을 그래프로 투영한다.

        postgres 구현에서는 no-op — 정본이 곧 그래프이므로 투영할 데가 없다.
        """

    def drop(self, scan_id: str) -> ProjectionResult:
        """투영을 지운다. 정본은 건드리지 않는다."""


def make_graph_store() -> GraphStore:
    """설정에 따라 구현을 고른다.

    NEO4J_URI 가 비어 있으면 postgres_cte 로 떨어진다. 예외를 던지지 않는다 —
    그래프 계층 부재는 오류가 아니라 정상적인 배포 형태다.
    """
    from app.config import settings

    cfg = settings()
    if not cfg.neo4j_uri:
        from app.graph.postgres import PostgresGraphStore

        return PostgresGraphStore()

    from app.graph.neo4j_store import Neo4jGraphStore

    return Neo4jGraphStore(cfg.neo4j_uri, cfg.neo4j_user or "neo4j",
                           cfg.neo4j_password or "")


MAX_DEPTH = 6


def check_depth(depth: int) -> int:
    """depth 는 Cypher 의 가변 길이 상한으로 문자열 보간된다.

    파라미터 바인딩이 안 되는 자리이므로 여기서 반드시 정수로 좁힌다.
    이 함수를 우회해서 depth 를 쿼리에 넣지 말 것.
    """
    if not isinstance(depth, int) or isinstance(depth, bool):
        raise TypeError("depth must be int")
    if not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth must be between 1 and {MAX_DEPTH}")
    return depth


# 관계 타입은 Cypher 에서 파라미터로 바인딩되지 않는다.
# DB 의 edge_type ENUM 과 1:1 로 맞춘 화이트리스트로만 보간한다.
EDGE_TYPE_TO_REL = {
    "calls": "CALLS",
    "imports": "IMPORTS",
    "extends": "EXTENDS",
    "implements": "IMPLEMENTS",
    "references": "REFERENCES",
}
