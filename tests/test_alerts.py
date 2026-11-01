import json

import httpx

from app import alerts
from app.config import Settings
from app.schemas import Event

EVENT = Event(id="evt_9", type="order.created", data={})
SLACK = "https://hooks.slack.com/services/T000/B000/secret"
N8N = "http://n8n:5678/webhook/dead-letter"


def mock_http(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(
        alerts.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )


async def test_alert_goes_to_n8n_first(monkeypatch):
    calls = []

    def handler(request):
        calls.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200)

    mock_http(monkeypatch, handler)
    s = Settings(n8n_alert_url=N8N, slack_webhook_url=SLACK)
    assert await alerts.send_dead_letter_alert(s, EVENT, "retries_exhausted", 5, "n8n returned 500") == "n8n"
    assert len(calls) == 1 and calls[0][0] == N8N
    assert calls[0][1] == {
        "event_id": "evt_9",
        "type": "order.created",
        "reason": "retries_exhausted",
        "attempts": 5,
        "error": "n8n returned 500",
    }


async def test_falls_back_to_slack_when_n8n_is_down(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if str(request.url) == N8N:
            raise httpx.ConnectError("n8n is down")
        return httpx.Response(200, text="ok")

    mock_http(monkeypatch, handler)
    s = Settings(n8n_alert_url=N8N, slack_webhook_url=SLACK)
    assert await alerts.send_dead_letter_alert(s, EVENT, "retries_exhausted", 5, "n8n unreachable") == "slack"
    assert seen == [N8N, SLACK]


async def test_falls_back_when_n8n_alert_returns_error(monkeypatch):
    mock_http(monkeypatch, lambda r: httpx.Response(500 if str(r.url) == N8N else 200))
    s = Settings(n8n_alert_url=N8N, slack_webhook_url=SLACK)
    assert await alerts.send_dead_letter_alert(s, EVENT, "retries_exhausted", 5, "x") == "slack"


async def test_slack_only_when_no_n8n_alert_url(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200)

    mock_http(monkeypatch, handler)
    s = Settings(n8n_alert_url="", slack_webhook_url=SLACK)
    assert await alerts.send_dead_letter_alert(s, EVENT, "permanent_error", 1, "n8n returned 404") == "slack"
    assert "evt_9" in bodies[0]["text"] and "permanent_error" in bodies[0]["text"]


async def test_nothing_configured_sends_nothing(monkeypatch):
    mock_http(monkeypatch, lambda r: (_ for _ in ()).throw(AssertionError("no request expected")))
    s = Settings(n8n_alert_url="", slack_webhook_url="")
    assert await alerts.send_dead_letter_alert(s, EVENT, "x", 1, "y") is None


async def test_both_down_returns_none_and_does_not_raise(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("everything is down")

    mock_http(monkeypatch, handler)
    s = Settings(n8n_alert_url=N8N, slack_webhook_url=SLACK)
    assert await alerts.send_dead_letter_alert(s, EVENT, "x", 1, "y") is None


async def test_slack_control_characters_are_escaped(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200)

    mock_http(monkeypatch, handler)
    evil = Event(id="evt<!channel>&", type="order.created", data={})
    s = Settings(n8n_alert_url="", slack_webhook_url=SLACK)
    await alerts.send_dead_letter_alert(s, evil, "x", 1, "<!here> boom")
    assert "<!channel>" not in bodies[0]["text"] and "<!here>" not in bodies[0]["text"]
    assert "&lt;!channel&gt;&amp;" in bodies[0]["text"]


async def test_slack_url_is_never_logged(monkeypatch, caplog):
    def handler(request):
        raise httpx.ConnectError("down")

    mock_http(monkeypatch, handler)
    s = Settings(n8n_alert_url="", slack_webhook_url=SLACK)
    with caplog.at_level("WARNING"):
        await alerts.send_dead_letter_alert(s, EVENT, "x", 1, "y")
    assert "secret" not in caplog.text and "hooks.slack.com" in caplog.text