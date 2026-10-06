"""Copyright 2026 my-PV GmbH, Austria.

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

The my-PV library.
"""

from abc import ABC, abstractmethod
import asyncio
import contextlib
from enum import StrEnum
import logging
import re
import time
from typing import Any, Final

from .configs import read_config
from .connection import (
    MyPVCloudConnection,
    MyPVConnection,
    MyPVHTTPConnection,
    MyPVHTTPSConnection,
)
from .exceptions import (
    MyPVAuthenticationError,
    MyPVConnectionError,
    MyPVDeviceNotSupportedError,
    MyPVNotSupportedError,
    MyPVTooManyRequestsError,
)

logger = logging.getLogger(__name__)

CLOUD_FRONTEND = "https://live.my-pv.com/"

_IGNORED_SETUP_KEYS: Final[list[str]] = [
    "fwversion",
    "psversion",
    "hwvers",
    "serialno",
    "macadr",
]
_IGNORED_DATA_KEYS: Final[list[str]] = [
    "device",
]
_BOOST_SETUP_KEYS: Final[list[str]] = [
    "boostactive",
    "bsttof1",
    "bsttof2",
    "bstton1",
    "bstton2",
    "bstwd1",
    "bstwd2",
    "bstwd3",
    "bstwd4",
    "bstwd5",
    "bstwd6",
    "bstwd7",
    "ww1boost",
]

_FW_VERSION_COMPARE_RE = re.compile("([<>=]*)[a-z]([0-9]*)")


class MyPVDeviceMainMode(StrEnum):
    """The different modes a my-PV device can support."""

    HOT_WATER = "boiler"
    SPACE_HEATING = "space_heating"
    HEATPUMP = "heatpump"
    PWM = "pwm"


class MyPVDevice(ABC):
    """my-PV base class for interfacing with my-PV devices."""

    advanced: bool = False

    _serial_number: str
    _model: str
    _hardware_version: str | None = None
    _firmware_version: str | None = None
    _mac_address: str | None = None

    _connection: MyPVConnection | None = None
    _uri: str | None = None
    _setup_uri: str | None = None

    _setup_values: dict[str, Any]
    _data_values: dict[str, Any]
    _device_config: dict[str, Any]

    _main_modes: tuple[MyPVDeviceMainMode, ...] | None = None

    _firmware_update_lock: asyncio.Lock
    _next_check_fwupd: float | None = None

    def __init__(self, advanced: bool = False):
        """Initializes a my-PV device."""
        self.advanced = advanced

        self._setup_values = {}
        self._data_values = {}
        self._device_config = {}

        self._firmware_update_lock = asyncio.Lock()

    def _init_device(self, setup_values: dict[str, Any]) -> None:
        self._hardware_version = setup_values.get("hwvers")
        self._firmware_version = setup_values.get("fwversion")

        # Format MAC address
        mac_address = setup_values.get("macadr")
        if mac_address:
            mac_address = mac_address.lower()
            mac_address = re.sub("[^0-9a-f]", "", mac_address)
            mac_address = ":".join(mac_address[i : i + 2] for i in range(0, 12, 2))
            self._mac_address = mac_address

        if self.serial_number.startswith(
            ("160150", "160151", "160152")
        ) and setup_values.get("compmode") not in (None, 0):
            raise MyPVDeviceNotSupportedError(self.serial_number)

        match setup_values.get("mainmode"):
            case 1 | 2 | 3:
                self._main_modes = (MyPVDeviceMainMode.HOT_WATER,)
            case 4:
                self._main_modes = (
                    MyPVDeviceMainMode.HOT_WATER,
                    MyPVDeviceMainMode.HEATPUMP,
                )
            case 5:
                self._main_modes = (
                    MyPVDeviceMainMode.HOT_WATER,
                    MyPVDeviceMainMode.SPACE_HEATING,
                )
            case 6:
                self._main_modes = (MyPVDeviceMainMode.SPACE_HEATING,)
            case 7:
                self._main_modes = (
                    MyPVDeviceMainMode.HOT_WATER,
                    MyPVDeviceMainMode.PWM,
                )
            case _:
                self._main_modes = (MyPVDeviceMainMode.HOT_WATER,)

        # Boost mode on the AC ELWA 2 is special because it depends on mainmode.
        # If mainmode is 1 only option 4 is possible.
        # If mainmode is 3 options 4 and 5 are possible.
        if (
            self.serial_number.startswith(("160150", "160151", "160152"))
            and setup_values.get("mainmode") == 1
        ):
            self._device_config["setup"]["bstmode"].get("options", {}).pop("5", None)

        # Disable Maximum Power when Main Mode is Hot water 3.5 kW + 3 kW on AC ELWA 2
        if (
            self.serial_number.startswith(("160150", "160151"))
            and setup_values.get("mainmode") == 3
        ):
            self._device_config["setup"].pop("maxpwr", None)

    @abstractmethod
    async def connect(self) -> bool:
        """Connect to my-PV device.

        Returns True when connection could be established else False.
        """
        raise NotImplementedError

    @property
    def connected(self) -> bool:
        """True when connection is established else False."""
        if self._connection:
            return self._connection.is_open()
        return False

    @property
    def setup_uri(self) -> str | None:
        """The location of the my-PV device setup web interface."""
        return self._setup_uri

    @property
    def uri(self) -> str | None:
        """The underlying connection uri to the my-PV device."""
        return self._uri

    async def disconnect(self) -> bool:
        """Disconnect from my-PV device.

        Returns True when connection could be closed else False.
        """
        if self._connection is None:
            return True

        if await self._connection.close():
            self._connection = None
            return True

        return False

    @property
    def serial_number(self) -> str:
        """The device serial number."""
        return self._serial_number

    @property
    def model(self) -> str:
        """The device model."""
        return self._model

    @property
    def hardware_version(self) -> str | None:
        """The device hardware version."""
        return self._hardware_version

    @property
    def firmware_version(self) -> str | None:
        """The device firmware version."""
        return self._firmware_version

    @property
    def latest_firmware_version(self) -> str | None:
        """The device latest available firmware version."""
        return self._get_data_value("fwversionlatest")

    @property
    def firmware_update_available(self) -> bool:
        """Checks if there is new firmware available for the device."""
        if (
            not self.supports_command("firmware_update")
            or not self.firmware_version
            or not self.latest_firmware_version
        ):
            return False

        return self._get_data_value("upd_state") not in [None, "0"]

    async def update_firmware(self) -> bool:
        """Updates the firmware on the device."""
        if self._get_data_value("upd_state") in [None, "0"]:
            # Nothing to update.
            return False

        if self._firmware_update_lock.locked():
            return False

        async with self._firmware_update_lock:
            # Download firmware.
            if (
                self.supports_command("firmware_download")
                and self._get_data_value("upd_state") == "1"
            ):
                logger.info("Downloading firmware")
                await self.send_command("firmware_download")

            # Wait for download to be finished.
            if self._get_data_value("upd_state") in [str(x) for x in range(1, 10)]:
                timeout = time.monotonic() + 300  # 5 minutes
                while True:
                    logger.debug(
                        "Downloading firmware %i%%",
                        self._get_data_value("upd_percentage"),
                    )
                    if self._get_data_value("upd_state") == "10":
                        logger.debug("Downloading finished")
                        break
                    if self._get_data_value("upd_state") == "99":
                        logger.debug("Downloading failed")
                        break
                    if time.monotonic() > timeout:
                        logger.debug("Downloading timeout")
                        return False

                    await asyncio.sleep(1)
                    with contextlib.suppress(MyPVTooManyRequestsError):
                        await self.fetch_data()

            # Update firmware.
            if (
                self.supports_command("firmware_update")
                and self._get_data_value("upd_state") == "10"
            ):
                logger.info("Updating firmware")
                await self.send_command("firmware_update")

            # Wait for update to be finished.
            timeout = time.monotonic() + 300  # 5 minutes
            while True:
                if self._get_data_value("upd_state") == "0":
                    logger.debug("Update finished")
                    return True
                if self._get_data_value("upd_state") == "99":
                    logger.error("Update failed")
                    return False
                if time.monotonic() > timeout:
                    logger.debug("Update timeout")
                    return False

                await asyncio.sleep(1)
                if not self.connected:
                    # Device reboots during the firmware update, reconnect.
                    with contextlib.suppress(MyPVConnectionError):
                        await self.connect()

                with contextlib.suppress(
                    MyPVConnectionError
                ):  # A connection error is expected as the device will reboot during the firmware update.
                    await self.fetch_data()

    @property
    def firmware_update_progress(self) -> int | None:
        """Returns the progress of the firmware update in percents, or None if no firmware update is active."""
        if self._get_data_value("upd_state") == "3":
            return self._get_data_value("upd_percentage")
        return None

    @property
    def mac_address(self) -> str | None:
        """The device MAC address."""
        return self._mac_address

    def _fw_version_compare(self, compare_with: str) -> bool:
        result = _FW_VERSION_COMPARE_RE.search(compare_with)
        if result is None:
            return False

        comparator = result.group(1)
        version = int(result.group(2))

        fw_version = self._data_values.get("fwversion2_comp")
        if fw_version is None:
            fw_version = self._data_values.get("fwversion2")
        if fw_version is None:
            return False

        fw_version = int(fw_version[1:])

        match comparator:
            case "==":
                return fw_version == version
            case "<=":
                return fw_version <= version
            case ">=":
                return fw_version >= version
            case "<":
                return fw_version < version
            case ">":
                return fw_version > version

        return False

    async def _read_config(self) -> None:
        try:
            self._device_config = await read_config(self.serial_number)
        except FileNotFoundError as ex:
            raise MyPVDeviceNotSupportedError(self.serial_number) from ex

    async def fetch_data(self) -> bool:
        """Fetch data from the device.

        Returns True when successful else False.
        """
        if not self._connection or not self.connected:
            return False

        setup_values = await self._connection.fetch_setup()
        if not setup_values:
            return False
        setup_values = {
            key: val
            for key, val in setup_values.items()
            if key not in _IGNORED_SETUP_KEYS and val not in [None, "null"]
        }

        data_values = await self._connection.fetch_data()
        if not data_values:
            return False
        data_values = {
            key: val
            for key, val in data_values.items()
            if key not in _IGNORED_DATA_KEYS
            and val not in [None, "null"]
            and not (
                key in self._device_config["data"]
                and self._device_config["data"][key].get("type") == "number"
                and self._device_config["data"][key].get("unit") == "°C"
                and val == 0
            )
        }

        self._setup_values = {
            key: setup_values[key]
            for key, val in self._device_config["setup"].items()
            if key in setup_values and not val.get("readonly", False)
        } | {
            key: data_values[key]
            for key, val in self._device_config["data"].items()
            if key in data_values and not val.get("readonly", True)
        }

        self._data_values = {
            key: setup_values[key]
            for key, val in self._device_config["setup"].items()
            if key in setup_values and val.get("readonly", False)
        } | {
            key: data_values[key]
            for key, val in self._device_config["data"].items()
            if key in data_values and val.get("readonly", True)
        }

        if self.supports_command("check_fwupd") and (
            not self._next_check_fwupd or time.monotonic() > self._next_check_fwupd
        ):
            with contextlib.suppress(MyPVConnectionError):
                await self.send_command("check_fwupd")
            self._next_check_fwupd = time.monotonic() + 24 * 60 * 60  # Once a  day

        return True

    def supports_configuration(self, key: str) -> bool:
        """Return True if the device supports the given setup parameter."""
        if key not in self._setup_values:
            return False
        if (
            key in self._device_config["setup"]
            and not self._device_config["setup"][key].get("readonly", False)
            and self._device_config["setup"][key].get("advanced", False)
            in [False, self.advanced]
        ):
            return True
        return (
            key in self._device_config["data"]
            and not self._device_config["data"][key].get("readonly", True)
            and self._device_config["data"][key].get("advanced", False)
            in [False, self.advanced]
        )

    def get_setup_configurations(self) -> dict[str, dict[str, Any]]:
        """Gets the configuration of the available setup parameters."""
        setup_keys = self._setup_values.keys()
        return {
            key: val
            for key, val in self._device_config["setup"].items()
            if key in setup_keys
            and not val.get("readonly", False)
            and val.get("advanced", False) in [False, self.advanced]
        } | {
            key: val
            for key, val in self._device_config["data"].items()
            if key in setup_keys
            and not val.get("readonly", True)
            and val.get("advanced", False) in [False, self.advanced]
        }

    def get_setup_configuration(self, key: str) -> dict[str, Any] | None:
        """Returns the configuration for the the given setup parameter."""
        return self.get_setup_configurations().get(key)

    def get_setup_value(self, key: str) -> Any:
        """Gets the value of the given setup parameter."""
        if not self.connected:
            raise MyPVConnectionError("Device not connected")

        if not self.supports_configuration(key):
            raise MyPVNotSupportedError(key)

        # Disable all but Device Mode and Target Temperature when Device Mode is Off
        if (
            key not in ["devmode", "ww1target"]
            and self._setup_values.get("devmode") == 0
        ):
            return None

        # Disable Boost Active when Boost Mode is Off
        if key in _BOOST_SETUP_KEYS and self._setup_values.get("bstmode") == 0:
            return None

        config = self.get_setup_configuration(key)
        value = self._setup_values.get(key)
        if config and value is not None:
            match config.get("type"):
                case "boolean":
                    value = bool(value)
                case "number":
                    value = int(value)
                    if divider := config.get("divider"):
                        value = value / divider
                    if multiplier := config.get("multiplier"):
                        value = value * multiplier
                case "enumeration":
                    value = str(value)
                case "string":
                    value = str(value)

        return value

    def supports_data(self, key: str) -> bool:
        """Returns True when  the given data key is supported by the device."""
        if key not in self._data_values:
            return False
        if (
            key in ["wifi_signal", "wifi_signal_strength"]
            and self._data_values.get("cur_eth_mode") == 0
        ):
            return False
        if (
            key in self._device_config["data"]
            and self._device_config["data"][key].get("readonly", True)
            and self._device_config["data"][key].get("advanced", False)
            in [False, self.advanced]
        ):
            return True

        return (
            key in self._device_config["setup"]
            and self._device_config["setup"][key].get("readonly", False)
            and self._device_config["setup"][key].get("advanced", False)
            in [False, self.advanced]
        )

    def get_data_configurations(self) -> dict[str, dict[str, Any]]:
        """Gets the configuration of the  the given data key."""
        data_keys = self._data_values.keys()
        data_configurations = {
            key: val
            for key, val in self._device_config["setup"].items()
            if key in data_keys
            and val.get("readonly", False)
            and val.get("advanced", False) in [False, self.advanced]
        } | {
            key: val
            for key, val in self._device_config["data"].items()
            if key in data_keys
            and val.get("readonly", True)
            and val.get("advanced", False) in [False, self.advanced]
        }

        if self._data_values.get("cur_eth_mode") == 0:
            data_configurations.pop("wifi_signal", None)
            data_configurations.pop("wifi_signal_strength", None)

        return data_configurations

    def get_data_configuration(self, key: str) -> dict[str, Any] | None:
        """Returns the configuration for the the given data key."""
        return self.get_data_configurations().get(key)

    def _get_data_value(self, key: str) -> Any:
        """Gets the value of the given data key or None if the given data key is not supported."""
        config = self.get_data_configuration(key)
        value = self._data_values.get(key)
        if config and value is not None:
            match config.get("type"):
                case "boolean":
                    value = bool(value)
                case "number":
                    value = int(value)
                    if divider := config.get("divider"):
                        value = value / divider
                    if multiplier := config.get("multiplier"):
                        value = value * multiplier
                case "enumeration":
                    value = str(value)
                case "string":
                    value = str(value)

        return value

    def get_data_value(self, key: str) -> Any:
        """Gets the value of the given data key.

        Throws MyPVConnectionError if the device is not connected.

        Throws MyPVNotSupportedError if the given data key is not supported.
        """
        if not self.connected:
            raise MyPVConnectionError("Device not connected")

        if not self.supports_data(key):
            raise MyPVNotSupportedError(key)

        return self._get_data_value(key)

    async def set_setup_value(self, key: str, value: Any) -> bool:
        """Sets the value of the given setup parameter."""
        if not self._connection or not self.connected:
            raise MyPVConnectionError("Device not connected")

        if not self.supports_configuration(key):
            raise MyPVNotSupportedError(key)

        # Disable all but Device Mode and target temperature when Device Mode is Off
        if (
            key not in ["devmode", "ww1target"]
            and self._setup_values.get("devmode") == 0
        ):
            return False

        # Disable Boost Active when Boost Mode is Off
        if key in _BOOST_SETUP_KEYS and self._setup_values.get("bstmode") == 0:
            return False

        config = self.get_setup_configuration(key)
        if config:
            command = config.get("command")
            if command:
                result = await self.send_command(command, value)

                if result:
                    self._setup_values[key] = value

                return result

            match config.get("type"):
                case "boolean":
                    value = int(value)
                case "number":
                    if not config.get("min", 0) <= value <= config.get("max", 0):
                        return False

                    if divider := config.get("divider"):
                        value = value * divider
                    if multiplier := config.get("multiplier"):
                        value = value / multiplier

                    value = round(value)
                case "enumeration":
                    if value not in config["options"]:
                        return False
                    value = int(value)

        result = await self._connection.set_setup_value(key, value)

        if result:
            self._setup_values[key] = value

        return result

    def supports_command(self, command: str) -> bool:
        """Returns True when a command is supported by the device."""
        return command in self._device_config["commands"] and self._device_config[
            "commands"
        ][command].get("advanced", False) in [False, self.advanced]

    def get_command_configurations(self) -> dict[str, dict[str, Any]]:
        """Gets the configuration of the available commands supported by the device."""
        return {
            key: val
            for key, val in self._device_config["commands"].items()
            if val.get("advanced", False) in [False, self.advanced]
        }

    def get_command_configuration(self, command: str) -> dict[str, Any] | None:
        """Returns the configuration for a command."""
        return self.get_command_configurations().get(command)

    async def send_command(self, command: str, value: Any = None) -> bool:
        """Sends a command to the device."""
        if not self._connection or not self.connected:
            raise MyPVConnectionError("Device not connected")

        if not self.supports_command(command):
            raise MyPVNotSupportedError(command)

        config = self.get_command_configuration(command)
        if config:
            if value is None and config.get("type") in ("boolean", "number"):
                return False

            match config.get("type"):
                case "boolean":
                    value = int(value)
                case "number":
                    if not config.get("min", 0) <= value <= config.get("max", 0):
                        return False

                    if divider := config.get("divider"):
                        value = value * divider
                    if multiplier := config.get("multiplier"):
                        value = value / multiplier

                    value = round(value)
                case "fixed":
                    value = config.get("value")
                case "any":
                    value = 1

        return await self._connection.send_command(command, value)

    def supports_main_mode(self, mode: MyPVDeviceMainMode) -> bool:
        """Returns True if device supports the given main device mode."""
        if not self._main_modes:
            return False
        return mode in self._main_modes

    @property
    def current_temperature(self) -> float | None:
        """The current temperature."""
        return self.get_data_value("temp1")

    @property
    def target_temperature(self) -> float | None:
        """The target temperature."""
        return self.get_setup_value("ww1target")

    async def set_target_temperature(self, temperature: float) -> bool:
        """Sets the device target temperature."""
        return await self.set_setup_value("ww1target", temperature)

    @property
    def is_on(self) -> bool:
        """Returns True if the device is on."""
        return self.get_setup_value("devmode") is True

    async def turn_on(self) -> bool:
        """Turns the device on."""
        return await self.set_setup_value("devmode", True)

    async def turn_off(self) -> bool:
        """Turns the device off."""
        return await self.set_setup_value("devmode", False)


class MyPVLocalDevice(MyPVDevice):
    """my-PV class for interfacing with my-PV devices over a (local) TCP/IP connection."""

    _host: str
    _password: str | None

    def __init__(
        self, host: str, password: str | None = None, advanced: bool = False
    ) -> None:
        """Initializes a local connected my-PV device."""
        if not host:
            raise ValueError("host is required")

        super().__init__(advanced=advanced)

        self._host = host
        self._password = password

        if password:
            self._setup_uri = f"https://{host}/"

    async def connect(self) -> bool:
        """Connect to my-PV device.

        Returns True when connection could be established else False.
        """
        await self.disconnect()

        connection: MyPVHTTPConnection
        if self._password:
            connection = MyPVHTTPSConnection(self._host, self._password)
        else:
            connection = MyPVHTTPConnection(self._host)
        self._uri = connection.uri

        try:
            if not await connection.open():
                return False
        except MyPVConnectionError:
            return False
        finally:
            try:
                if connection.mypv_dev:
                    self._serial_number = connection.mypv_dev["sn"]
                    self._firmware_version = connection.mypv_dev.get("fwversion")
                    await self._read_config()
                    if "name" in self._device_config:
                        self._model = self._device_config["name"]
                    else:
                        self._model = connection.mypv_dev.get("device", "")
            except MyPVDeviceNotSupportedError:
                await connection.close()
                raise

        try:
            # Get the device setup
            setup_values = await connection.fetch_setup()
            if not setup_values:
                await connection.close()
                return False

            self._init_device(setup_values)
        except MyPVConnectionError:
            await connection.close()
            return False
        except MyPVAuthenticationError, MyPVDeviceNotSupportedError:
            await connection.close()
            raise

        self._connection = connection

        if not self._setup_uri and setup_values.get("cloudmode"):
            self._setup_uri = CLOUD_FRONTEND

        return True


class MyPVCloudDevice(MyPVDevice):
    """my-PV class for interfacing with my-PV devices over the cloud."""

    _setup_uri = CLOUD_FRONTEND

    _host: str | None
    _serial_number: str
    _api_token: str

    def __init__(
        self,
        serial_number: str,
        api_token: str,
        advanced: bool = False,
        *,
        host: str | None = None,
    ) -> None:
        """Initializes a cloud connected my-PV device."""
        assert serial_number is not None
        assert api_token is not None

        super().__init__(advanced=advanced)

        self._host = host
        self._serial_number = serial_number
        self._api_token = api_token

    async def connect(self) -> bool:
        """Connect to my-PV device.

        Returns True when connection could be established else False.
        """
        await self.disconnect()

        connection = MyPVCloudConnection(
            self._serial_number, self._api_token, host=self._host
        )

        try:
            if not await connection.open():
                return False
        except MyPVConnectionError:
            return False
        finally:
            await self._read_config()
            self._model = self._device_config["name"]

        try:
            # Get the device setup
            setup_values = await connection.fetch_setup()
            if not setup_values:
                await connection.close()
                return False

            self._init_device(setup_values)
        except MyPVConnectionError:
            await connection.close()
            return False

        self._connection = connection

        await self._read_config()

        return True
