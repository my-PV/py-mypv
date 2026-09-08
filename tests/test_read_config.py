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

Tests for reading the device config files.
"""

import pytest

import tests.conftest as conftest
from my_pv import MyPVDevice, MyPVLocalDevice
from my_pv.configs import read_config
from my_pv.exceptions import MyPVDeviceNotSupportedError


async def test_read_config() -> None:
    config = await read_config("1601500000000000")
    assert config


async def test_read_config_unsupported() -> None:
    with (pytest.raises(FileNotFoundError),):
        await read_config("9999990000000000")


@pytest.mark.usefixtures("mock_client_session")
async def test_read_config() -> None:
    conftest.serial_number = "1601500000000000"
    device = MyPVLocalDevice("127.0.0.1")
    assert await device.connect()
    await device._read_config()

    assert device._device_config


@pytest.mark.usefixtures("mock_client_session")
async def test_read_config_unsupported_device() -> None:
    device = MyPVLocalDevice("127.0.0.1")
    device._serial_number = "9999990000000000"
    with (pytest.raises(MyPVDeviceNotSupportedError),):
        await device._read_config()
