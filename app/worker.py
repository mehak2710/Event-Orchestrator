"""Stream consumer: idempotency check -> execute with retries -> ack or DLQ."""
import asyncio
import logging
import os
import socket

import redis.asyncio as redis
from pydantic import ValidationError

from . import alerts, audit, dlq, idempotency, streams
from .config import get_settings
from .executor import execute_event
from .retry import PermanentError, TransientError, run_with_retry
from .router import resolve_executor
from .schemas import Event

log = logging.getLogger("worker")


async def _audit(s, *args, **kwargs):
    await asyncio.to_thread(audit.record, s.db_path, *args, **kwargs)


def _summarize(result) -> str | None:
    """Short human-readable outcome shown on the dashboard."""
    if isinstance(result, dict):
        text = result.get("summary") or result.get("result")
        if text:
            return str(text)[:200]
    return None


async def process(r, s, msg_id, fields, execute=execute_event) -> str:
    """Handle one stream message. Returns the outcome (used by tests/logs)."""
    try:
        event = Event.model_validate_json(fields["payload"])
    except (KeyError, ValidationError):
        log.error("malformed message %s, dropping", msg_id)
        await r.hincrby(streams.METRICS, "malformed", 1)
        await streams.ack(r, s, msg_id)
        return "malformed"

    executor = resolve_executor(event.type)
    state = await idempotency.claim(r, s, event.id)

    if state == "done":
        log.info("duplicate %s ignored", event.id)
        await r.hincrby(streams.METRICS, "duplicates", 1)
        await _audit(s, event.id, event.type, "duplicate", executor=executor)
        await streams.ack(r, s, msg_id)
        return "duplicate"

    if state == "processing":
        # Another worker holds the claim. Leave the message pending: it is
        # reclaimed later and becomes 'done' (duplicate) or is retried if
        # the other worker crashed.
        return "skipped"

    async def on_retry(retry_state):
        await r.hincrby(streams.METRICS, "retries", 1)
        log.warning("retrying %s (attempt %s)", event.id, retry_state.attempt_number)

    async def attempt():
        return await execute(event, s)

    try:
        result, attempts = await run_with_retry(attempt, s, on_retry)
    except Exception as exc:
        attempts = getattr(exc, "attempts", 1)
        if isinstance(exc, PermanentError):
            reason = "permanent_error"
        elif isinstance(exc, TransientError):
            reason = "retries_exhausted"
        else:
            reason = "unexpected_error"
        log.error("dead-lettering %s: %s (%s)", event.id, reason, exc)
        await idempotency.release(r, event.id)  # so a replay can run again
        await dlq.send(r, s, event, reason, attempts, str(exc))
        await r.hincrby(streams.METRICS, "dead_lettered", 1)
        await _audit(
            s, event.id, event.type, "dead_lettered", attempts, str(exc), executor
        )
        await streams.ack(r, s, msg_id)
        try:
            sent_via = await alerts.send_dead_letter_alert(s, event, reason, attempts, str(exc))
            log.info("dead-letter alert for %s sent via %s", event.id, sent_via)
        except Exception:
            log.exception("dead-letter alert failed for %s", event.id)
        return "dead_lettered"

    await idempotency.complete(r, s, event.id)
    await r.hincrby(streams.METRICS, "processed", 1)
    await _audit(
        s,
        event.id,
        event.type,
        "succeeded",
        attempts,
        executor=executor,
        result=_summarize(result),
    )
    await streams.ack(r, s, msg_id)
    log.info("processed %s in %s attempt(s)", event.id, attempts)
    return "succeeded"


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = get_settings()
    r = redis.from_url(s.redis_url, decode_responses=True)
    await streams.ensure_group(r, s)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    log.info("worker %s listening on %s", consumer, s.stream_key)

    while True:
        stale = await streams.reclaim_stale(r, s, consumer)
        fresh = await streams.read_batch(r, s, consumer, block_ms=2000)
        batch = stale + fresh
        if batch:
            await asyncio.gather(*(process(r, s, mid, f) for mid, f in batch))


if __name__ == "__main__":
    asyncio.run(main())