from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)


class TransientError(Exception):
    """Worth retrying: timeouts, connection errors, 5xx, 429."""


class PermanentError(Exception):
    """Retrying will not help: 4xx responses, bad config."""


async def run_with_retry(fn, settings, on_retry=None):
    """Run async `fn` retrying TransientError with exponential backoff + jitter.

    Returns (result, attempts). On failure the raised exception gets an
    `.attempts` attribute so the caller can log it to the DLQ.
    """
    retrying = AsyncRetrying(
        stop=stop_after_attempt(settings.max_attempts),
        wait=wait_random_exponential(
            multiplier=settings.retry_base_seconds, max=settings.retry_max_seconds
        ),
        retry=retry_if_exception_type(TransientError),
        before_sleep=on_retry,
        reraise=True,
    )
    try:
        result = await retrying(fn)
    except Exception as exc:
        exc.attempts = retrying.statistics.get("attempt_number", 1)
        raise
    return result, retrying.statistics["attempt_number"]