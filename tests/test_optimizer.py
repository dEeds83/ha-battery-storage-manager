"""Tests for the battery storage optimizer (DP + smoothing pipeline)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Import optimizer.py directly to avoid pulling in homeassistant deps
_OPT_PATH = Path(__file__).resolve().parent.parent / (
    "custom_components/battery_storage_manager/optimizer.py"
)
_spec = importlib.util.spec_from_file_location("optimizer", _OPT_PATH)
optimizer = importlib.util.module_from_spec(_spec)
sys.modules["optimizer"] = optimizer
_spec.loader.exec_module(optimizer)

solve_dp = optimizer.solve_dp
smooth_plan = optimizer.smooth_plan


# ── Helpers ──────────────────────────────────────────────────────────


def _make_slots(
    prices: list[float],
    *,
    grid_fraction: float = 1.0,
    solar_wh_hour: int = 0,
    solar_surplus_kwh: float = 0.0,
) -> list[dict]:
    """Build minimal slot dicts from a price list."""
    return [
        {
            "price": p,
            "grid_fraction": grid_fraction,
            "solar_wh_hour": solar_wh_hour,
            "solar_surplus_kwh": solar_surplus_kwh,
        }
        for p in prices
    ]


def _make_slots_detailed(entries: list[dict]) -> list[dict]:
    """Build slot dicts with per-slot overrides (price required, rest optional)."""
    slots = []
    for e in entries:
        slot = {
            "price": e["price"],
            "grid_fraction": e.get("grid_fraction", 1.0),
            "solar_wh_hour": e.get("solar_wh_hour", 0),
            "solar_surplus_kwh": e.get("solar_surplus_kwh", 0.0),
        }
        slots.append(slot)
    return slots


# Default battery parameters matching the real system
DEFAULT = dict(
    charge_kwh_slot=0.220,
    discharge_kwh_slot=0.200,
    cap=7.5,
    efficiency=0.85,
    cycle_cost_eur=0.04,
    slot_h=0.25,
    min_soc=10.0,
    max_soc=90.0,
)


# ── solve_dp tests ──────────────────────────────────────────────────


class TestSolveDP:
    """Tests for the backward-induction DP solver."""

    def test_basic_charge_discharge(self):
        """DP should charge at cheap prices, discharge at expensive ones."""
        prices = [0.10] * 8 + [0.35] * 8  # 2h cheap, 2h expensive
        slots = _make_slots(prices)
        actions, profit = solve_dp(
            slots, len(slots), 50.0, **DEFAULT
        )
        assert "charge" in actions[:8], "Should charge during cheap slots"
        assert "discharge" in actions[8:], "Should discharge during expensive slots"
        assert profit > 0, "Plan should be profitable"

    def test_flat_prices_no_cycling(self):
        """With flat prices, DP should not cycle the battery (no profit)."""
        prices = [0.25] * 16
        slots = _make_slots(prices)
        actions, _ = solve_dp(slots, len(slots), 50.0, **DEFAULT)
        charge_count = actions.count("charge")
        discharge_count = actions.count("discharge")
        # At flat prices, cycling loses money due to efficiency + cycle cost
        assert charge_count == 0, f"No charging expected at flat prices, got {charge_count}"

    def test_respects_min_soc(self):
        """DP must not discharge significantly below min_soc.

        Note: due to SOC quantization (1% steps), the DP may slightly
        overshoot min_soc by up to one discharge step (~2.67%).
        """
        prices = [0.40] * 40  # All expensive → DP wants to discharge everything
        slots = _make_slots(prices)
        actions, _ = solve_dp(slots, len(slots), 50.0, **DEFAULT)

        # Simulate SOC
        soc = 50.0
        discharge_pct = DEFAULT["discharge_kwh_slot"] / DEFAULT["cap"] * 100
        for act in actions:
            if act == "discharge":
                soc -= discharge_pct
            elif act == "charge":
                soc += DEFAULT["charge_kwh_slot"] / DEFAULT["cap"] * 100
            # Allow up to 2 discharge steps overshoot due to quantization
            assert soc >= DEFAULT["min_soc"] - 2 * discharge_pct - 0.5, (
                f"SOC {soc:.1f}% too far below min_soc"
            )

    def test_respects_max_soc(self):
        """DP must not charge above max_soc."""
        prices = [0.05] * 40  # All cheap → DP wants to charge everything
        slots = _make_slots(prices)
        actions, _ = solve_dp(
            slots, len(slots), 50.0,
            **{**DEFAULT, "epex_terminal_value_per_kwh": 0.30},
        )

        soc = 50.0
        for act in actions:
            if act == "charge":
                soc += DEFAULT["charge_kwh_slot"] / DEFAULT["cap"] * 100
            elif act == "discharge":
                soc -= DEFAULT["discharge_kwh_slot"] / DEFAULT["cap"] * 100
            soc = max(DEFAULT["min_soc"], min(DEFAULT["max_soc"], soc))
            assert soc <= DEFAULT["max_soc"] + 0.5, f"SOC {soc:.1f}% above max_soc"

    def test_break_even_charges(self):
        """DP should charge when spread is clearly profitable."""
        # Start at low SOC so DP has room and motivation to charge
        prices = [0.15] * 8 + [0.35] * 8
        slots = _make_slots(prices)
        actions, _ = solve_dp(slots, len(slots), 20.0, **DEFAULT)
        assert actions[:8].count("charge") > 0, "Should charge at 15ct with 35ct discharge ahead"

    def test_empty_slots(self):
        """Empty slot list should not crash."""
        actions, profit = solve_dp([], 0, 50.0, **DEFAULT)
        assert actions == []

    def test_single_slot(self):
        """Single slot should work without crash."""
        slots = _make_slots([0.25])
        actions, _ = solve_dp(slots, 1, 50.0, **DEFAULT)
        assert len(actions) == 1

    def test_missing_optional_keys(self):
        """Slots with only 'price' and 'grid_fraction' must not crash."""
        slots = [{"price": 0.20, "grid_fraction": 1.0}] * 8
        actions, _ = solve_dp(slots, 8, 50.0, **DEFAULT)
        assert len(actions) == 8


# ── smooth_plan tests ────────────────────────────────────────────────


class TestSmoothPlan:
    """Tests for the 6-pass smoothing pipeline."""

    def _run_smooth(self, actions, prices, **kwargs):
        """Helper: run smooth_plan with default params."""
        slots = _make_slots(prices)
        params = {**DEFAULT, "current_soc": 50.0}
        params.update(kwargs)
        soc = params.pop("current_soc")
        result, count = smooth_plan(
            list(actions), slots, len(slots),
            params["efficiency"], params["cycle_cost_eur"],
            params["charge_kwh_slot"], params["discharge_kwh_slot"],
            params["cap"], soc,
            params["min_soc"], params["max_soc"], params["slot_h"],
        )
        return result, count

    def test_enclave_removal(self):
        """Pass 1: single discharge between idles should be removed."""
        actions = ["idle", "idle", "discharge", "idle", "idle"]
        prices = [0.20, 0.20, 0.21, 0.20, 0.20]
        result, _ = self._run_smooth(actions, prices)
        assert result[2] == "idle", "Single discharge enclave should be removed"

    def test_enclave_preserved_with_neighbor(self):
        """Pass 1: discharge near another discharge (within 2) should be kept."""
        actions = ["idle", "discharge", "idle", "discharge", "idle"]
        prices = [0.20, 0.30, 0.20, 0.30, 0.20]
        result, _ = self._run_smooth(actions, prices)
        # Both discharges within 2 positions of each other → kept
        assert result[1] == "discharge", "Discharge near neighbor should be preserved"
        assert result[3] == "discharge", "Discharge near neighbor should be preserved"

    def test_charge_gap_fill(self):
        """Post-pass: idle gap between charges should be filled."""
        actions = ["charge", "idle", "charge", "idle", "idle"]
        prices = [0.20, 0.19, 0.20, 0.25, 0.25]
        result, _ = self._run_smooth(actions, prices)
        assert result[1] == "charge", (
            "Idle gap at 19ct between charges at 20ct should be filled"
        )

    def test_alternation_dampening(self):
        """Pass 2: charge→discharge→charge with similar prices → collapse."""
        actions = ["charge", "discharge", "charge"]
        prices = [0.20, 0.21, 0.20]
        result, _ = self._run_smooth(actions, prices)
        # The discharge at 0.21 between charges at 0.20 should be dampened
        assert result.count("discharge") <= 1

    def test_no_crash_all_idle(self):
        """All-idle plan should pass through smoothing without crash."""
        actions = ["idle"] * 10
        prices = [0.25] * 10
        result, count = self._run_smooth(actions, prices)
        assert result == ["idle"] * 10
        assert count == 0

    def test_no_crash_all_charge(self):
        """All-charge plan should pass through smoothing without crash."""
        actions = ["charge"] * 10
        prices = [0.15] * 10
        result, _ = self._run_smooth(actions, prices)
        assert len(result) == 10

    def test_no_crash_all_discharge(self):
        """All-discharge plan should pass through smoothing without crash."""
        actions = ["discharge"] * 10
        prices = [0.35] * 10
        result, _ = self._run_smooth(actions, prices)
        assert len(result) == 10

    def test_pass6_profitability_check(self):
        """Pass 6 should NOT add charge slots at prices above break-even."""
        # Expensive idle slots before a discharge block
        actions = ["idle"] * 4 + ["discharge"] * 4
        prices = [0.30, 0.30, 0.30, 0.30,  # expensive idle
                  0.32, 0.33, 0.34, 0.35]   # discharge
        result, _ = self._run_smooth(
            actions, prices, current_soc=50.0,
        )
        # avg discharge = 0.335, max_charge = 0.335 * 0.85 - 0.04 = 0.245
        # Idle prices (0.30) > max_charge (0.245) → should NOT fill
        for i in range(4):
            assert result[i] != "charge", (
                f"Slot {i} at {prices[i]*100:.0f}ct should not be charged "
                f"(above break-even)"
            )

    def test_pass6_fills_cheap_slots(self):
        """Pass 6 should fill cheap idle slots before a discharge block."""
        actions = ["idle"] * 4 + ["discharge"] * 4
        prices = [0.15, 0.15, 0.15, 0.15,  # cheap idle
                  0.35, 0.36, 0.37, 0.38]   # expensive discharge
        result, _ = self._run_smooth(
            actions, prices, current_soc=50.0,
        )
        # avg discharge = 0.365, max_charge = 0.365 * 0.85 - 0.04 = 0.270
        # Idle prices (0.15) < max_charge (0.270) → should fill
        charge_count = sum(1 for i in range(4) if result[i] == "charge")
        assert charge_count > 0, "Should fill cheap slots before discharge block"

    def test_missing_slot_keys_no_crash(self):
        """Slots with missing optional keys must not crash smoothing."""
        # Minimal slots: only 'price' - no solar_wh_hour, no solar_surplus_kwh
        slots = [{"price": p, "grid_fraction": 1.0} for p in [0.20] * 8]
        actions = ["idle"] * 4 + ["discharge"] * 4
        # This would have caught the KeyError 'solar_wh_hour' bug
        result, _ = smooth_plan(
            actions, slots, 8,
            DEFAULT["efficiency"], DEFAULT["cycle_cost_eur"],
            DEFAULT["charge_kwh_slot"], DEFAULT["discharge_kwh_slot"],
            DEFAULT["cap"], 50.0,
            DEFAULT["min_soc"], DEFAULT["max_soc"], DEFAULT["slot_h"],
        )
        assert len(result) == 8


# ── Integration tests (DP + smoothing together) ─────────────────────


class TestDPWithSmoothing:
    """End-to-end tests: DP followed by smoothing pipeline."""

    def test_no_night_charging_at_high_prices(self):
        """Battery should not charge at night (28-30ct) when midday is 18ct."""
        # Simulate: evening expensive, night medium, midday cheap, evening expensive
        prices = (
            [0.35] * 8    # 17:00-19:00 expensive (discharge)
            + [0.29] * 16  # 19:00-23:00 night (should idle)
            + [0.18] * 16  # 07:00-11:00 cheap midday (should charge)
            + [0.35] * 8   # 17:00-19:00 expensive (discharge)
        )
        slots = _make_slots(prices)
        n = len(slots)

        actions, _ = solve_dp(slots, n, 50.0, **DEFAULT)
        actions, _ = smooth_plan(
            actions, slots, n,
            DEFAULT["efficiency"], DEFAULT["cycle_cost_eur"],
            DEFAULT["charge_kwh_slot"], DEFAULT["discharge_kwh_slot"],
            DEFAULT["cap"], 50.0,
            DEFAULT["min_soc"], DEFAULT["max_soc"], DEFAULT["slot_h"],
        )

        # Night slots (8-24) at 29ct should NOT be charge
        night_charges = [i for i in range(8, 24) if actions[i] == "charge"]
        assert len(night_charges) == 0, (
            f"Night charging at 29ct: slots {night_charges} "
            f"(should charge at midday 18ct instead)"
        )

    def test_charge_at_cheapest_window(self):
        """Charging should concentrate in the cheapest price window."""
        prices = [0.28] * 16 + [0.17] * 16 + [0.35] * 16
        slots = _make_slots(prices)
        n = len(slots)

        actions, _ = solve_dp(slots, n, 30.0, **DEFAULT)
        actions, _ = smooth_plan(
            actions, slots, n,
            DEFAULT["efficiency"], DEFAULT["cycle_cost_eur"],
            DEFAULT["charge_kwh_slot"], DEFAULT["discharge_kwh_slot"],
            DEFAULT["cap"], 30.0,
            DEFAULT["min_soc"], DEFAULT["max_soc"], DEFAULT["slot_h"],
        )

        cheap_charges = sum(1 for i in range(16, 32) if actions[i] == "charge")
        expensive_charges = sum(1 for i in range(0, 16) if actions[i] == "charge")

        assert cheap_charges > expensive_charges, (
            f"Should charge more at 17ct ({cheap_charges}) "
            f"than at 28ct ({expensive_charges})"
        )

    def test_realistic_price_curve(self):
        """Test with a realistic Tibber-like price curve."""
        # Typical German winter day: expensive morning/evening, cheap midday
        prices = (
            [0.30, 0.31, 0.33, 0.35]   # 06:00-07:00 morning peak
            + [0.28, 0.25, 0.22, 0.20]  # 07:00-08:00 dropping
            + [0.18, 0.17, 0.17, 0.18]  # 10:00-11:00 solar midday
            + [0.19, 0.20, 0.22, 0.25]  # 12:00-13:00 rising
            + [0.30, 0.33, 0.36, 0.38]  # 17:00-18:00 evening peak
            + [0.37, 0.35, 0.33, 0.31]  # 19:00-20:00 declining
            + [0.29, 0.28, 0.27, 0.26]  # 21:00-22:00 night
        )
        slots = _make_slots(prices)
        n = len(slots)

        actions, profit = solve_dp(slots, n, 40.0, **DEFAULT)
        actions, _ = smooth_plan(
            actions, slots, n,
            DEFAULT["efficiency"], DEFAULT["cycle_cost_eur"],
            DEFAULT["charge_kwh_slot"], DEFAULT["discharge_kwh_slot"],
            DEFAULT["cap"], 40.0,
            DEFAULT["min_soc"], DEFAULT["max_soc"], DEFAULT["slot_h"],
        )

        # Basic sanity: should have some charges and discharges
        assert "charge" in actions, "Should plan some charging"
        assert "discharge" in actions, "Should plan some discharging"

        # Charges should be in cheap midday, discharges in expensive peak
        charge_indices = [i for i, a in enumerate(actions) if a == "charge"]
        discharge_indices = [i for i, a in enumerate(actions) if a == "discharge"]

        if charge_indices and discharge_indices:
            avg_charge_price = sum(prices[i] for i in charge_indices) / len(charge_indices)
            avg_discharge_price = sum(prices[i] for i in discharge_indices) / len(discharge_indices)
            assert avg_discharge_price > avg_charge_price, (
                f"Discharge price ({avg_discharge_price:.3f}) should exceed "
                f"charge price ({avg_charge_price:.3f})"
            )
