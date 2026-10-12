"""app/ai_providers.py의 grammar/schema-constrained decoding(response_schema) +
fail-open 재시도 단위 테스트. 실제 LLM/게이트웨이 없이 httpx.post만 몽키패치한다.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel

from app import ai_providers


class _Choice(BaseModel):
    dummy: str = "x"


class _FakeResponse:
    def __init__(self, status_code: int = 200, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {"choices": [{"message": {"content": "ok"}}], "usage": {}}
        self.text = "error body"
        self.request = httpx.Request("POST", "http://x/v1/chat/completions")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=self.request,
                                        response=httpx.Response(self.status_code, request=self.request))

    def json(self):
        return self._body


def test_payload_uses_json_schema_when_response_schema_given():
    body = ai_providers._payload("m", "p", json_mode=False, stream=False, response_schema=_Choice)
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "_Choice", "schema": _Choice.model_json_schema()},
    }


def test_payload_uses_json_object_for_loose_json_mode():
    body = ai_providers._payload("m", "p", json_mode=True, stream=False)
    assert body["response_format"] == {"type": "json_object"}


def test_payload_has_no_response_format_when_neither_given():
    body = ai_providers._payload("m", "p", json_mode=False, stream=False)
    assert "response_format" not in body


def test_payload_response_schema_takes_precedence_over_json_mode():
    body = ai_providers._payload("m", "p", json_mode=True, stream=False, response_schema=_Choice)
    assert body["response_format"]["type"] == "json_schema"


def test_generate_retries_loose_on_schema_400(monkeypatch, caplog):
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            return _FakeResponse(400)
        return _FakeResponse(200)

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    result = ai_providers.generate("gateway", "m", "prompt", response_schema=_Choice)

    assert result == "ok"
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert "response_format" not in calls[1] or calls[1]["response_format"]["type"] == "json_object"
    assert any("스키마 제약 생성 실패" in r.message for r in caplog.records)


def test_generate_does_not_retry_on_401(monkeypatch):
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(json)
        return _FakeResponse(401)

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        ai_providers.generate("gateway", "m", "prompt", response_schema=_Choice)
    assert len(calls) == 1


def test_generate_retries_loose_on_timeout(monkeypatch):
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(json)
        if len(calls) == 1:
            raise httpx.TimeoutException("timed out")
        return _FakeResponse(200)

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    result = ai_providers.generate("gateway", "m", "prompt", response_schema=_Choice)
    assert result == "ok"
    assert len(calls) == 2


def test_generate_without_schema_does_not_retry(monkeypatch):
    """response_schema가 없으면(json_mode만 또는 둘 다 없음) 재시도 로직 자체가
    적용되지 않는다 — 기존 동작(1회 실패 시 그대로 예외 전파) 그대로."""
    calls = []

    def fake_post(url, *, headers, json, timeout):
        calls.append(json)
        return _FakeResponse(500)

    monkeypatch.setattr(ai_providers.httpx, "post", fake_post)
    with pytest.raises(httpx.HTTPStatusError):
        ai_providers.generate("gateway", "m", "prompt", json_mode=True)
    assert len(calls) == 1
