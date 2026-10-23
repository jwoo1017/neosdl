"""검증 러너 — 하나의 finding 을 confirmed/false_positive/inconclusive/not_attempted 로 판정.

흐름: entrypoint 해석 → (도달 가능하면) 무해 프로브 합성 → 대상 앱에 실제 요청(control+probe) →
앱로그/IAST 신호 수집 → oracle 판정 → feedback 반영.

샌드박스와 **하드커플하지 않는다**: 대상 앱 호출과 로그/IAST 수집을 두 콜러블로 주입받는다.
- app_client(method, path, param_name, param_in, value) -> ResponseLike(status_code:int, text:str)
- log_source(nonce) -> {"app_log": str, "iast_reached": bool|None}
운영에서는 샌드박스(booted base_url + IAST 로그)가 이 둘을 제공하고, 테스트는 가짜를 주입한다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from app.dynamic import entrypoint, oracle, probes

log = logging.getLogger("app.dynamic.runner")


@dataclass
class VerifyOutcome:
    verdict: object                 # oracle.Verdict
    entry_route: dict
    oracle_kind: str                # blackbox | iast | blackbox+iast
    artifacts: list[dict]           # [{kind, content}] — feedback 가 마스킹 후 저장


def _not_attempted(reason: str, entry_route: dict | None = None) -> VerifyOutcome:
    return VerifyOutcome(
        verdict=oracle.Verdict("not_attempted", 0.0, {"reason": reason}),
        entry_route=entry_route or {"reason": reason}, oracle_kind="blackbox", artifacts=[])


def verify_finding(cur, scan_id: str, finding: dict, *, app_client: Callable,
                   log_source: Callable | None = None, allow_instrumentation: bool = False) -> VerifyOutcome:
    """finding 하나를 동적 검증한다. app_client 가 None 결과를 주면(앱 미기동) not_attempted."""
    category = finding["category"]
    if category not in probes.CATEGORIES:
        return _not_attempted("unsupported_category")

    ep = entrypoint.resolve(cur, scan_id, finding)
    route = {"method": ep.method, "path": ep.path_template, "param_name": ep.param_name,
             "param_in": ep.param_in, "auth_required": ep.auth_required, "resolved_by": ep.resolved_by,
             "confidence": ep.confidence}
    if not ep.ok:
        return _not_attempted(ep.reason or "no_http_entrypoint", route)
    if ep.auth_required:
        # 1차: 인증 필요한 라우트는 자격 없이 못 때림 — 정직하게 미시도.
        return _not_attempted("needs_auth", route)
    if not ep.param_name:
        return _not_attempted("param_unresolved", route)

    probe = probes.synthesize(category, finding["id"])
    if probe is None:
        return _not_attempted("probe_unavailable", route)

    # control + probe 실제 요청(멱등). app_client 는 (status, text) 를 주거나 None(미기동).
    control = app_client(ep.method, ep.path_template, ep.param_name, ep.param_in, probe.control_value)
    probe_resp = app_client(ep.method, ep.path_template, ep.param_name, ep.param_in, probe.probe_value)
    if control is None or probe_resp is None:
        return _not_attempted("app_not_running", route)

    logs, iast_reached = "", None
    if log_source is not None:
        collected = log_source(probe.nonce) or {}
        logs = collected.get("app_log", "") or ""
        iast_reached = collected.get("iast_reached") if allow_instrumentation else None

    verdict = oracle.decide(
        category, control_body=getattr(control, "text", ""), probe_body=getattr(probe_resp, "text", ""),
        control_status=getattr(control, "status_code", 200), probe_status=getattr(probe_resp, "status_code", 200),
        nonce=probe.probe_value, app_logs=logs, iast_reached=iast_reached, entry_confidence=ep.confidence)

    oracle_kind = "iast" if iast_reached is not None else "blackbox"
    artifacts = [
        {"kind": "request", "content": f"{ep.method} {ep.path_template} {ep.param_name}={probe.probe_value}"},
        {"kind": "response", "content": getattr(probe_resp, "text", "")[:8000]},
    ]
    if logs:
        artifacts.append({"kind": "app_log", "content": logs[:8000]})
    return VerifyOutcome(verdict=verdict, entry_route=route, oracle_kind=oracle_kind, artifacts=artifacts)


def rate_limited_client(base_url: str, rps: int = 2):
    """운영용 app_client — httpx 로 base_url 에 실제 요청(토큰버킷 rps 제한, idempotent GET 우선).

    import 는 함수 안에서(테스트는 이 함수를 안 쓰고 가짜 주입). 앱 미기동/오류면 None 반환.
    """
    import httpx

    interval = 1.0 / max(rps, 1)
    state = {"last": 0.0}

    def client(method, path, param_name, param_in, value):
        wait = interval - (time.monotonic() - state["last"])
        if wait > 0:
            time.sleep(wait)
        state["last"] = time.monotonic()
        url = base_url.rstrip("/") + "/" + path.lstrip("/")
        params = {param_name: value} if param_in in ("query", "path") else None
        body = {param_name: value} if param_in in ("body", "form") else None
        try:
            return httpx.request(method, url, params=params, json=body, timeout=15)
        except Exception:
            log.warning("대상 앱 요청 실패: %s %s", method, url, exc_info=True)
            return None

    return client
