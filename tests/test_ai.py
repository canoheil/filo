from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from services import ai


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,expected,calls", [
    (200, {"choices": [{"message": {"content": " سلام "}}]}, "سلام", 1),
    (401, {"secret": "PRIVATE"}, "http_401", 1),
    (503, {"secret": "PRIVATE"}, "http_503", 2),
    (200, {"choices": []}, "invalid_response", 1),
    (200, {"choices": [{"message": {"content": " "}}]}, "invalid_response", 1),
])
async def test_ai_safe_errors_and_retry(monkeypatch, status, body, expected, calls):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(
        GEMMA_MODEL="test", GEMINI_API_KEY="PRIVATE", AI_ENDPOINT="https://example.test/PRIVATE"))
    monkeypatch.setattr(ai.asyncio, "sleep", AsyncMock())
    if status == 200 and expected == "سلام":
        assert await ai.generate_reply("PRIVATE", "PRIVATE") == expected
    else:
        with pytest.raises(ai.AIError) as error:
            await ai.generate_reply("PRIVATE", "PRIVATE")
        assert str(error.value) == expected
        assert "PRIVATE" not in str(error.value)
    assert len(requests) == calls


@pytest.mark.asyncio
async def test_history_is_between_system_and_current_message(monkeypatch):
    captured = []
    import json
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "پاسخ"}}]})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setattr(ai, "get_settings", lambda: SimpleNamespace(
        GEMMA_MODEL="test", GEMINI_API_KEY="key", AI_ENDPOINT="https://example.test"))
    await ai.generate_reply("rules", "current", history=[
        {"role": "user", "content": "previous"}, {"role": "assistant", "content": "sent"},
        {"role": "system", "content": "untrusted"}])
    assert captured[0]["messages"] == [
        {"role": "system", "content": "rules"}, {"role": "user", "content": "previous"},
        {"role": "assistant", "content": "sent"}, {"role": "user", "content": "current"}]
