"""Best-effort dead-letter alerts.

When an event is dead-lettered, tell a human. The alert goes to an n8n webhook
(which posts to Slack). If n8n is the thing that is down, the alert falls back
to posting straight to the Slack webhook, so an outage still produces a message.
Alerting never raises: a failed alert must not break event processing.
"""
import logging
from urllib.parse import urlparse

import httpx

log = logging.getLogger("alerts")

DASHBOARD_URL = "http://localhost:8501"


def _escape(text) -> str:
    """Escape the three characters Slack treats specially in message text."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _host(url: str) -> str:
    # Slack webhook URLs are secrets, so only ever log the host.
    return urlparse(url).netloc or "unknown-host"


async def _post(url: str, payload: dict, timeout: float = 5) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
    except httpx.HTTPError as exc:
        log.warning("alert POST to %s failed: %r", _host(url), exc)
        return False
    if resp.status_code >= 400:
        log.warning("alert POST to %s returned %s", _host(url), resp.status_code)
        return False
    return True


async def send_dead_letter_alert(s, event, reason: str, attempts: int, error: str):
    """Returns 'n8n', 'slack' (direct fallback), or None if nothing was sent."""
    payload = {
        "event_id": event.id,
        "type": event.type,
        "reason": reason,
        "attempts": attempts,
        "error": error[:500],
    }
    if s.n8n_alert_url and await _post(s.n8n_alert_url, payload):
        return "n8n"

    if s.slack_webhook_url:
        text = (
            f":rotating_light: *Event dead-lettered*\n"
            f"Event `{_escape(event.id)}` ({_escape(event.type)})\n"
            f"Reason: *{_escape(reason)}* after {attempts} attempt(s)\n"
            f"Last error: `{_escape(error[:300])}`\n"
            f"Replay it from the dashboard: {DASHBOARD_URL} "
            f"(sent directly because the n8n alert workflow was unavailable)"
        )
        if await _post(s.slack_webhook_url, {"text": text}):
            return "slack"
    return None