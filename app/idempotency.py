"""Atomic claim-before-run tracking of event IDs in Redis."""


def _key(event_id: str) -> str:
    return f"idem:{event_id}"


async def claim(r, settings, event_id: str) -> str:
    """Try to claim an event. Returns 'claimed', 'done' or 'processing'."""
    for _ in range(2):
        ok = await r.set(
            _key(event_id), "processing", nx=True, ex=settings.processing_lock_seconds
        )
        if ok:
            return "claimed"
        state = await r.get(_key(event_id))
        if state is not None:
            return "done" if state == "done" else "processing"
    return "processing"


async def complete(r, settings, event_id: str) -> None:
    await r.set(_key(event_id), "done", ex=settings.idempotency_ttl_seconds)


async def release(r, event_id: str) -> None:
    await r.delete(_key(event_id))