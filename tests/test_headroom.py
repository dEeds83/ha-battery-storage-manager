"""Tests for the solar headroom calculation logic.

These tests verify the coordinator's headroom logic in isolation,
without needing a running Home Assistant instance.
"""

from __future__ import annotations

import pytest


def _calc_headroom(
    slot_data: list[dict],
    cap: float = 7.5,
    max_soc: float = 90.0,
    min_soc: float = 10.0,
) -> tuple[float, float]:
    """Replicate the coordinator's headroom calculation.

    Returns (headroom_pct, dp_max_soc).
    """
    # This mirrors coordinator.py lines 942-972
    expected_surplus_kwh = 0.0
    seen_solar = False
    for h in slot_data:
        if h.get("solar_wh_hour", 0) > 0:
            seen_solar = True
            expected_surplus_kwh += h.get("solar_surplus_kwh", 0)
        elif seen_solar:
            break

    if cap > 0 and expected_surplus_kwh > 0:
        headroom_pct = min(
            expected_surplus_kwh / cap * 100,
            max_soc - min_soc - 10,
        )
        if headroom_pct > 5:
            dp_max_soc = round(max_soc - headroom_pct, 1)
        else:
            dp_max_soc = max_soc
            headroom_pct = 0.0
    else:
        dp_max_soc = max_soc
        headroom_pct = 0.0

    return headroom_pct, dp_max_soc


class TestSolarHeadroom:
    """Tests for solar headroom calculation."""

    def test_no_solar_no_headroom(self):
        """Without solar data, headroom should be 0."""
        slots = [{"price": 0.25}] * 10
        headroom, dp_max = _calc_headroom(slots)
        assert headroom == 0.0
        assert dp_max == 90.0

    def test_today_solar_surplus_creates_headroom(self):
        """Solar surplus today should create headroom."""
        slots = [
            {"price": 0.20, "solar_wh_hour": 500, "solar_surplus_kwh": 0.15},
            {"price": 0.20, "solar_wh_hour": 500, "solar_surplus_kwh": 0.15},
            {"price": 0.20, "solar_wh_hour": 500, "solar_surplus_kwh": 0.15},
            {"price": 0.20, "solar_wh_hour": 500, "solar_surplus_kwh": 0.15},
            {"price": 0.25, "solar_wh_hour": 0, "solar_surplus_kwh": 0},
        ]
        headroom, dp_max = _calc_headroom(slots)
        assert headroom > 0, "Should have headroom for solar surplus"
        assert dp_max < 90.0, "grid_max_soc should be reduced"

    def test_tomorrow_solar_does_not_affect_headroom(self):
        """Solar surplus tomorrow (after night gap) must NOT affect headroom."""
        slots = (
            # Today remaining: sun setting
            [{"price": 0.25, "solar_wh_hour": 100, "solar_surplus_kwh": 0.0}] * 2
            # Night gap
            + [{"price": 0.28, "solar_wh_hour": 0, "solar_surplus_kwh": 0.0}] * 10
            # Tomorrow: lots of solar
            + [{"price": 0.18, "solar_wh_hour": 500, "solar_surplus_kwh": 0.3}] * 8
        )
        headroom, dp_max = _calc_headroom(slots)
        # Today has 0 surplus → headroom = 0, even though tomorrow has 2.4kWh
        assert headroom == 0.0, (
            f"Tomorrow's solar should not create headroom today, got {headroom:.1f}%"
        )
        assert dp_max == 90.0

    def test_afternoon_no_surplus_no_headroom(self):
        """Late afternoon with solar but no surplus should have no headroom."""
        slots = [
            {"price": 0.25, "solar_wh_hour": 200, "solar_surplus_kwh": 0.0},
            {"price": 0.25, "solar_wh_hour": 100, "solar_surplus_kwh": 0.0},
            {"price": 0.30, "solar_wh_hour": 0, "solar_surplus_kwh": 0.0},
        ]
        headroom, dp_max = _calc_headroom(slots)
        assert headroom == 0.0
        assert dp_max == 90.0

    def test_missing_keys_no_crash(self):
        """Slots without solar keys must not crash (the KeyError bug)."""
        slots = [{"price": 0.25}] * 5
        headroom, dp_max = _calc_headroom(slots)
        assert headroom == 0.0
        assert dp_max == 90.0

    def test_partial_keys_no_crash(self):
        """Slots with only solar_wh_hour but no surplus must not crash."""
        slots = [
            {"price": 0.20, "solar_wh_hour": 300},
            {"price": 0.20, "solar_wh_hour": 0},
        ]
        headroom, dp_max = _calc_headroom(slots)
        assert headroom == 0.0  # No surplus → no headroom

    def test_headroom_capped(self):
        """Headroom must not exceed max_soc - min_soc - 10."""
        # Huge surplus: 10 kWh → would be 133% of 7.5kWh cap
        slots = [
            {"price": 0.15, "solar_wh_hour": 1000, "solar_surplus_kwh": 2.5},
        ] * 4  # 10 kWh total
        headroom, dp_max = _calc_headroom(slots)
        # max_soc(90) - min_soc(10) - 10 = 70
        assert headroom <= 70.0, f"Headroom {headroom:.1f}% exceeds cap"
        assert dp_max >= 20.0, f"dp_max_soc {dp_max:.1f}% too low"

    def test_small_surplus_ignored(self):
        """Surplus < 5% of battery should be ignored (threshold)."""
        # 0.3 kWh surplus / 7.5 kWh cap = 4% → below threshold
        slots = [
            {"price": 0.20, "solar_wh_hour": 200, "solar_surplus_kwh": 0.1},
            {"price": 0.20, "solar_wh_hour": 200, "solar_surplus_kwh": 0.1},
            {"price": 0.20, "solar_wh_hour": 200, "solar_surplus_kwh": 0.1},
            {"price": 0.25, "solar_wh_hour": 0, "solar_surplus_kwh": 0.0},
        ]
        headroom, dp_max = _calc_headroom(slots)
        assert headroom == 0.0, "Small surplus should be ignored"
        assert dp_max == 90.0
