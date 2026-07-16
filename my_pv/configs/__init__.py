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


Configuration files for my-PV devices.

140100 - SOL•THOR
160150 – AC ELWA 2
160151 – AC ELWA 2
160152 – AC ELWA 2 3 kW
200100 – AC•THOR
200103 – AC•THOR i
200110 - AC•THOR Viessmann
200113 – AC•THOR i Viessmann
200300 – AC•THOR 9s
200310 – AC•THOR 9s Viessmann
210300 - HEA•THOR IoT 3,5 kW
210900 - HEA•THOR IoT 9 kW
"""

import asyncio
import importlib.resources
import json
import logging
from json.decoder import JSONDecodeError
from typing import Any

logger = logging.getLogger(__name__)


def _deep_merge(dict1: dict[str, Any], dict2: dict[str, Any]) -> dict[str, Any]:
    for key in dict2:
        if (
            key in dict1
            and isinstance(dict1[key], dict)
            and isinstance(dict2[key], dict)
        ):
            _deep_merge(dict1[key], dict2[key])
        else:
            dict1[key] = dict2[key]
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
            text = await asyncio.get_running_loop().run_in_executor(
                None, importlib.resources.read_text, "my_pv.configs", config_file
            )

            if text is not None and len(text) > 0:
                config = {} if config is None else config
                config = _deep_merge(config, json.loads(text))
            else:
                logger.warning("Empty config file %s", config_file)
        except FileNotFoundError:
            logger.warning("Non existing config file %s", config_file)
        except JSONDecodeError:
            logger.warning("Invalid config file %s", config_file)

    if config is not None:
        config = {key: val for key, val in config.items() if val is not None}
        return config

    return {}
