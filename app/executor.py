"""Workflow executors. n8n is the primary one; Groq handles AI-only events."""
import json

import httpx

from .retry import PermanentError, TransientError
from .router import resolve_executor

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _check(resp: httpx.Response, who: str) -> None:
    code = resp.status_code
    if code >= 500 or code in (408, 429):
        raise TransientError(f"{who} returned {code}")
    if code >= 400:
        raise PermanentError(f"{who} returned {code}: {resp.text[:200]}")


async def run_n8n(event, s) -> dict:
    try:
        async with httpx.AsyncClient(timeout=s.n8n_timeout_seconds) as client:
            resp = await client.post(
                s.n8n_webhook_url,
                json=event.model_dump(),
                headers={"X-Event-Id": event.id},
            )
    except httpx.TransportError as exc:
        raise TransientError(f"n8n unreachable: {exc!r}") from exc
    _check(resp, "n8n")
    try:
        body = resp.json()
    except ValueError:
        body = {}
    result = body.get("result") if isinstance(body, dict) else None
    return {"status_code": resp.status_code, "result": result}


async def run_groq(event, s) -> dict:
    if not s.groq_api_key:
        raise PermanentError("GROQ_API_KEY is not set")
    prompt = f"Summarize this event in one sentence: {json.dumps(event.model_dump())}"
    body = {
        "model": s.groq_model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 512,  # reasoning models spend part of this on thinking
    }
    if s.groq_model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                GROQ_URL,
                json=body,
                headers={"Authorization": f"Bearer {s.groq_api_key}"},
            )
    except httpx.TransportError as exc:
        raise TransientError(f"groq unreachable: {exc!r}") from exc
    _check(resp, "groq")
    try:
        content = resp.json()["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, ValueError) as exc:
        raise TransientError(f"groq returned an unexpected body: {exc!r}") from exc
    if not content.strip():
        raise TransientError("groq returned empty content")
    return {"summary": content.strip()}


async def execute_event(event, s) -> dict:
    if resolve_executor(event.type) == "groq":
        return await run_groq(event, s)
    return await run_n8n(event, s)