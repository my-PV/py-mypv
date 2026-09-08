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

Tests for MyPVLocalDevice.
"""

import pytest

import tests.conftest as conftest
from my_pv import MyPVLocalDevice
from my_pv.configs import read_config
from my_pv.exceptions import MyPVDeviceNotSupportedError


@pytest.mark.usefixtures("mock_client_session")
async def test_connect() -> None:
    conftest.serial_number = "1601500000000000"
    device = MyPVLocalDevice("127.0.0.1")
    assert await device.connect()
    assert device._serial_number == "1601500000000000"


@pytest.mark.usefixtures("mock_client_session")
async def test_connect_unsupported_compmode() -> None:
    conftest.serial_number = "1601500000000001"
    device = MyPVLocalDevice("127.0.0.1")
    with (pytest.raises(MyPVDeviceNotSupportedError),):
        assert await device.connect()
