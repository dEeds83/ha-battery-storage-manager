"""Pytest configuration for battery storage manager tests.

Adds the package directory to sys.path so optimizer.py can be imported
directly without pulling in homeassistant dependencies.
"""

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Mock homeassistant and all sub-modules before any imports.
# Using a module-like mock that supports attribute access and sub-imports.

_MOCK_MODULES = [
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.http",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.core",
    "homeassistant.helpers",
    "homeassistant.helpers.entity_registry",
    "homeassistant.helpers.event",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.util",
    "homeassistant.util.dt",
    "aiohttp",
]

for mod_name in _MOCK_MODULES:
    sys.modules[mod_name] = MagicMock()
