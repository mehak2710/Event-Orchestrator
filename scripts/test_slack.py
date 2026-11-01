"""Check your Slack webhook works, independent of n8n and the rest of the stack.

  python -m scripts.test_slack

Reads SLACK_WEBHOOK_URL from .env and posts one test message.
"""
import sys

import httpx

from app.config import get_settings

HINTS = {
    400: "Slack rejected the payload (invalid_payload).",
    403: "Slack refused the request (the app or webhook may have been revoked).",
    404: "Slack does not know this webhook URL (no_service). Copy it again from your Slack app.",
    410: "The channel for this webhook was archived (channel_is_archived).",
}


def main() -> None:
    url = get_settings().slack_webhook_url
    if not url:
        sys.exit("SLACK_WEBHOOK_URL is empty. Add it to .env first (see README, 'Set up Slack').")
    if not url.startswith("https://hooks.slack.com/"):
        print("Warning: this does not look like a Slack incoming webhook URL.")
    try:
        resp = httpx.post(
            url,
            json={"text": ":white_check_mark: Slack is connected to Event Orchestrator."},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        sys.exit(f"Could not reach Slack: {exc!r}")
    if resp.status_code == 200:
        print("OK: check your Slack channel for the test message.")
    else:
        print(f"Slack answered {resp.status_code}: {resp.text[:120]}")
        print(HINTS.get(resp.status_code, "See https://api.slack.com/messaging/webhooks"))
        sys.exit(1)


if __name__ == "__main__":
    main()