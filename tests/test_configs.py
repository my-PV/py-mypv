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

Tests the configuration file functions."""

import importlib.resources

import pytest

from my_pv import configs

_CONFIG_DIR = importlib.resources.files("my_pv.configs")
CONFIG_FILES = sorted(p.name for p in _CONFIG_DIR.iterdir() if p.name.endswith(".json"))
MODELS = tuple(
    name.removesuffix(".json")
    for name in CONFIG_FILES
    if name.removesuffix(".json") != "000000"
)


@pytest.mark.parametrize(
    "model",
    MODELS,
)
async def test_all_files(model: str):
    """Tests if all configuration files follow the specification."""
    device_config = await configs.read_config(f"{model}0000000000")
    assert "name" in device_config
    assert "commands" in device_config
    assert "data" in device_config
    assert "setup" in device_config

    command_config = device_config["commands"]
    for config in command_config.values():
        assert config["type"] in ("boolean", "any")
    data_config = device_config["data"]
    for config in data_config.values():
        assert config["type"] in ("boolean", "number", "string", "enumeration")
    setup_config = device_config["setup"]
    for config in setup_config.values():
        assert config["type"] in ("boolean", "number", "string", "enumeration")
        if (config["type"] == "number"):
            assert "min" in config or "max" in config
        if config["type"] in ("enum"):
            assert config["options"]


async def test_unknown_model():
    """Test if a FileNotFoundError is raised when a configuration file is not found"""
    with pytest.raises(FileNotFoundError):
        await configs.read_config("9999990000000000")


def test_deep_merge():
    """Test deep merging two dictionaties."""
    dict1 = {
        "commands": {
            "bststrt": {
                "name": "Manual Boost",
                "type": "boolean"
            }
        }
    }
    dict2 = {
        "setup": {
            "bstmode": {
                "options": {
                    "0": "Off",
                    "1": "On",
                    "3": "Relais"
                },
                "type": "enumeration"
            }
        }
    }
    configs._deep_merge(dict1, dict2)
    assert dict1["commands"]["bststrt"]
    assert dict1["setup"]["bstmode"]


def test_deep_merge_updated_value():
    """Test deep merging two dictionaties with an updated value."""
    dict1 = {
        "setup": {
            "bstmode": {
                "name": "Boost Mode",
                "type": "boolean"
            }
        }
    }
    dict2 = {
        "setup": {
            "bstmode": {
                "options": {
                    "0": "Off",
                    "1": "On",
                    "3": "Relais"
                },
                "type": "enumeration"
            }
        }
    }
    configs._deep_merge(dict1, dict2)
    assert dict1["setup"]["bstmode"]["type"] == "enumeration"
    assert dict1["setup"]["bstmode"]["options"] == {"0": "Off", "1": "On", "3": "Relais"}

def test_deep_merge_deleted_value():
    """Test deep merging two dictionaties with a deleted value."""
    dict1 = {
        "setup": {
            "bstmode": {
                "name": "Boost Mode",
                "type": "boolean"
            }
        }
    }
    dict2 = {
        "setup": {
            "bstmode": None
        }
    }
    configs._deep_merge(dict1, dict2)
    assert "bstmode" not in dict1["setup"]
