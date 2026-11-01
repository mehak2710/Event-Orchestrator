"""Push a signed event onto the inbound Redis stream (the queue input).

  python -m scripts.push_queue_event
  python -m scripts.push_queue_event --type user.signup
  python -m scripts.push_queue_event --bad-signature     # rejected to the DLQ
  python -m scripts.push_queue_event --invalid           # invalid payload, rejected to the DLQ
"""
import argparse
import json
import uuid

import redis

from app.config import get_settings
from app.security import sign


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--type", default="order.created")
    p.add_argument("--id", default=None)
    p.add_argument("--bad-signature", action="store_true")
    p.add_argument("--invalid", action="store_true")
    args = p.parse_args()

    s = get_settings()
    event_id = args.id or f"evt_{uuid.uuid4().hex[:8]}"
    payload = json.dumps(
        {"id": "" if args.invalid else event_id, "type": args.type, "data": {"amount": 7}}
    )
    signature = "sha256=bad" if args.bad_signature else sign(payload.encode(), s.webhook_secret)

    r = redis.Redis.from_url(s.redis_url, decode_responses=True)
    msg_id = r.xadd(s.inbound_stream_key, {"payload": payload, "signature": signature})
    print(f"pushed {event_id} to {s.inbound_stream_key} as {msg_id}")


if __name__ == "__main__":
    main()