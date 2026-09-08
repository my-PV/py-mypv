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

Common fixtures for the my-PV library tests.
"""

import importlib.resources as resources
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientResponse


class MockResponse:
    def __init__(self, text, status, content_type):
        self._text = text
        self.status = status
        self.content_type = content_type

    async def text(self):
        return self._text

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def __aenter__(self):
        return self


serial_number = "1601500000000000"


def read_response_from_file(url: str, **kwargs):
    file_name = url.split("/")[-1]
    response_file = f"{serial_number}.{file_name}"

    response_text = ""
    status = 500
    try:
        response_text = resources.read_text("tests.response_files", response_file)
        status = 200
    except FileNotFoundError:
        status = 404

    return MockResponse(response_text, status, "application/json")


@pytest.fixture
def mock_client_session() -> Generator[AsyncMock]:
    """Mock the ClientSession across the integration."""
    with (
        patch(
            "my_pv.connection.ClientSession",
            autospec=True,
        ) as mock_client_session,
    ):
        client_session = mock_client_session.return_value
        client_session.get = AsyncMock(side_effect=read_response_from_file)
        client_session.post = AsyncMock(side_effect=read_response_from_file)

        yield client_session
