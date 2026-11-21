"""§8 external-HTTP retries: retry then succeed, give up, no 404 retry, capture raises."""
import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agents import http_retry


async def _no_sleep(_):
    pass


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_retries_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr(http_retry, "_sleep", _no_sleep)
    calls = []

    async def handler(request):
        calls.append(request.url)
        if len(calls) <= 2:
            return httpx.Response(500, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    async def run():
        async with _client(handler) as client:
            return await http_retry.request_with_retries(client, "GET", "https://x", max_retries=3)

    resp = asyncio.run(run())
    assert resp.status_code == 200
    assert len(calls) == 3


def test_retries_transport_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(http_retry, "_sleep", _no_sleep)
    calls = []

    async def handler(request):
        calls.append(request.url)
        if len(calls) == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, request=request)

    async def run():
        async with _client(handler) as client:
            return await http_retry.request_with_retries(client, "GET", "https://x", max_retries=3)

    resp = asyncio.run(run())
    assert resp.status_code == 200
    assert len(calls) == 2


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(http_retry, "_sleep", _no_sleep)
    calls = []

    async def handler(request):
        calls.append(1)
        return httpx.Response(500, request=request)

    async def run():
        async with _client(handler) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await http_retry.request_with_retries(client, "GET", "https://x", max_retries=3)

    asyncio.run(run())
    assert len(calls) == 4  # initial attempt + 3 retries


def test_no_retry_on_404(monkeypatch):
    monkeypatch.setattr(http_retry, "_sleep", _no_sleep)
    calls = []

    async def handler(request):
        calls.append(1)
        return httpx.Response(404, request=request)

    async def run():
        async with _client(handler) as client:
            return await http_retry.request_with_retries(client, "GET", "https://x", max_retries=3)

    resp = asyncio.run(run())
    assert resp.status_code == 404
    assert len(calls) == 1


def test_weather_capture_raises_where_mock_falls_back():
    from agents.weather_agent import WeatherAgent

    agent = WeatherAgent()

    async def fail(client, method, url, **kwargs):
        raise httpx.ConnectError("unreachable")

    with patch("agents.weather_agent.request_with_retries", fail):
        with pytest.raises(httpx.ConnectError):
            asyncio.run(agent._fetch_climatology("Greece", "july", "api-key"))

    assert agent._historical_climate("Greece", "july")["source"] == "historical_average"


def test_weather_climatology_falls_back_on_unresolvable_destination():
    from agents.weather_agent import WeatherAgent

    agent = WeatherAgent()

    async def empty_geo(client, method, url, **kwargs):
        resp = MagicMock()
        resp.json.return_value = []
        return resp

    with patch("agents.weather_agent.request_with_retries", empty_geo):
        out = asyncio.run(agent._fetch_climatology("Nowhere", "july", "api-key"))
    assert out["source"] == "historical_average_fallback"
