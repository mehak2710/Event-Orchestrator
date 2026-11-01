"""Thin helpers around Redis Streams with consumer groups."""
from redis.exceptions import ResponseError

METRICS = "events:metrics"


async def ensure_group(r, s, stream=None, group=None) -> None:
    try:
        await r.xgroup_create(
            stream or s.stream_key, group or s.consumer_group, id="0", mkstream=True
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def enqueue(r, s, event, source: str = "webhook") -> str:
    return await r.xadd(
        s.stream_key,
        {"payload": event.model_dump_json(), "source": source},
        maxlen=s.stream_maxlen,
        approximate=True,
    )


async def read_batch(
    r, s, consumer: str, block_ms: int = 2000, count: int = 10, stream=None, group=None
):
    stream = stream or s.stream_key
    group = group or s.consumer_group
    resp = await r.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
    if not resp:
        return []
    return [(mid, fields) for _, msgs in resp for mid, fields in msgs]


async def reclaim_stale(
    r, s, consumer: str, min_idle_ms: int = 60000, count: int = 10, stream=None, group=None
):
    """Pick up messages another (crashed) consumer never acknowledged."""
    res = await r.xautoclaim(
        stream or s.stream_key,
        group or s.consumer_group,
        consumer,
        min_idle_time=min_idle_ms,
        start_id="0-0",
        count=count,
    )
    return [(mid, fields) for mid, fields in res[1] if fields]


async def ack(r, s, msg_id: str, stream=None, group=None) -> None:
    await r.xack(stream or s.stream_key, group or s.consumer_group, msg_id)