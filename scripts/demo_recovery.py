"""Recovery demo: n8n goes down, an event is dead-lettered, n8n returns, replay succeeds.

Run from the project root while the stack is up (and the n8n workflow is published):
  python -m scripts.demo_recovery

Screen-record the dashboard (http://localhost:8501) while it runs.
"""
import json
import subprocess
import sys
import time
import uuid

import httpx

from app.config import get_settings
from app.security import sign

API = "http://localhost:8000"
N8N_HEALTH = "http://localhost:5678/healthz"
N8N_WEBHOOK = "http://localhost:5678/webhook/events"


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True)


def wait_for(check, timeout: int, label: str, interval: int = 3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    sys.exit(f"Timed out waiting for: {label}")


def n8n_healthy() -> bool:
    try:
        return httpx.get(N8N_HEALTH, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def workflow_registered() -> bool:
    """A failure-flagged probe returns 500 once the workflow is live, 404 before."""
    probe = {"id": "probe", "type": "probe", "data": {"simulate_failure": True}}
    try:
        return httpx.post(N8N_WEBHOOK, json=probe, timeout=5).status_code != 404
    except httpx.HTTPError:
        return False


def main() -> None:
    s = get_settings()
    admin = {"X-Admin-Token": s.admin_token}
    event_id = f"evt_recover_{uuid.uuid4().hex[:6]}"
    body = json.dumps({"id": event_id, "type": "order.created", "data": {"amount": 42}}).encode()

    print("1/6 stopping n8n ...")
    compose("stop", "n8n")

    print(f"2/6 sending {event_id} ...")
    resp = httpx.post(
        f"{API}/webhooks/demo",
        content=body,
        headers={"Content-Type": "application/json", "X-Signature": sign(body, s.webhook_secret)},
        timeout=10,
    )
    resp.raise_for_status()

    print("3/6 waiting for the retries to run out and the event to reach the DLQ ...")

    def in_dlq():
        rows = httpx.get(f"{API}/admin/dlq", headers=admin, timeout=5).json()
        return next((row for row in rows if row["event_id"] == event_id), None)

    entry = wait_for(in_dlq, 240, "event in the dead letter queue")
    print(f"    dead-lettered: {entry['reason']} after {entry['attempts']} attempts")

    print("4/6 starting n8n ...")
    compose("start", "n8n")
    wait_for(n8n_healthy, 180, "n8n to become healthy")
    wait_for(workflow_registered, 120, "the published workflow to register its webhook")

    print("5/6 replaying from the DLQ ...")
    httpx.post(
        f"{API}/admin/dlq/{entry['entry_id']}/replay", headers=admin, timeout=10
    ).raise_for_status()

    def succeeded():
        rows = httpx.get(f"{API}/admin/events", params={"limit": 50}, headers=admin, timeout=5).json()
        return any(r["event_id"] == event_id and r["status"] == "succeeded" for r in rows)

    wait_for(succeeded, 120, "the replayed event to succeed")
    print("6/6 done: the replayed event succeeded after n8n recovered.")


if __name__ == "__main__":
    main()