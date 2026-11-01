import fakeredis.aioredis
import pytest

from app import idempotency
from app.config import Settings


@pytest.fixture
async def r():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


async def test_claim_is_exclusive(r):
    s = Settings()
    assert await idempotency.claim(r, s, "e1") == "claimed"
    assert await idempotency.claim(r, s, "e1") == "processing"


async def test_completed_event_reports_done(r):
    s = Settings()
    await idempotency.claim(r, s, "e1")
    await idempotency.complete(r, s, "e1")
    assert await idempotency.claim(r, s, "e1") == "done"


async def test_release_allows_reclaim(r):
    s = Settings()
    await idempotency.claim(r, s, "e1")
    await idempotency.release(r, "e1")
    assert await idempotency.claim(r, s, "e1") == "claimed"