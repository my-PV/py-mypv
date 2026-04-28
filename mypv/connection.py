"""
   Copyright 2026 my-PV GmbH, Austria

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

This file defines the different connection methods the my-PV library supports.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from asyncio.base_events import ssl
from typing import Final
from urllib.parse import urlencode, urlunsplit

from aiohttp import ClientSession
from aiohttp.client_exceptions import ClientConnectionError, ConnectionTimeoutError

from mypv.exceptions import MyPVAuthenticationError, MyPVConnectionError

logger = logging.getLogger(__name__)

HTTP_PORT: Final = 80
HTTPS_PORT: Final = 443

CLOUD_HOST = "api.my-pv.com"


class MyPVConnection(ABC):
    """
    my-PV Connection base class for connecting to my-PV devices.
    """

    @abstractmethod
    async def open(self) -> bool:
        """Opens the connection to the device."""
        raise NotImplementedError

    @abstractmethod
    def is_open(self) -> bool:
        """True if the connection is open else False."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> bool:
        """Closes the connection to the device."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_setup(self) -> dict:
        """Retrieves the device setup."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_data(self) -> dict:
        """Retrieves the device data."""
        raise NotImplementedError

    @abstractmethod
    async def set_setup_value(self, key: str, value: bool | float | int | str) -> bool:
        """Sets the setup value for the given key."""
        raise NotImplementedError

    @abstractmethod
    async def send_command(self, key: str, value: bool | float | int | str) -> bool:
        """Sends a command to the device."""
        raise NotImplementedError

    @property
    def uri(self) -> str | None:
        """Returns the URI the connection is connected to"""
        return None

    def __str__(self) -> str:
        """Returns a string representation of the object."""
        return str(self.uri)


class MyPVHTTPConnection(MyPVConnection):
    """my-PV connection using HTTP on port 80."""

    _PROTOCOL = "http"
    _SSL_CHECK = True

    _host: str
    _session: ClientSession | None = None
    _setup_url: str | None = None
    _data_url: str | None = None

    _mypv_dev = None
    # _serial_number: str | None = None
    # _model: str | None = None
    # _firmware_version: str | None = None

    def __init__(self, host: str) -> None:
        assert host is not None

        self._host = host

    async def _auth(self, session: ClientSession) -> bool:
        """The older HTTP only firmware doesn't yet support authentication."""
        try:
            auth_url = urlunsplit([self._PROTOCOL, self._host, "/auth.jsn", None, None])
            response = await session.get(auth_url, ssl=True)

            if response.status == 404:
                # Firmware doesn't support authentication.
                return True
        except ssl.SSLCertVerificationError as exc:
            # Connection is redirected to SSL, authentication is needed.
            raise MyPVAuthenticationError() from exc
        except ClientConnectionError as exc:
            logger.debug(exc)

        return False

    async def open(self) -> bool:
        # Close the existing connection if still open
        await self.close()

        mypv_dev_url = urlunsplit(
            [self._PROTOCOL, self._host, "/mypv_dev.jsn", None, None]
        )

        session = None
        try:
            session = ClientSession()
            response = await session.get(mypv_dev_url, ssl=self._SSL_CHECK)
            response_body = await response.text()
            response_json = {}
            if response.content_type == "application/json":
                response_json = json.loads(response_body)

            if response.status == 401:
                await session.close()
                raise MyPVAuthenticationError(response_json.get("msg"))
            if response.status != 200:
                logger.error(
                    "Unexpected response %i %s: %s",
                    response.status,
                    response.reason,
                    response_body,
                )
                await session.close()
                return False

            self._mypv_dev = response_json
            # self._serial_number = response_json.get("sn")
            # self._model = response_json.get("device")
            # self._firmware_version = response_json.get("fwversion")

            if await self._auth(session):
                self._session = session

                self._setup_url = urlunsplit(
                    [self._PROTOCOL, self._host, "/setup.jsn", None, None]
                )
                self._data_url = urlunsplit(
                    [self._PROTOCOL, self._host, "/data.jsn", None, None]
                )

                return True
        except json.JSONDecodeError:
            logger.error(
                "Invallid JSON for response status %i: %s",
                response.status,
                response_body,
            )
        except ClientConnectionError as exc:
            logger.debug(exc)

        # Close the connection if we failed to connect.
        if session:
            await session.close()

        return False

    def is_open(self) -> bool:
        if self._session is None:
            return False

        return not self._session.closed

    async def close(self) -> bool:
        if self._session is not None:
            await self._session.close()
            self._session = None

        return True

    async def _get(self, url) -> dict:
        if (self._session is None or self._session.closed) and not await self.open():
            raise MyPVConnectionError()

        try:
            response = await self._session.get(url, ssl=self._SSL_CHECK)
            if response.status == 429:
                logger.error(response.reason)
                return {}

            response_body = await response.text()
            response_json = {}
            if response.content_type == "application/json":
                response_json = json.loads(response_body)
                # if isinstance(response_json, str):
                #     response_json = json.loads(response_json)

            if response.status == 200:
                return response_json

            if response.status == 401:
                raise MyPVAuthenticationError(response_json.get("msg"))

            logger.error(
                "Unexpected response %i %s: %s",
                response.status,
                response.reason,
                response_body,
            )
        except json.JSONDecodeError as exc:
            logger.error(
                "Invallid JSON for response status %i: %s",
                response.status,
                response_body,
            )
            raise MyPVConnectionError() from exc
        except (ClientConnectionError, ConnectionTimeoutError) as exc:
            raise MyPVConnectionError() from exc

        return {}

    def mypv_dev(self) -> dict | None:
        return self._mypv_dev

    async def fetch_setup(self) -> dict:
        return await self._get(self._setup_url)

    async def fetch_data(self) -> dict:
        data = await self._get(self._data_url)
        return {key.lower(): value for key, value in data.items()}

    async def set_setup_value(self, key: str, value: bool | float | int | str) -> bool:
        query = urlencode({key: value})
        url = urlunsplit([self._PROTOCOL, self._host, "/setup.jsn", query, None])
        logger.debug("Set setup parameter url: %s", url)

        response = await self._get(url)
        return response.get(key) == value

    async def send_command(self, key: str, value: bool | float | int | str) -> bool:
        query = urlencode({key: value})
        url = urlunsplit([self._PROTOCOL, self._host, "/setup.jsn", query, None])
        logger.debug("Send command parameter url: %s", url)

        await self._get(url)

        return True

    @property
    def uri(self) -> str:
        return urlunsplit([self._PROTOCOL, self._host, "/", None, None])


class MyPVHTTPSConnection(MyPVHTTPConnection):
    """my-PV connection using HTTPS on port 443."""

    _PROTOCOL = "https"
    _SSL_CHECK = False

    _password: str

    def __init__(self, host: str, password: str) -> None:
        assert host is not None
        assert password is not None

        self._host = host
        self._password = password

    async def _auth(self, session: ClientSession) -> bool:
        try:
            auth_url = urlunsplit([self._PROTOCOL, self._host, "/auth.jsn", None, None])
            data = {"pw": self._password}
            response = await session.post(auth_url, data=data, ssl=self._SSL_CHECK)
            if response.status == 405:
                # Older beta firmware expects a get request
                # ToDo Remove when older beta firmware is phased out
                query = urlencode(data)
                auth_url = urlunsplit(
                    [self._PROTOCOL, self._host, "/auth.jsn", query, None]
                )
                response = await session.get(auth_url, ssl=self._SSL_CHECK)

            response_body = await response.text()
            response_json = {}
            if response.content_type == "application/json":
                response_json = json.loads(response_body)

            if response_json.get("auth", 0) == 1:
                return True

            # Authentication failed.
            if response_json.get("default", 0) == 1:
                raise MyPVAuthenticationError("Use device key")
            else:
                raise MyPVAuthenticationError("Use password")
        except ClientConnectionError as exc:
            logger.debug(exc)

        return False

    async def _post(self, url, data) -> dict:
        if (self._session is None or self._session.closed) and not await self.open():
            raise MyPVConnectionError()

        try:
            response = await self._session.post(url, data=data, ssl=self._SSL_CHECK)
            if response.status == 405:
                # Older beta firmware expects a get request
                # ToDo Remove when older beta firmware is phased out
                query = urlencode(data)
                auth_url = urlunsplit(
                    [self._PROTOCOL, self._host, "/setup.jsn", query, None]
                )
                response = await self._session.get(auth_url, ssl=self._SSL_CHECK)
            response_body = await response.text()

            if response.status == 200 and response.content_type == "application/json":
                return json.loads(response_body)

            logger.error(
                "Unexpected response %i %s: %s",
                response.status,
                response.reason,
                response_body,
            )
        except json.JSONDecodeError as exc:
            logger.error(
                "Invallid JSON for response status %i %s: %s",
                response.status,
                response_body,
            )
            raise MyPVConnectionError() from exc
        except (ClientConnectionError, ConnectionTimeoutError) as exc:
            raise MyPVConnectionError() from exc

        return {}

    async def set_setup_value(self, key: str, value: bool | float | int | str) -> bool:
        url = urlunsplit([self._PROTOCOL, self._host, "/setup.jsn", None, None])
        logger.debug("Set setup parameter url: %s", url)

        data = {key: value}

        response = await self._post(url, data)
        return response.get(key) == value

    async def send_command(self, key: str, value: bool | float | int | str) -> bool:
        url = urlunsplit([self._PROTOCOL, self._host, "/setup.jsn", None, None])
        logger.debug("Send command parameter url: %s", url)

        data = {key: value}

        await self._post(url, data)

        return True


class MyPVCloudConnection(MyPVHTTPConnection):
    """my-PV cloud connection."""

    _PROTOCOL = "https"

    _api_token: str

    _soc_url: str | None = None
    _logdata_url: str | None = None

    _serial_number: str

    def __init__(
        self, serial_number: str, api_token: str, *, host: str | None = None
    ) -> None:
        assert serial_number is not None
        assert api_token is not None

        if not host:
            host = CLOUD_HOST

        super().__init__(host)

        self._serial_number = serial_number
        self._api_token = api_token

    async def open(self) -> bool:
        # Close the existing connection if still open
        await self.close()

        is_online_url = urlunsplit(
            [
                self._PROTOCOL,
                self._host,
                f"/api/v1/device/{self._serial_number}/isOnline",
                None,
                None,
            ]
        )
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        session = None
        try:
            session = ClientSession(headers=headers)

            response = await session.get(is_online_url)
            response_body = await response.text()
            response_json = json.loads(response_body)

            if response.status == 401:
                await session.close()
                raise MyPVAuthenticationError(response_json.get("msg"))
            if response.status == 200 and not response_json.get("isOnline"):
                await session.close()
                return False
            if response.status != 200:
                logger.error(
                    "Unexpected response %i %s: %s",
                    response.status,
                    response.reason,
                    response_body,
                )
                await session.close()
                return False

            self._session = session
            self._setup_url = urlunsplit(
                [
                    self._PROTOCOL,
                    self._host,
                    f"/api/v1/device/{self._serial_number}/setup",
                    None,
                    None,
                ]
            )
            self._data_url = urlunsplit(
                [
                    self._PROTOCOL,
                    self._host,
                    f"/api/v1/device/{self._serial_number}/data",
                    None,
                    None,
                ]
            )
            self._soc_url = urlunsplit(
                [
                    self._PROTOCOL,
                    self._host,
                    f"/api/v1/device/{self._serial_number}/data/soc",
                    None,
                    None,
                ]
            )
            query = urlencode(
                {
                    "beginDate": "2020-01-01",
                    "endDate": "2026-03-23",
                    "timezone": time.tzname[1],
                    "interval": "1h",
                }
            )
            # ?beginDate=2024-10-27&endDate=2024-10-29&timezone=Europe%2FVienna&interval=1h
            self._logdata_url = urlunsplit(
                [
                    self._PROTOCOL,
                    self._host,
                    f"/api/v1/device/{self._serial_number}/logdata",
                    query,
                    None,
                ]
            )

            return True
        except ClientConnectionError as exc:
            logger.debug(exc)

        # Close the connection if we failed to connect.
        if session:
            await session.close()

        return False

    async def _put(self, url, body) -> bool:
        if (self._session is None or self._session.closed) and not await self.open():
            raise MyPVConnectionError()

        try:
            response = await self._session.put(url, data=body, ssl=self._SSL_CHECK)
            if response.status == 429:
                logger.error(response.reason)
                return False

            response_body = await response.text()
            response_json = {}
            if response.content_type == "application/json":
                response_json = json.loads(response_body)

            if response.status == 200 and response_json == "ok":
                return True

            if response.status == 401:
                raise MyPVAuthenticationError(response_json.get("msg"))

            logger.error(
                "Unexpected response %i %s: %s",
                response.status,
                response.reason,
                response_body,
            )
        except json.JSONDecodeError as exc:
            logger.error(
                "Invallid JSON for response status %i: %s",
                response.status,
                response_body,
            )
            raise MyPVConnectionError() from exc
        except (ClientConnectionError, ConnectionTimeoutError) as exc:
            raise MyPVConnectionError() from exc

        return False

    def mypv_dev(self) -> dict | None:
        raise NotImplementedError

    async def fetch_setup(self) -> dict:
        return (await super().fetch_setup()).get("setup", {})

    async def fetch_data(self) -> dict:
        data = await super().fetch_data()
        soc = await self._get(self._soc_url)
        if soc and len(soc) == 2:
            data["soc"] = soc["percentage"]

        return data

    async def set_setup_value(self, key: str, value: bool | float | int | str) -> bool:
        body = json.dumps({key: value})

        return await self._put(self._setup_url, body)

    async def send_command(self, key: str, value: bool | float | int | str) -> bool:
        body = json.dumps({key: value})

        return await self._put(self._setup_url, body)
