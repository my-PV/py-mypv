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

import asyncio
import json
import logging
import ssl
import time
from abc import ABC, abstractmethod
from typing import Any, Final
from urllib.parse import urlencode, urlunsplit

from aiohttp import ClientSession, ClientTimeout
from aiohttp.client_exceptions import ClientConnectionError

from my_pv.exceptions import (
    MyPVAuthenticationError,
    MyPVConnectionError,
    MyPVTooManyRequestsError,
)

logger = logging.getLogger(__name__)

HTTP_PORT: Final = 80
HTTPS_PORT: Final = 443

CLOUD_HOST = "api.my-pv.com"

DONT_ENCODE = "-_.!~*'()"


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
    async def fetch_setup(self) -> dict[str, Any] | None:
        """Retrieves the device setup."""
        raise NotImplementedError

    @abstractmethod
    async def fetch_data(self) -> dict[str, Any] | None:
        """Retrieves the device data."""
        raise NotImplementedError

    @abstractmethod
    async def set_setup_value(self, key: str, value: Any) -> bool:
        """Sets the setup value for the given key."""
        raise NotImplementedError

    @abstractmethod
    async def send_command(self, key: str, value: Any) -> bool:
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

    def __init__(self, host: str) -> None:
        assert host is not None

        self._host = host

    async def _auth(self, session: ClientSession) -> bool:
        """The older HTTP only firmware doesn't yet support authentication."""
        auth_url = urlunsplit([self._PROTOCOL, self._host, "/auth.jsn", None, None])

        logger.debug("GET %s", auth_url)

        try:
            response = await session.get(auth_url, ssl=True)

            if response.status == 429:
                logger.error(response.reason)
                raise MyPVTooManyRequestsError(response.reason)
        except ssl.SSLCertVerificationError as exc:
            # Connection is redirected to SSL, authentication is needed.
            raise MyPVAuthenticationError() from exc
        except ConnectionRefusedError as exc:
            raise MyPVTooManyRequestsError(response.reason) from exc
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            await self.close()
            raise MyPVConnectionError() from exc

        return True

    async def open(self) -> bool:
        # Close the existing connection if still open
        await self.close()

        mypv_dev_url = urlunsplit(
            [self._PROTOCOL, self._host, "/mypv_dev.jsn", None, None]
        )

        session = None
        success = False
        try:
            session = ClientSession(timeout=ClientTimeout(total=5))
            response = await session.get(mypv_dev_url, ssl=self._SSL_CHECK)
            response_body = await response.text()
            response_json = {}
            if response.content_type == "application/json":
                response_json = json.loads(response_body)

            if response.status != 200:
                logger.error(
                    "Unexpected response %i %s: %s",
                    response.status,
                    response.reason,
                    response_body,
                )
            else:
                self._mypv_dev = response_json

                if await self._auth(session):
                    self._setup_url = urlunsplit(
                        [self._PROTOCOL, self._host, "/setup.jsn", None, None]
                    )
                    self._data_url = urlunsplit(
                        [self._PROTOCOL, self._host, "/data.jsn", None, None]
                    )

                    success = True
        except json.JSONDecodeError:
            logger.error(
                "Invallid JSON for response status %i: %s",
                response.status,
                response_body,
            )
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            logger.debug(exc)
        finally:
            if success:
                self._session = session
            elif session:
                # Close the connection if we failed to connect.
                await session.close()

        return success

    def is_open(self) -> bool:
        if self._session is None:
            return False

        return not self._session.closed

    async def close(self) -> bool:
        if self._session is not None:
            await self._session.close()
            self._session = None

        return True

    async def _get(
        self, url: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._session or (not self.is_open() and not await self.open()):
            raise MyPVConnectionError()

        if data:
            query = urlencode(data, safe=DONT_ENCODE)
            url = f"{url}?{query}"

        logger.debug("GET %s", url)

        try:
            response = await self._session.get(url, ssl=self._SSL_CHECK)
            response_body = await response.text()

            if response.status == 429:
                logger.error(response.reason)
                raise MyPVTooManyRequestsError(response.reason)

            response_json = {}
            if response.content_type == "application/json":
                response_json = json.loads(response_body)

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
        except ConnectionRefusedError as exc:
            raise MyPVTooManyRequestsError(response.reason) from exc
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            await self.close()
            raise MyPVConnectionError() from exc

        return {}

    @property
    def mypv_dev(self) -> dict[str, Any] | None:
        return self._mypv_dev

    async def fetch_setup(self) -> dict[str, Any] | None:
        if not self._setup_url:
            return None

        return await self._get(self._setup_url)

    async def fetch_data(self) -> dict[str, Any] | None:
        if not self._data_url:
            return None

        data = await self._get(self._data_url)
        return {key.lower(): value for key, value in data.items()}

    async def set_setup_value(self, key: str, value: Any) -> bool:
        if not self._setup_url:
            return False

        data = {key: value}

        response = await self._get(self._setup_url, data)
        return response.get(key) == value

    async def send_command(self, key: str, value: Any) -> bool:
        if not self._setup_url:
            return False

        data = {key: value}

        await self._get(self._setup_url, data)

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

        super().__init__(host)

        self._password = password

    async def _auth(self, session: ClientSession) -> bool:
        auth_url = urlunsplit([self._PROTOCOL, self._host, "/auth.jsn", None, None])

        data = urlencode({"pw": self._password}, safe=DONT_ENCODE)

        logger.debug("POST %s %s", auth_url, urlencode({"pw": "***"}, safe=DONT_ENCODE))

        try:
            response = await session.post(auth_url, data=data, ssl=self._SSL_CHECK)
            response_body = await response.text()

            if response.status == 429:
                logger.error(response.reason)
                raise MyPVTooManyRequestsError(response.reason)

            if response.status == 200 and response.content_type == "application/json":
                response_json = json.loads(response_body)

                if response_json.get("auth", 0) == 1:
                    return True
            else:
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
        except ConnectionRefusedError as exc:
            raise MyPVTooManyRequestsError(response.reason) from exc
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            await self.close()
            raise MyPVConnectionError() from exc

        # Authentication failed.
        raise MyPVAuthenticationError()

    async def _post(self, url: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self._session or (not self.is_open() and not await self.open()):
            raise MyPVConnectionError()

        data = urlencode(data, safe=DONT_ENCODE)

        logger.debug("POST %s %s", url, data)

        try:
            response = await self._session.post(url, data=data, ssl=self._SSL_CHECK)
            response_body = await response.text()

            if response.status == 429:
                logger.error(response.reason)
                raise MyPVTooManyRequestsError(response.reason)

            response_json = {}
            if response.content_type == "application/json":
                response_json = json.loads(response_body)

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
        except ConnectionRefusedError as exc:
            raise MyPVTooManyRequestsError(response.reason) from exc
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            await self.close()
            raise MyPVConnectionError() from exc

        return {}

    async def set_setup_value(self, key: str, value: Any) -> bool:
        if not self._setup_url:
            return False

        data = {key: value}

        response = await self._post(self._setup_url, data)
        return response.get(key) == value

    async def send_command(self, key: str, value: Any) -> bool:
        if not self._setup_url:
            return False

        data = {key: value}

        await self._post(self._setup_url, data)

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
        success = False
        try:
            session = ClientSession(headers=headers)

            response = await session.get(is_online_url)
            response_body = await response.text()
            response_json = json.loads(response_body)

            if response.status == 401:
                raise MyPVAuthenticationError(response_json.get("msg"))

            if response.status != 200:
                logger.error(
                    "Unexpected response %i %s: %s",
                    response.status,
                    response.reason,
                    response_body,
                )
            elif response_json.get("isOnline"):
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

                success = True
        except json.JSONDecodeError:
            logger.error(
                "Invallid JSON for response status %i: %s",
                response.status,
                response_body,
            )
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            logger.debug(exc)
        finally:
            if success:
                self._session = session
            elif session:
                # Close the connection if we failed to connect.
                await session.close()

        return success

    async def _put(self, url: str, data: str) -> bool:
        if not self._session or (not self.is_open() and not await self.open()):
            raise MyPVConnectionError()

        logger.debug("PUT %s %s", url, data)

        try:
            response = await self._session.put(url, data=data, ssl=self._SSL_CHECK)
            if response.status == 429:
                logger.error(response.reason)
                raise MyPVTooManyRequestsError(response.reason)

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
        except (ClientConnectionError, asyncio.TimeoutError) as exc:
            await self.close()
            raise MyPVConnectionError() from exc

        return False

    @property
    def mypv_dev(self) -> dict[str, Any] | None:
        raise NotImplementedError

    async def fetch_setup(self) -> dict[str, Any] | None:
        setup = await super().fetch_setup()
        if not setup:
            return None
        return setup.get("setup", {})

    async def fetch_data(self) -> dict[str, Any] | None:
        data = await super().fetch_data()
        if not data:
            return None
        if self._soc_url:
            soc = await self._get(self._soc_url)
            if soc and len(soc) == 2:
                data["soc"] = soc["percentage"]

        return data

    async def set_setup_value(self, key: str, value: Any) -> bool:
        if not self._setup_url:
            return False

        data = json.dumps({key: value})

        return await self._put(self._setup_url, data)

    async def send_command(self, key: str, value: Any) -> bool:
        if not self._setup_url:
            return False

        data = json.dumps({key: value})

        return await self._put(self._setup_url, data)
