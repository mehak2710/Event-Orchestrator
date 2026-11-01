import json

import httpx
import pytest

from app import executor
from app.config import Settings
from app.retry import PermanentError, TransientError
from app.schemas import Event

EVENT = Event(id="evt_1", type="order.created", data={"amount": 5})


def mock_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        executor.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


async def test_n8n_success_sends_event_id_header(monkeypatch):
    seen = {}

    def handler(request):
        seen["event_id"] = request.headers["x-event-id"]
        return httpx.Response(200, json={"status": "ok", "result": "order.created handled"})

    mock_http(monkeypatch, handler)
    out = await executor.run_n8n(EVENT, Settings())
    assert seen["event_id"] == "evt_1"
    assert out["result"] == "order.created handled"


@pytest.mark.parametrize(
    "code,expected",
    [
        (500, TransientError),
        (503, TransientError),
        (429, TransientError),
        (408, TransientError),
        (400, PermanentError),
        (404, PermanentError),
    ],
)
async def test_n8n_status_codes_are_classified(monkeypatch, code, expected):
    mock_http(monkeypatch, lambda request: httpx.Response(code))
    with pytest.raises(expected):
        await executor.run_n8n(EVENT, Settings())


async def test_n8n_unreachable_is_transient(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("n8n is down")

    mock_http(monkeypatch, handler)
    with pytest.raises(TransientError):
        await executor.run_n8n(EVENT, Settings())


async def test_groq_requires_api_key():
    with pytest.raises(PermanentError):
        await executor.run_groq(EVENT, Settings(groq_api_key=""))


async def test_groq_success_builds_the_right_request(monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": " An order was created. "}}]}
        )

    mock_http(monkeypatch, handler)
    s = Settings(groq_api_key="gsk_test", groq_model="openai/gpt-oss-20b")
    out = await executor.run_groq(EVENT, s)

    assert out == {"summary": "An order was created."}
    assert seen["auth"] == "Bearer gsk_test"
    assert seen["body"]["model"] == "openai/gpt-oss-20b"
    assert seen["body"]["reasoning_effort"] == "low"


async def test_groq_skips_reasoning_effort_for_other_models(monkeypatch):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    mock_http(monkeypatch, handler)
    await executor.run_groq(EVENT, Settings(groq_api_key="k", groq_model="some/other-model"))
    assert "reasoning_effort" not in seen["body"]


async def test_groq_empty_content_is_transient(monkeypatch):
    mock_http(
        monkeypatch,
        lambda request: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
    )
    with pytest.raises(TransientError):
        await executor.run_groq(EVENT, Settings(groq_api_key="k"))


async def test_groq_rate_limit_is_transient(monkeypatch):
    mock_http(monkeypatch, lambda request: httpx.Response(429))
    with pytest.raises(TransientError):
        await executor.run_groq(EVENT, Settings(groq_api_key="k"))


async def test_ai_events_route_to_groq(monkeypatch):
    calls = []

    async def fake_groq(event, s):
        calls.append("groq")
        return {"summary": "x"}

    async def fake_n8n(event, s):
        calls.append("n8n")
        return {}

    monkeypatch.setattr(executor, "run_groq", fake_groq)
    monkeypatch.setattr(executor, "run_n8n", fake_n8n)
    await executor.execute_event(Event(id="1", type="ai.summarize"), Settings())
    await executor.execute_event(Event(id="2", type="order.created"), Settings())
    assert calls == ["groq", "n8n"]