"""Tests for connection retry behavior."""

import asyncio
from typing import Self
from unittest.mock import AsyncMock

from aiohttp.client_exceptions import ClientConnectorError
import pytest

from my_pv import connection


class _Response:
    """Minimal aiohttp response context manager."""

    content_type = "application/json"
    reason = "OK"
    status = 200

    def __init__(self, body: str) -> None:
        self._body = body

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def text(self) -> str:
        return self._body


class _Session:
    """Session that refuses the first connection and accepts the retry."""

    closed = False

    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls += 1
        if self.calls == 1:
            raise ClientConnectorError(None, ConnectionRefusedError(111, "refused"))
        return _Response('{"value": 42}')


class _OpenSession:
    """Session that briefly refuses the initial device identification request."""

    closed = False

    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls += 1
        if self.calls == 1:
            raise ClientConnectorError(None, ConnectionRefusedError(111, "refused"))
        if url.endswith("/mypv_dev.jsn"):
            return _Response('{"sn": "1"}')
        return _Response("{}")


@pytest.fixture(autouse=True)
def no_refused_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry refused connects without waiting in tests."""
    monkeypatch.setattr(connection, "_REFUSED_DELAY", 0)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


async def test_open_retries_a_briefly_refused_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A brief refusal fetching mypv_dev.jsn does not block initial setup."""
    session = _OpenSession()
    monkeypatch.setattr(connection, "ClientSession", lambda **kwargs: session)
    device_connection = connection.MyPVHTTPConnection("device.local")

    assert await device_connection.open()
    assert device_connection.mypv_dev == {"sn": "1"}
    assert session.calls == 3


async def test_get_retries_a_briefly_refused_connection() -> None:
    """A refused connection after Connection: close succeeds on retry."""
    session = _Session()
    device_connection = connection.MyPVHTTPConnection("device.local", session=session)
    device_connection._connected = True
    device_connection._setup_url = "http://device.local/setup.jsn"

    assert await device_connection.fetch_setup() == {"value": 42}
    assert session.calls == 2


async def test_retry_refused_does_not_retry_other_connection_errors() -> None:
    """Errors other than ConnectionRefusedError fail without retries."""
    calls = 0

    async def request() -> None:
        nonlocal calls
        calls += 1
        raise ClientConnectorError(None, OSError(113, "no route"))

    with pytest.raises(ClientConnectorError):
        await connection._retry_refused(request)
    assert calls == 1


async def test_retry_refused_stops_after_configured_attempts() -> None:
    """A persistent refusal eventually propagates to the caller."""
    calls = 0

    async def request() -> None:
        nonlocal calls
        calls += 1
        raise ClientConnectorError(None, ConnectionRefusedError(111, "refused"))

    with pytest.raises(ClientConnectorError):
        await connection._retry_refused(request)
    assert calls == connection._REFUSED_RETRIES + 1
