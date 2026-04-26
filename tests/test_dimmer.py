"""Smoke tests for the dimmable-charger configuration shape.

Tests the data-structure invariants and migration logic without
pulling in the HomeAssistant runtime. Device-level behaviour (set_value
service calls, PID, etc.) is covered by manual integration testing
against a live HA instance via the MCP tool.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Direct file import (same pattern as test_optimizer.py).
_CONST_PATH = Path(__file__).resolve().parent.parent / (
    "custom_components/battery_storage_manager/const.py"
)
_spec = importlib.util.spec_from_file_location("bsm_const", _CONST_PATH)
const = importlib.util.module_from_spec(_spec)
sys.modules["bsm_const"] = const
_spec.loader.exec_module(const)


def _build_charger_entry(c: dict) -> dict:
    """Mirror BatteryStorageCoordinator._build_charger_entry."""
    return {
        "switch": c.get("switch", ""),
        "power": c.get("power", 0),
        "power_entity": c.get("power_entity", ""),
        "actual_power_entity": c.get("actual_power_entity", ""),
        "type": c.get("type", const.CHARGER_TYPE_SWITCH),
        "min_power": c.get("min_power", 0),
        "active": False,
        "target_power": 0.0,
        "measured_power": None,
    }


def _migrate_v2_to_v3(chargers: list[dict]) -> list[dict]:
    """Mirror the inline migration in __init__.async_migrate_entry."""
    for c in chargers:
        c.setdefault("type", const.CHARGER_TYPE_SWITCH)
        c.setdefault("min_power", 0)
    return chargers


# ── Data shape ──────────────────────────────────────────────────────


def test_const_charger_types_defined():
    assert const.CHARGER_TYPE_SWITCH == "switch"
    assert const.CHARGER_TYPE_DIMMER == "dimmer"


def test_dimmer_conf_keys_present():
    for k in (
        "CONF_CHARGER_TYPE",
        "CONF_DIMMER_POWER_ENTITY",
        "CONF_DIMMER_ENABLE_SWITCH",
        "CONF_DIMMER_MAX_POWER",
        "CONF_DIMMER_MIN_POWER",
        "CONF_DIMMER_ACTUAL_POWER_ENTITY",
    ):
        assert hasattr(const, k), f"Missing const: {k}"


def test_charger_entry_defaults_to_switch():
    e = _build_charger_entry({"switch": "switch.charger1", "power": 440})
    assert e["type"] == "switch"
    assert e["min_power"] == 0
    assert e["target_power"] == 0.0
    assert e["active"] is False


def test_charger_entry_dimmer_keeps_type():
    e = _build_charger_entry({
        "type": "dimmer",
        "power_entity": "number.dimmer_setpoint",
        "power": 1000,
        "min_power": 50,
    })
    assert e["type"] == "dimmer"
    assert e["power_entity"] == "number.dimmer_setpoint"
    assert e["power"] == 1000
    assert e["min_power"] == 50


# ── Migration ────────────────────────────────────────────────────────


def test_migration_v2_to_v3_adds_type_switch():
    legacy = [
        {"switch": "switch.c1", "power": 440},
        {"switch": "switch.c2", "power": 440, "power_entity": "sensor.c2_w"},
    ]
    migrated = _migrate_v2_to_v3(legacy)
    assert all(c["type"] == "switch" for c in migrated)
    assert all(c["min_power"] == 0 for c in migrated)
    # Existing fields preserved.
    assert migrated[1]["power_entity"] == "sensor.c2_w"


def test_migration_idempotent():
    """Running migration twice must not change a v3 record."""
    v3 = [{"switch": "switch.c1", "power": 440, "type": "dimmer", "min_power": 50}]
    once = _migrate_v2_to_v3(list(v3))
    twice = _migrate_v2_to_v3(list(once))
    assert once == twice


# ── Dimmer power clamping logic ─────────────────────────────────────


def _clamp_dimmer_target(value_w: float, max_p: int, min_p: int) -> int:
    """Mirror the clamp+min-cutoff logic in DevicesMixin._set_dimmer_power."""
    target = max(0, min(max_p, round(value_w)))
    if 0 < target < min_p:
        target = 0
    return target


def test_dimmer_clamp_within_range():
    assert _clamp_dimmer_target(320, 1000, 0) == 320


def test_dimmer_clamp_max():
    assert _clamp_dimmer_target(2000, 1000, 0) == 1000


def test_dimmer_clamp_negative_zero():
    assert _clamp_dimmer_target(-50, 1000, 0) == 0


def test_dimmer_below_min_zeros_out():
    assert _clamp_dimmer_target(40, 1000, 100) == 0


def test_dimmer_at_min_keeps_value():
    assert _clamp_dimmer_target(100, 1000, 100) == 100


# ── Dimmer zero-feed step computation ───────────────────────────────


def _dimmer_step(current_target: float, grid_w: float, gain: float = 0.8) -> float:
    """Mirror the one-step adjust in _regulate_dimmer_zero_feed."""
    return current_target + (-grid_w) * gain


def test_dimmer_step_export_increases_target():
    # grid_ema = -200 (export), current=300 → +200*0.8 = 460
    assert _dimmer_step(300, -200) == 460


def test_dimmer_step_import_decreases_target():
    # grid_ema = 100 (import), current=500 → -100*0.8 = 420
    assert _dimmer_step(500, 100) == 420


def test_dimmer_step_balanced_no_change():
    assert _dimmer_step(300, 0) == 300
