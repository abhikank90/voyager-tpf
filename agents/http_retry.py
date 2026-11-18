"""§8 retries for external HTTP calls (SerpApi, OpenWeather, Open-Meteo).

One helper for the live-inventory requests: up to ``settings.llm_max_retries``
(3) retries with exponential backoff and jitter, on timeouts, transport errors,
429 and 5xx only. Other 4xx are definitive and returned immediately (no retry),
so callers still see their ``raise_for_status()``.
"""

from __future__ import annotations

import asyncio
import random

import httpx

_RETRYABLE_STATUS = {429} | set(range(500, 600))

_sleep = asyncio.sleep  # module-level so tests can stub the backoff wait


def _backoff(attempt: int, base: float = 1.0) -> float:
    """Exponential backoff with jitter: base * 2^attempt + U(0, 0.5) seconds."""
    return base * (2 ** attempt) + random.uniform(0, 0.5)


async def request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_retries: int | None = None,
    **kwargs,
) -> httpx.Response:
    """Perform ``client.<method>`` with up to ``max_retries`` retries.

    ``max_retries`` defaults to ``settings.llm_max_retries``. Retried failures:
    timeouts, transport errors, 429 and 5xx. A 429/5xx response is drained and
    re-raised as ``httpx.HTTPStatusError`` so callers handle it uniformly.
    """
    if max_retries is None:
        from config.settings import get_settings

        max_retries = get_settings().llm_max_retries

    requester = getattr(client, method.lower(), None)
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            if requester is not None:
                response = await requester(url, **kwargs)
            else:
                response = await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
        else:
            status = getattr(response, "status_code", None)
            if status is None or status not in _RETRYABLE_STATUS:
                return response
            await response.aread()  # drain the body so the connection is reusable
            last_exc = httpx.HTTPStatusError(
                f"{status} {getattr(response, 'reason_phrase', '')}",
                request=response.request,
                response=response,
            )
        if attempt < max_retries:
            await _sleep(_backoff(attempt))
    assert last_exc is not None
    raise last_exc
