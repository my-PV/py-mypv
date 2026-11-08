"""Tests for local device connection handling."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from my_pv import MyPVLocalDevice
from my_pv.connection import MyPVHTTPSConnection


class _FakeConnection:
    """Minimal local connection for testing transport selection."""

    def __init__(
        self,
        host: str,
        password: str | None = None,
        *,
        session: object | None = None,
        open_result: bool = True,
    ) -> None:
        self.uri = f"{self.protocol}://{host}/"
        self.mypv_dev = {"sn": "1234560000000000", "device": "AC-THOR"}
        self.open_result = open_result
        self.opened = False
        self.session = session
        self.password = password
        self.close = AsyncMock()
        self.fetch_setup = AsyncMock(return_value={"cloudmode": False})
        self.fetch_data = AsyncMock(return_value={"power": 0})

    async def open(self) -> bool:
        self.opened = self.open_result
        return self.open_result

    def is_open(self) -> bool:
        return self.opened


class _FakeHTTPConnection(_FakeConnection):
    protocol = "http"


class _FakeHTTPSConnection(_FakeConnection):
    protocol = "https"


@pytest.fixture
def connection_classes():
    with (
        patch("my_pv.MyPVHTTPConnection", _FakeHTTPConnection),
        patch("my_pv.MyPVHTTPSConnection", _FakeHTTPSConnection),
        patch.object(MyPVLocalDevice, "_read_config", new_callable=AsyncMock),
        patch.object(MyPVLocalDevice, "_init_device"),
    ):
        yield


async def test_connect_keeps_http_for_older_devices(connection_classes):
    """Devices that connect over HTTP should not incur an HTTPS retry."""
    device = MyPVLocalDevice("device.local")

    assert await device.connect()
    assert device.uri == "http://device.local/"


async def test_connect_falls_back_to_https_without_password(connection_classes):
    """Devices that require HTTPS should connect even without a password."""
    device = MyPVLocalDevice("device.local")
    device._connection.open_result = False

    assert await device.connect()
    assert device.uri == "https://device.local/"
    assert device.setup_uri == "https://device.local/"
    assert device._connection.password is None


async def test_https_connection_skips_auth_without_password():
    """HTTPS devices without password protection do not require an auth call."""
    connection = MyPVHTTPSConnection("device.local")
    session = Mock()

    assert await connection._auth(session)
    session.post.assert_not_called()
