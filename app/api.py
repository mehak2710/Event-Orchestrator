"""FastAPI receiver: verify signature, validate, dedupe, enqueue. Plus admin API."""
import asyncio
import hmac
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError

from . import audit, dlq, streams
from .config import get_settings
from .schemas import Event
from .security import verify_signature


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    app.state.redis = redis.from_url(s.redis_url, decode_responses=True)
    await streams.ensure_group(app.state.redis, s)
    yield
    await app.state.redis.aclose()


app = FastAPI(title="Event Orchestrator", lifespan=lifespan)


@app.get("/health")
async def health(request: Request):
    await request.app.state.redis.ping()
    return {"status": "ok"}


@app.post("/webhooks/{source}", status_code=202)
async def receive(
    source: str, request: Request, x_signature: str | None = Header(default=None)
):
    s = get_settings()
    r = request.app.state.redis
    body = await request.body()

    if not verify_signature(body, x_signature, s.webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        event = Event.model_validate_json(body)
    except ValidationError:
        raise HTTPException(status_code=422, detail="invalid event payload")

    # Fast-path dedupe at the edge; the worker still enforces idempotency.
    fresh = await r.set(f"enq:{event.id}", "1", nx=True, ex=s.idempotency_ttl_seconds)
    if not fresh:
        await r.hincrby(streams.METRICS, "edge_duplicates", 1)
        return {"status": "duplicate", "event_id": event.id}

    try:
        await streams.enqueue(r, s, event, source=source)
    except Exception:
        await r.delete(f"enq:{event.id}")
        raise HTTPException(status_code=503, detail="queue unavailable")

    await r.hincrby(streams.METRICS, "received", 1)
    return {"status": "queued", "event_id": event.id}


async def require_admin(x_admin_token: str | None = Header(default=None)):
    token = get_settings().admin_token
    if token and not hmac.compare_digest(x_admin_token or "", token):
        raise HTTPException(status_code=401, detail="admin token required")


admin = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@admin.get("/stats")
async def stats(request: Request):
    s, r = get_settings(), request.app.state.redis
    pending = await r.xpending(s.stream_key, s.consumer_group)
    return {
        "metrics": await r.hgetall(streams.METRICS),
        "stream_length": await r.xlen(s.stream_key),
        "pending": pending["pending"] if pending else 0,
        "dlq_length": await r.xlen(s.dlq_key),
    }


@admin.get("/events")
async def events(limit: int = 50):
    return await asyncio.to_thread(audit.recent, get_settings().db_path, limit)


@admin.get("/dlq")
async def dlq_list(request: Request):
    return await dlq.list_entries(request.app.state.redis, get_settings())


@admin.post("/dlq/replay-all")
async def dlq_replay_all(request: Request):
    count = await dlq.replay_all(request.app.state.redis, get_settings())
    return {"replayed": count}


@admin.post("/dlq/{entry_id}/replay")
async def dlq_replay(entry_id: str, request: Request):
    try:
        found = await dlq.replay(request.app.state.redis, get_settings(), entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not found:
        raise HTTPException(status_code=404, detail="entry not found")
    return {"replayed": entry_id}


@admin.get("/n8n")
async def n8n_status():
    """n8n health, plus execution history when N8N_API_KEY is configured."""
    s = get_settings()
    out = {"healthy": False, "api_configured": bool(s.n8n_api_key), "executions": [], "error": None}
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            health = await client.get(f"{s.n8n_base_url}/healthz")
            out["healthy"] = health.status_code == 200
        except httpx.HTTPError as exc:
            out["error"] = f"n8n unreachable: {exc!r}"
            return out
        if s.n8n_api_key:
            try:
                resp = await client.get(
                    f"{s.n8n_base_url}/api/v1/executions",
                    params={"limit": 20},
                    headers={"X-N8N-API-KEY": s.n8n_api_key},
                )
                resp.raise_for_status()
                for item in resp.json().get("data", []):
                    status = item.get("status") or ("success" if item.get("finished") else "unknown")
                    out["executions"].append(
                        {
                            "id": item.get("id"),
                            "status": status,
                            "mode": item.get("mode"),
                            "started": item.get("startedAt"),
                            "stopped": item.get("stoppedAt"),
                        }
                    )
            except (httpx.HTTPError, ValueError) as exc:
                out["error"] = f"executions API failed: {exc}"
    return out


app.include_router(admin)