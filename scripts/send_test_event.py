"""Send signed test events to the webhook receiver.

Examples (run from the project root):
  python -m scripts.send_test_event
  python -m scripts.send_test_event --type user.signup
  python -m scripts.send_test_event --id evt_dup --repeat 3     # duplicate delivery
  python -m scripts.send_test_event --fail                      # forces n8n to return 500
"""
import argparse
import json
import uuid

import httpx

from app.config import get_settings
from app.security import sign


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000/webhooks/demo")
    p.add_argument("--type", default="order.created")
    p.add_argument("--id", default=None)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--fail", action="store_true", help="ask n8n to fail this event")
    p.add_argument("--bad-signature", action="store_true")
    args = p.parse_args()

    secret = get_settings().webhook_secret
    event_id = args.id or f"evt_{uuid.uuid4().hex[:8]}"
    payload = {
        "id": event_id,
        "type": args.type,
        "data": {"amount": 42, "simulate_failure": args.fail},
    }
    body = json.dumps(payload).encode()
    signature = "sha256=bad" if args.bad_signature else sign(body, secret)

    for i in range(args.repeat):
        resp = httpx.post(
            args.url,
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": signature},
            timeout=10,
        )
        print(f"[{i + 1}/{args.repeat}] {event_id} -> {resp.status_code} {resp.text}")


if __name__ == "__main__":
    main()