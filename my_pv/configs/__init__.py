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

Configuration files for my-PV devices.

140100 - SOL•THOR
160150 - AC ELWA 2
160151 - AC ELWA 2
160152 - AC ELWA 2 3 kW
200100 - AC•THOR
200103 - AC•THOR i
200110 - AC•THOR Viessmann
200113 - AC•THOR i Viessmann
200300 - AC•THOR 9s
200310 - AC•THOR 9s Viessmann
210300 - HEA•THOR IoT 3,5 kW
210900 - HEA•THOR IoT 9 kW
"""

import asyncio
import importlib.resources
import json
from json.decoder import JSONDecodeError
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _deep_merge(dict1: dict[str, Any], dict2: dict[str, Any]) -> dict[str, Any]:
    """Merges dict2 into dict1. A null in dict2 removes the inherited value."""
    for key, value in dict2.items():
        if value is None:
            dict1.pop(key, None)
        elif isinstance(value, dict):
            if not isinstance(dict1.get(key), dict):
                dict1[key] = {}
            _deep_merge(dict1[key], value)
        else:
            dict1[key] = value
    return dict1


async def read_config(serial_number: str | None) -> dict[str, Any]:
    """Reads the configuration for a device with a given serial number."""

    config_files = ["000000.json"]
    if serial_number:
        config_files.append(
            "".join(
                c if c.isalnum() or c in "._-" else "_"
                for c in serial_number[:6].lower()
            )
            + ".json"
        )
    logger.debug("Using config files %s", config_files)

    config: dict[str, Any] | None = None
    for config_file in config_files:
        try:
            path = importlib.resources.files("my_pv.configs").joinpath(config_file)
            data = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))

            if not isinstance(data, dict):
                logger.error("Invallid configuration file %s", config_file)
            else:
                config = {} if config is None else config
                config = _deep_merge(config, data)
        except FileNotFoundError:
            logger.debug("Configuration file %s not found", config_file)
            raise
        except IsADirectoryError, PermissionError:
            logger.exception("Configuration file %s not accessible", config_file)
        except UnicodeDecodeError:
            logger.exception(
                "Invallid configuration file %s, Unicode error", config_file
            )
        except JSONDecodeError:
            logger.warning("Invallid config file %s", config_file)

    if config is not None:
        return {key: val for key, val in config.items() if val is not None}

    return {}
