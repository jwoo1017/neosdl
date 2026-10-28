"""app/sandbox.py allowlist 게이트 + worker/sandbox ContainerSpec 안전 기본값 — 단위 테스트."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi import HTTPException

from app import sandbox
from app.config import Settings
from worker.sandbox import ContainerSpec


class _Cur:
    def __init__(self, repo_row):
        self._repo = repo_row
        self._audits = []

    def execute(self, sql, params=None):
        self._last = "repo" if "FROM repository" in sql else "audit"
        if self._last == "audit":
            self._audits.append((sql, params))

    def fetchone(self):
        if self._last == "repo":
            return self._repo
        return {"seq": 1}


def _patch_settings(monkeypatch, **kw):
    base = dict(sandbox_enabled=True, sandbox_repo_allowlist="jwoo1017/neosdl,jwoo1017/acts-gallery")
    base.update(kw)
    monkeypatch.setattr(sandbox, "settings", lambda: Settings(**base))


def test_repo_allowed_pure(monkeypatch):
    _patch_settings(monkeypatch)
    assert sandbox.repo_allowed("jwoo1017/neosdl") is True
    assert sandbox.repo_allowed("evilcorp/malware") is False
    assert sandbox.repo_allowed(None) is False


def test_empty_allowlist_is_fail_closed(monkeypatch):
    _patch_settings(monkeypatch, sandbox_repo_allowlist="")
    assert sandbox.repo_allowed("jwoo1017/neosdl") is False   # 비면 아무것도 허용 안 함


def test_assert_rejects_external_repo_with_audit_in_allowlist_mode(monkeypatch):
    # allowlist 모드(보수적): 미등록/외부 레포는 거부 + audit.
    _patch_settings(monkeypatch, sandbox_repo_authz="allowlist")
    cur = _Cur({"namespace": "evilcorp", "name": "malware"})
    with pytest.raises(HTTPException) as ei:
        sandbox.assert_repo_allowed(cur, "r1", actor="user:1")
    assert ei.value.status_code == 403 and ei.value.detail == "external_repo_not_allowed"
    assert cur._audits, "거부는 audit 에 남아야 한다"


def test_assert_allows_self_repo_in_allowlist_mode(monkeypatch):
    _patch_settings(monkeypatch, sandbox_repo_authz="allowlist")
    cur = _Cur({"namespace": "jwoo1017", "name": "neosdl"})
    assert sandbox.assert_repo_allowed(cur, "r1", actor="user:1") == "jwoo1017/neosdl"


def test_assert_workspace_authz_allows_membership_repo_with_audit(monkeypatch):
    # 워크스페이스 모드(기본): allowlist 등록 없이도 멤버십으로 인가(engagement 생성 시 manager
    # 권한 체크가 선행). sandbox.authorized 로 audit. 외부 레포는 애초에 워크스페이스에 없어야 함.
    _patch_settings(monkeypatch, sandbox_repo_authz="workspace", sandbox_repo_allowlist="")
    cur = _Cur({"namespace": "finble", "name": "exchange-backend"})
    assert sandbox.assert_repo_allowed(cur, "r1", actor="user:1") == "finble/exchange-backend"
    assert cur._audits, "인가도 audit 에 남아야 한다"


def test_assert_rejects_when_sandbox_disabled(monkeypatch):
    _patch_settings(monkeypatch, sandbox_enabled=False)
    cur = _Cur({"namespace": "jwoo1017", "name": "neosdl"})
    with pytest.raises(HTTPException) as ei:
        sandbox.assert_repo_allowed(cur, "r1", actor="user:1")
    assert ei.value.status_code == 409 and ei.value.detail == "sandbox_disabled"


def test_container_spec_has_no_unsafe_fields():
    """privileged·host 바인드·docker.sock 는 아예 필드로 존재하지 않아야 한다(끌 수 없게)."""
    fields = {f.name for f in dataclasses.fields(ContainerSpec)}
    assert "privileged" not in fields
    assert not (fields & {"volumes", "binds", "mounts", "docker_sock"})
    spec = ContainerSpec(image="node:20-bookworm-slim")
    assert spec.read_only is True and spec.pids_limit == 512
