"""Queue listener: the second input next to webhooks.

Producers XADD messages to the inbound stream with two fields:
  payload    the event JSON (string)
  signature  sha256=<hmac of the payload>, same scheme as the webhook header

Valid messages are deduplicated and forwarded to the main stream. Messages
with a bad signature or invalid payload go to the dead letter queue so they
stay visible (they cannot be replayed).
"""
import asyncio
import logging
import os
import socket

import redis.asyncio as redis
from pydantic import ValidationError

from . import dlq, streams
from .config import get_settings
from .schemas import Event
from .security import verify_signature

log = logging.getLogger("listener")


async def process_message(r, s, msg_id, fields) -> str:
    async def done(outcome: str) -> str:
        await streams.ack(r, s, msg_id, stream=s.inbound_stream_key, group=s.inbound_group)
        return outcome

    payload = fields.get("payload", "")
    signature = fields.get("signature")

    if not verify_signature(payload.encode(), signature, s.webhook_secret):
        await dlq.send_raw(
            r, s, "unknown", "unknown", payload, "invalid_signature", 0, "signature check failed"
        )
        await r.hincrby(streams.METRICS, "rejected", 1)
        log.warning("rejected queue message %s: bad signature", msg_id)
        return await done("rejected")

    try:
        event = Event.model_validate_json(payload)
    except ValidationError as exc:
        await dlq.send_raw(
            r, s, "unknown", "unknown", payload, "invalid_payload", 0, str(exc)[:500]
        )
        await r.hincrby(streams.METRICS, "rejected", 1)
        log.warning("rejected queue message %s: invalid payload", msg_id)
        return await done("invalid")

    fresh = await r.set(f"enq:{event.id}", "1", nx=True, ex=s.idempotency_ttl_seconds)
    if not fresh:
        await r.hincrby(streams.METRICS, "edge_duplicates", 1)
        log.info("duplicate %s from queue ignored", event.id)
        return await done("duplicate")

    try:
        await streams.enqueue(r, s, event, source="queue")
    except Exception:
        await r.delete(f"enq:{event.id}")
        raise  # left unacknowledged: reclaimed and retried later

    await r.hincrby(streams.METRICS, "received", 1)
    log.info("queued %s from inbound stream", event.id)
    return await done("queued")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = get_settings()
    r = redis.from_url(s.redis_url, decode_responses=True)
    await streams.ensure_group(r, s, s.inbound_stream_key, s.inbound_group)
    await streams.ensure_group(r, s)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    log.info("listener %s consuming %s", consumer, s.inbound_stream_key)

    kwargs = {"stream": s.inbound_stream_key, "group": s.inbound_group}
    while True:
        try:
            stale = await streams.reclaim_stale(r, s, consumer, **kwargs)
            fresh = await streams.read_batch(r, s, consumer, block_ms=2000, **kwargs)
            for msg_id, fields in stale + fresh:
                await process_message(r, s, msg_id, fields)
        except Exception:
            log.exception("listener loop error, retrying in 2s")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())