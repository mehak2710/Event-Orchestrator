import json

import fakeredis.aioredis
import pytest

from app import dlq, streams
from app.config import Settings
from app.listener import process_message
from app.security import sign


@pytest.fixture
async def env():
    s = Settings(webhook_secret="k")
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await streams.ensure_group(r, s)
    await streams.ensure_group(r, s, s.inbound_stream_key, s.inbound_group)
    yield r, s
    await r.aclose()


def message(s, event_id="e1", secret=None, payload=None):
    payload = payload or json.dumps({"id": event_id, "type": "order.created", "data": {}})
    return {"payload": payload, "signature": sign(payload.encode(), secret or s.webhook_secret)}


async def test_valid_message_reaches_main_stream(env):
    r, s = env
    assert await process_message(r, s, "1-0", message(s)) == "queued"
    assert await r.xlen(s.stream_key) == 1
    assert await r.hget(streams.METRICS, "received") == "1"


async def test_duplicate_message_is_not_queued_twice(env):
    r, s = env
    assert await process_message(r, s, "1-0", message(s)) == "queued"
    assert await process_message(r, s, "2-0", message(s)) == "duplicate"
    assert await r.xlen(s.stream_key) == 1


async def test_bad_signature_is_rejected_to_dlq(env):
    r, s = env
    assert await process_message(r, s, "1-0", message(s, secret="wrong")) == "rejected"
    assert await r.xlen(s.stream_key) == 0
    (entry,) = await dlq.list_entries(r, s)
    assert entry["reason"] == "invalid_signature"


async def test_invalid_payload_is_rejected_to_dlq(env):
    r, s = env
    bad = json.dumps({"id": "", "type": "order.created"})
    assert await process_message(r, s, "1-0", message(s, payload=bad)) == "invalid"
    assert await r.xlen(s.stream_key) == 0
    (entry,) = await dlq.list_entries(r, s)
    assert entry["reason"] == "invalid_payload"


async def test_rejected_entries_cannot_be_replayed(env):
    r, s = env
    await process_message(r, s, "1-0", message(s, secret="wrong"))
    (entry,) = await dlq.list_entries(r, s)
    with pytest.raises(ValueError):
        await dlq.replay(r, s, entry["entry_id"])
    assert await r.xlen(s.stream_key) == 0  # nothing was injected
    assert await dlq.replay_all(r, s) == 0