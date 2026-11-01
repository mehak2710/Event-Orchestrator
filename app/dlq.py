"""Dead letter queue: a separate Redis stream with failure details + replay."""
import time

from pydantic import ValidationError

from . import idempotency, streams
from .schemas import Event

# Entries rejected at the door. Replaying them would bypass validation or the
# signature check, so they are never replayable.
NON_REPLAYABLE = {"invalid_signature", "invalid_payload"}


async def send_raw(
    r, s, event_id: str, event_type: str, payload: str, reason: str, attempts: int, error: str
) -> str:
    return await r.xadd(
        s.dlq_key,
        {
            "event_id": event_id,
            "type": event_type,
            "payload": payload[:4000],
            "reason": reason,
            "attempts": str(attempts),
            "error": error[:1000],
            "failed_at": str(int(time.time())),
        },
    )


async def send(r, s, event: Event, reason: str, attempts: int, error: str) -> str:
    return await send_raw(
        r, s, event.id, event.type, event.model_dump_json(), reason, attempts, error
    )


async def list_entries(r, s, count: int = 100) -> list[dict]:
    rows = await r.xrevrange(s.dlq_key, count=count)
    return [{"entry_id": entry_id, **fields} for entry_id, fields in rows]


async def replay(r, s, entry_id: str) -> bool:
    """Re-queue a dead-lettered event. Raises ValueError if it must not be replayed."""
    rows = await r.xrange(s.dlq_key, min=entry_id, max=entry_id)
    if not rows:
        return False
    _, fields = rows[0]
    if fields.get("reason") in NON_REPLAYABLE:
        raise ValueError(f"entries rejected as {fields['reason']} cannot be replayed")
    try:
        event = Event.model_validate_json(fields["payload"])
    except ValidationError as exc:
        raise ValueError("payload is not a valid event") from exc
    await idempotency.release(r, event.id)
    await streams.enqueue(r, s, event, source="dlq-replay")
    await r.xdel(s.dlq_key, entry_id)
    return True


async def replay_all(r, s) -> int:
    count = 0
    for entry in await list_entries(r, s, count=1000):
        try:
            if await replay(r, s, entry["entry_id"]):
                count += 1
        except ValueError:
            continue
    return count