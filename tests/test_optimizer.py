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
force_pre_solar_discharge = optimizer.force_pre_solar_discharge
remove_dp_discharge_enclaves = optimizer.remove_dp_discharge_enclaves
remove_unprofitable_predischarge = optimizer.remove_unprofitable_predischarge
compute_presolar_discharge_hours = optimizer.compute_presolar_discharge_hours


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

    def test_discharge_chosen_despite_solar_absorption(self):
        """v2.50.0: Discharge muss gewaehlt werden, auch wenn paralleler
        Solar-Surplus den SOC im selben Slot quasi vollstaendig wieder
        hochzieht — solange echte Netto-Abgabe bleibt (net_export > 0).

        Diskriminierung: Solar-Absorption (0.145 kWh) liegt knapp unter
        dem Discharge-Delta (0.150 kWh). Die Netto-SOC-Aenderung
        (-0.150 + 0.145 = -0.005 kWh) ist kleiner als ein
        Diskretisierungs-Schritt -> ``new_si == si``. Die ALTE Regel
        ``new_si < si`` haette den Discharge daher verworfen; nur der
        net-export-Check (v2.50.0) akzeptiert ihn. So testet der Fall
        wirklich den Fix und nicht nur eine ohnehin sinkende SOC-Kurve.
        """
        entries = [
            {"price": 0.30, "solar_surplus_kwh": 0.145, "discharge_kwh": 0.150},
            {"price": 0.32, "solar_surplus_kwh": 0.145, "discharge_kwh": 0.150},
            {"price": 0.35, "solar_surplus_kwh": 0.145, "discharge_kwh": 0.150},
            {"price": 0.38, "solar_surplus_kwh": 0.145, "discharge_kwh": 0.150},
            # Spaeter ohne Solar — Vergleichsfaelle
            {"price": 0.20, "solar_surplus_kwh": 0.0, "discharge_kwh": 0.200},
            {"price": 0.18, "solar_surplus_kwh": 0.0, "discharge_kwh": 0.200},
        ]
        # _make_slots_detailed kennt discharge_kwh nicht; selbst bauen
        slots = []
        for e in entries:
            slots.append({
                "price": e["price"],
                "grid_fraction": 1.0,
                "solar_wh_hour": 100,
                "solar_surplus_kwh": e["solar_surplus_kwh"],
                "discharge_kwh": e["discharge_kwh"],
            })
        # Hoher Start-SOC (80%) damit Discharge problemlos moeglich
        actions, _ = solve_dp(
            slots, len(slots), 80.0,
            charge_kwh_slot=0.0,  # kein laden, nur entladen erlaubt
            discharge_kwh_slot=0.200,
            cap=DEFAULT["cap"], efficiency=DEFAULT["efficiency"],
            cycle_cost_eur=DEFAULT["cycle_cost_eur"],
            slot_h=DEFAULT["slot_h"],
            min_soc=DEFAULT["min_soc"], max_soc=DEFAULT["max_soc"],
        )
        # Mindestens ein Discharge in den teuren Slots (0..3, mit Solar)
        discharges_in_solar = sum(1 for a in actions[:4] if a == "discharge")
        assert discharges_in_solar >= 1, (
            f"DP haette Discharge waehlen muessen trotz Solar-Surplus, "
            f"actions={actions}"
        )

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

    def test_local_discharge_swap(self):
        """Pass 3b: discharge followed by more expensive idle should swap."""
        # Multiple discharges so Pass 1 doesn't remove them as enclaves.
        # The last discharge (0.30) is followed by an expensive idle (0.38).
        actions = ["discharge", "discharge", "discharge", "idle", "idle"]
        prices = [0.32, 0.33, 0.30, 0.38, 0.25]
        result, _ = self._run_smooth(actions, prices)
        # Pass 3b: slot 2 (discharge 0.30) → slot 3 (idle 0.38) should swap
        assert result[3] == "discharge", "More expensive idle after discharge should become discharge"
        assert result[2] != "discharge" or result[4] != "idle", (
            "At least one swap should have occurred"
        )

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


# ── force_pre_solar_discharge tests (v2.49.0) ───────────────────────


class TestForcePreSolarDischarge:
    """Tests fuer den aktiven Pre-Solar-Discharge-Pass."""

    def _params(self, **overrides) -> dict:
        params = dict(
            charge_kwh_slot=DEFAULT["charge_kwh_slot"],
            discharge_kwh_slot=DEFAULT["discharge_kwh_slot"],
            cap=DEFAULT["cap"],
            min_soc=DEFAULT["min_soc"],
            max_soc=DEFAULT["max_soc"],
        )
        params.update(overrides)
        return params

    def test_no_force_when_no_overflow(self):
        """Kein Eingriff wenn SOC nie max_soc trifft."""
        # SOC startet niedrig, wenig Solar, kein Overflow zu erwarten.
        slots = _make_slots([0.20] * 8, solar_surplus_kwh=0.05)
        actions = ["idle"] * len(slots)
        forced, kwh, _idx = force_pre_solar_discharge(
            actions, slots, current_soc=30.0, **self._params()
        )
        assert forced == 0
        assert kwh == 0.0
        assert all(a == "idle" for a in actions)

    def test_forces_when_battery_would_overflow(self):
        """Hoher SOC + viel Solar -> Pass muss Idle zu Discharge konvertieren."""
        # 6 Slots Idle (verschiedene Preise) + danach 4 Slots mit dickem
        # Solar-Surplus. SOC startet bei 85% (nur 5% Headroom = 0.375 kWh)
        # -> bei jeweils 1.0 kWh Solar-Surplus fliegen viele kWh als Export.
        entries = (
            [{"price": 0.10}] * 2
            + [{"price": 0.30}] * 2  # teuerste Idle-Slots, sollen zuerst dran
            + [{"price": 0.15}] * 2
            + [{"price": 0.20, "solar_surplus_kwh": 1.0}] * 4
        )
        slots = _make_slots_detailed(entries)
        actions = ["idle"] * len(slots)
        forced, kwh, _idx = force_pre_solar_discharge(
            actions, slots, current_soc=85.0, **self._params()
        )
        assert forced > 0, "Bei Solar-Overflow muss mind. 1 Slot forciert werden"
        assert kwh > 0.0
        # Hauptkriterium: Mind. eine Discharge-Aktion existiert vor den Solar-Slots
        discharge_indices = [i for i, a in enumerate(actions) if a == "discharge"]
        assert discharge_indices, "Pass muss Discharge erzeugen"
        assert all(i < 6 for i in discharge_indices), (
            "Discharge muss vor den Solar-Slots liegen"
        )

    def test_prefers_expensive_slots(self):
        """Bei mehreren Kandidaten wird der teuerste Idle-Slot bevorzugt."""
        entries = (
            [{"price": 0.10}] * 2  # billig
            + [{"price": 0.30}] * 1  # teuer (bevorzugen!)
            + [{"price": 0.12}] * 2  # billig
            + [{"price": 0.20, "solar_surplus_kwh": 0.5}] * 3
        )
        slots = _make_slots_detailed(entries)
        actions = ["idle"] * len(slots)
        forced, _, _idx = force_pre_solar_discharge(
            actions, slots, current_soc=87.0, **self._params()
        )
        # Unbedingt: der Pass MUSS hier forcieren (sonst testet die
        # Slot-Assertion nichts — vacuous pass).
        assert forced > 0, "Bei Solar-Overflow muss mind. 1 Slot forciert werden"
        # Der teuerste Slot (index 2, Preis 0.30) muss zu Discharge geworden sein
        assert actions[2] == "discharge", (
            f"Teuerster Idle-Slot sollte zuerst forciert werden, "
            f"got actions={actions}"
        )

    def test_respects_min_soc(self):
        """Forced Discharge darf nicht unter min_soc treiben."""
        # SOC startet knapp ueber min_soc (12%), viel Solar erwartet.
        entries = (
            [{"price": 0.30}] * 4  # viele teure Idle-Slots
            + [{"price": 0.20, "solar_surplus_kwh": 2.0}] * 2
        )
        slots = _make_slots_detailed(entries)
        actions = ["idle"] * len(slots)
        force_pre_solar_discharge(
            actions, slots, current_soc=12.0, **self._params()
        )
        # Simuliere SOC und pruefe min_soc-Bound
        soc = 12.0
        for i, act in enumerate(actions):
            if act == "discharge":
                slot_dis = slots[i].get("discharge_kwh", DEFAULT["discharge_kwh_slot"])
                delta = min(slot_dis, max(0.0, (soc - DEFAULT["min_soc"]) / 100 * DEFAULT["cap"]))
                soc -= delta / DEFAULT["cap"] * 100
            # Solar-Absorption (nur bis max_soc)
            surplus = slots[i].get("solar_surplus_kwh", 0)
            soc = min(DEFAULT["max_soc"], soc + surplus / DEFAULT["cap"] * 100)
            assert soc >= DEFAULT["min_soc"] - 0.5, (
                f"SOC unter min_soc: {soc:.1f} an Slot {i}"
            )

    def test_skips_when_no_idle_candidates(self):
        """Wenn alle Slots vor Overflow charge/discharge sind, kein Eingriff."""
        entries = (
            [{"price": 0.10}] * 3  # charge wird's
            + [{"price": 0.20, "solar_surplus_kwh": 1.0}] * 3
        )
        slots = _make_slots_detailed(entries)
        actions = ["charge", "charge", "charge", "idle", "idle", "idle"]
        forced, _, _idx = force_pre_solar_discharge(
            actions, slots, current_soc=85.0, **self._params()
        )
        # Kein idle vor Overflow -> kein Eingriff
        assert forced == 0
        assert actions[:3] == ["charge", "charge", "charge"]

    def test_promotes_hold_too(self):
        """Hold-Slots sind ebenfalls Kandidaten (wie idle)."""
        entries = (
            [{"price": 0.25}] * 3  # hold-Slots
            + [{"price": 0.15, "solar_surplus_kwh": 1.5}] * 3
        )
        slots = _make_slots_detailed(entries)
        actions = ["hold"] * 3 + ["idle"] * 3
        forced, _, _idx = force_pre_solar_discharge(
            actions, slots, current_soc=87.0, **self._params()
        )
        assert forced > 0
        discharge_in_hold_range = sum(
            1 for i in range(3) if actions[i] == "discharge"
        )
        assert discharge_in_hold_range > 0, "Hold-Slots muessen umwandelbar sein"

    def test_excludes_candidate_with_own_solar_surplus(self):
        """Idle/Hold-Kandidaten mit eigenem Solar-Surplus > 0.05 kWh werden
        NICHT promotet (zero-export verbietet Entladen waehrend PV einspeist).

        Der teuerste Kandidat (Slot 0, 0.30) traegt Surplus 0.07 und muss
        uebersprungen werden; stattdessen wird der naechstteure Surplus-freie
        Slot (Slot 1, 0.25) forciert.
        """
        entries = (
            [{"price": 0.30, "solar_surplus_kwh": 0.07}]   # teuer, ABER Surplus -> exclude
            + [{"price": 0.25}]                            # eligible
            + [{"price": 0.20}]                            # eligible
            + [{"price": 0.18, "solar_surplus_kwh": 1.0}] * 3  # Overflow
        )
        slots = _make_slots_detailed(entries)
        actions = ["idle"] * len(slots)
        forced, _kwh, indices = force_pre_solar_discharge(
            actions, slots, current_soc=87.0, **self._params()
        )
        assert forced > 0
        # Slot 0 trotz hoechstem Preis NICHT promotet (Solar-Surplus).
        assert actions[0] != "discharge", (
            f"Slot mit eigenem Solar-Surplus darf nicht entladen, got {actions}"
        )
        assert 0 not in indices
        # Naechstteurer Surplus-freier Slot wurde forciert.
        assert actions[1] == "discharge", (
            f"Surplus-freier Kandidat sollte forciert werden, got {actions}"
        )

    def test_returns_forced_indices(self):
        """v2.50.1: force_pre_solar_discharge gibt die promovierten Indices zurueck."""
        entries = (
            [{"price": 0.30}] * 4
            + [{"price": 0.20, "solar_surplus_kwh": 1.0}] * 4
        )
        slots = _make_slots_detailed(entries)
        actions = ["idle"] * len(slots)
        forced, _kwh, indices = force_pre_solar_discharge(
            actions, slots, current_soc=87.0, **self._params()
        )
        assert forced > 0
        assert isinstance(indices, set)
        # Jede Promotion muss als Index erscheinen
        assert len(indices) == forced
        # Alle Indices liegen vor den Solar-Slots
        assert all(i < 4 for i in indices)
        # Genau diese Slots sind jetzt discharge
        for i in indices:
            assert actions[i] == "discharge"

    def test_resolves_overflow_when_candidates_remain(self):
        """v2.52.0: Loop darf nicht via veraltetem excess_kwh-Accounting
        abbrechen, solange echtes Clipping bleibt UND Kandidaten frei sind.

        Szenario: 3 teure Idle-Kandidaten, 1 Charge-Slot (verbraucht
        freigewordenen Headroom), dann anhaltender Solar-Overflow.
        Jeder Discharge gibt 0.20 kWh frei (excess_addressed += 0.20),
        aber der Charge frisst einen Teil — die 1:1-Schaetzung
        ueberschaetzt die Entlastung und der alte Guard stoppt zu frueh
        (forced=2, Restclipping 0.15). Korrekt: weiter bis Overflow weg.
        """
        entries = (
            [{"price": 0.30}] * 3       # teure Idle-Kandidaten
            + [{"price": 0.05}]          # Charge frisst Headroom
            + [{"price": 0.20, "solar_surplus_kwh": 0.10}] * 4  # Overflow
        )
        slots = _make_slots_detailed(entries)
        # discharge_kwh klein (0.20) damit Entlastung < freigegebenes delta
        for s in slots:
            s["discharge_kwh"] = 0.20
        actions = ["idle"] * 3 + ["charge"] + ["idle"] * 4
        params = self._params()
        params["charge_kwh_slot"] = 0.30
        params["discharge_kwh_slot"] = 0.20
        forced, _kwh, _idx = force_pre_solar_discharge(
            actions, slots, current_soc=88.0, **params
        )

        # Restclipping nach dem Pass simulieren.
        soc = 88.0
        cap = DEFAULT["cap"]
        residual_clip = 0.0
        for i, a in enumerate(actions):
            if a == "charge":
                soc += min(0.30, max(0.0, (90.0 - soc) / 100 * cap)) / cap * 100
            elif a == "discharge":
                sd = slots[i]["discharge_kwh"]
                soc -= min(sd, max(0.0, (soc - 10.0) / 100 * cap)) / cap * 100
            if a != "charge":
                sp = slots[i]["solar_surplus_kwh"]
                free = max(0.0, (90.0 - soc) / 100 * cap)
                residual_clip += max(0.0, sp - min(sp, free))
                soc += min(sp, free) / cap * 100
            soc = max(10.0, min(90.0, soc))

        assert residual_clip < 0.01, (
            f"Overflow nicht aufgeloest trotz freier Kandidaten: "
            f"Restclipping {residual_clip:.3f}, forced={forced}, actions={actions}"
        )


# ── remove_dp_discharge_enclaves tests (v2.50.1) ────────────────────


class TestRemoveDPDischargeEnclaves:
    """Tests fuer das Post-Force-Enclave-Cleanup."""

    def test_removes_isolated_dp_discharge(self):
        """Einzelner Discharge zwischen Hold-Slots wird zu idle."""
        actions = ["hold", "hold", "discharge", "hold", "hold"]
        slots = _make_slots([0.20] * 5)
        demoted = remove_dp_discharge_enclaves(actions, slots, protect_indices=set())
        assert demoted == 1
        assert actions[2] == "idle"

    def test_protects_force_promoted_slot(self):
        """Slots in protect_indices bleiben unangetastet."""
        actions = ["hold", "hold", "discharge", "hold", "hold"]
        slots = _make_slots([0.20] * 5)
        demoted = remove_dp_discharge_enclaves(actions, slots, protect_indices={2})
        assert demoted == 0
        assert actions[2] == "discharge"

    def test_keeps_real_discharge_block(self):
        """Block aus mehreren Discharges bleibt komplett erhalten."""
        actions = ["hold", "discharge", "discharge", "discharge", "hold"]
        slots = _make_slots([0.20] * 5)
        demoted = remove_dp_discharge_enclaves(actions, slots, protect_indices=set())
        assert demoted == 0
        assert actions == ["hold", "discharge", "discharge", "discharge", "hold"]

    def test_keeps_block_with_one_slot_gap(self):
        """Discharge + 1-Slot-Lueck + Discharge gilt als Block (nicht entfernen)."""
        actions = ["hold", "discharge", "hold", "discharge", "hold"]
        slots = _make_slots([0.20] * 5)
        demoted = remove_dp_discharge_enclaves(actions, slots, protect_indices=set())
        # has_nearby greift (Index 2 hat Discharge bei i-1 und i+1, also direkt;
        # Index 1 hat Discharge bei i+2; Index 3 hat Discharge bei i-2)
        assert demoted == 0


# ── remove_unprofitable_predischarge tests (v2.52.0) ────────────────


class TestRemoveUnprofitablePredischarge:
    """Tests fuer das Entfernen von Verlust-Roundtrip-Discharges.

    Pathologie (live beobachtet, v2.50.0): Der DP entlaedt morgens
    Ueberschuss-Energie (Preis > Terminal-Value), die der Abend-Peak
    wegen discharge_kwh-Cap nicht aufnehmen kann; smooth_plan Pass 6
    fuellt mittags wieder auf -> Sell-high/buy-low-Zyklus der
    round-trip Geld verliert. Dieser Pass demotet solche Pre-Charge-
    Discharges zu idle, wenn der Roundtrip nicht profitabel ist.
    """

    def test_demotes_discharge_before_cheaper_charge(self):
        """Discharge vor spaeterem guenstigeren Charge -> demote.

        Verkauf 17 ct (x0.85 = 14.45 ct geliefert), Rueckkauf 12.5 ct
        + 4 ct Zyklus = 16.5 ct -> Roundtrip -2.05 ct/kWh -> Verlust.
        """
        prices = [0.17, 0.17, 0.16, 0.125, 0.125, 0.35, 0.35]
        slots = _make_slots(prices)
        actions = [
            "discharge", "discharge", "idle",
            "charge", "charge", "discharge", "discharge",
        ]
        demoted = remove_unprofitable_predischarge(
            actions, slots, efficiency=0.85, cycle_cost_eur=0.04,
            protect_indices=set(),
        )
        assert demoted == 2, f"Beide Morgen-Discharges demoten, got {actions}"
        assert actions[0] == "idle"
        assert actions[1] == "idle"
        # Abend-Discharges ohne spaeteren Charge bleiben.
        assert actions[5] == "discharge"
        assert actions[6] == "discharge"

    def test_keeps_profitable_multipeak_predischarge(self):
        """Discharge im teuren Peak vor billigem Charge bleibt.

        Verkauf 40 ct (x0.85 = 34 ct), Rueckkauf 10 ct + 4 ct = 14 ct
        -> Roundtrip +20 ct/kWh -> profitabel, NICHT demoten.
        """
        prices = [0.40, 0.40, 0.10, 0.10, 0.40, 0.40]
        slots = _make_slots(prices)
        actions = [
            "discharge", "discharge", "charge",
            "charge", "discharge", "discharge",
        ]
        demoted = remove_unprofitable_predischarge(
            actions, slots, efficiency=0.85, cycle_cost_eur=0.04,
            protect_indices=set(),
        )
        assert demoted == 0
        assert actions[0] == "discharge"
        assert actions[1] == "discharge"

    def test_protects_force_promoted_slots(self):
        """force_pre_solar-Indizes (protect) werden nie demotet."""
        prices = [0.17, 0.17, 0.16, 0.125, 0.125, 0.35]
        slots = _make_slots(prices)
        actions = [
            "discharge", "discharge", "idle",
            "charge", "charge", "discharge",
        ]
        demoted = remove_unprofitable_predischarge(
            actions, slots, efficiency=0.85, cycle_cost_eur=0.04,
            protect_indices={0},
        )
        # Slot 0 geschuetzt, nur Slot 1 demotet.
        assert demoted == 1
        assert actions[0] == "discharge"
        assert actions[1] == "idle"

    def test_keeps_normal_charge_then_discharge(self):
        """Normaler Plan (erst laden, dann entladen) bleibt unangetastet."""
        prices = [0.10, 0.10, 0.35, 0.35]
        slots = _make_slots(prices)
        actions = ["charge", "charge", "discharge", "discharge"]
        demoted = remove_unprofitable_predischarge(
            actions, slots, efficiency=0.85, cycle_cost_eur=0.04,
            protect_indices=set(),
        )
        assert demoted == 0
        assert actions == ["charge", "charge", "discharge", "discharge"]

    def test_no_charge_at_all_keeps_discharges(self):
        """Ohne jeden Charge-Slot gibt es keinen Roundtrip -> nichts demoten."""
        prices = [0.30, 0.20, 0.25, 0.28]
        slots = _make_slots(prices)
        actions = ["discharge", "idle", "discharge", "discharge"]
        demoted = remove_unprofitable_predischarge(
            actions, slots, efficiency=0.85, cycle_cost_eur=0.04,
            protect_indices=set(),
        )
        assert demoted == 0

    def test_pipeline_no_roundtrip_negative_discharge(self):
        """Integration: today-like Kurve darf nach voller Pipeline keinen
        Discharge mehr enthalten, der vor einem Charge mit Verlust-Roundtrip
        liegt."""
        # Morgen 16-17 ct, Mittag-Dip 11.7-15.5 ct, Abend-Peak ->35 ct.
        prices = (
            [0.173, 0.171, 0.165, 0.171, 0.165, 0.163]      # 0-5  Morgen
            + [0.159, 0.155, 0.153, 0.149]                  # 6-9  spaeter Vormittag
            + [0.141, 0.129, 0.117, 0.125, 0.123]           # 10-14 Mittag-Dip
            + [0.144, 0.156, 0.171, 0.177, 0.177]           # 15-19 Nachmittag
            + [0.202, 0.269, 0.315, 0.305, 0.327]           # 20-24 Abend-Peak
            + [0.331, 0.338, 0.343, 0.351, 0.343]           # 25-29 Abend-Peak
        )
        n = len(prices)
        slots = []
        for i, p in enumerate(prices):
            slots.append({
                "price": p, "grid_fraction": 1.0,
                "solar_wh_hour": 500 if i < 20 else 0,
                "solar_surplus_kwh": 0.0,
                "discharge_kwh": 0.10,  # zero-feed-gedrosselt, kleiner Cap
                "house_w": 500,
            })
        start = 65.0
        actions, _ = solve_dp(
            slots, n, start, 0.220, 0.200, DEFAULT["cap"], 0.85, 0.04,
            0.25, min_soc=10.0, max_soc=90.0,
        )
        actions, _ = smooth_plan(
            actions, slots, n, 0.85, 0.04, 0.220, 0.200, DEFAULT["cap"],
            start, min_soc=10.0, max_soc=90.0, slot_h=0.25,
        )
        _f, _k, fi = force_pre_solar_discharge(
            actions, slots, start, 0.220, 0.200, DEFAULT["cap"],
            min_soc=10.0, max_soc=90.0,
        )
        remove_dp_discharge_enclaves(actions, slots, protect_indices=fi)
        remove_unprofitable_predischarge(
            actions, slots, efficiency=0.85, cycle_cost_eur=0.04,
            protect_indices=fi,
        )
        charge_idx = [i for i, a in enumerate(actions) if a == "charge"]
        bad = []
        for i, a in enumerate(actions):
            if a != "discharge" or i in fi:
                continue
            later_charges = [prices[j] for j in charge_idx if j > i]
            if not later_charges:
                continue
            roundtrip = prices[i] * 0.85 - min(later_charges) - 0.04
            if roundtrip <= 0:
                bad.append(i)
        assert not bad, (
            f"Verlust-Roundtrip-Discharges nach Pipeline: {bad}\n"
            f"actions={actions}"
        )


# ── compute_presolar_discharge_hours tests (v2.52.0) ────────────────


class TestComputePresolarDischargeHours:
    """Tests fuer die Reason-Markierung 'Platz fuer Solar schaffen'.

    Bug (live v2.50.0): JEDER Discharge vor dem ersten Solar-Slot wurde
    als 'Platz fuer Solar schaffen' markiert, auch wenn der gesamte
    spaetere Solar-Surplus nur ~0.07 kWh betrug (bewoelkter Tag). Das
    Label suggerierte eine Solar-Begruendung, wo keine war. Fix: nur
    markieren, wenn der kumulierte spaetere Surplus wirklich relevant ist
    — force_pre_solar-Slots bleiben immer markiert.
    """

    def test_labels_when_substantial_later_solar(self):
        """Discharges vor reichlich Solar werden markiert."""
        slots = _make_slots_detailed([
            {"price": 0.20}, {"price": 0.20},          # discharge, vor Solar
            {"price": 0.15, "solar_surplus_kwh": 0.8},  # dicker Surplus
            {"price": 0.15, "solar_surplus_kwh": 0.8},
        ])
        actions = ["discharge", "discharge", "idle", "idle"]
        result = compute_presolar_discharge_hours(actions, slots, forced_indices=set())
        assert result == {0, 1}

    def test_skips_label_when_trivial_later_solar(self):
        """Live-Bug: bei nur ~0.07 kWh spaeterem Surplus KEIN Solar-Label."""
        slots = _make_slots_detailed([
            {"price": 0.17}, {"price": 0.17}, {"price": 0.16},  # Morgen-Discharges
            {"price": 0.16, "solar_surplus_kwh": 0.07},          # trivialer Surplus
            {"price": 0.15, "solar_surplus_kwh": 0.0},
        ])
        actions = ["discharge", "discharge", "discharge", "idle", "idle"]
        result = compute_presolar_discharge_hours(actions, slots, forced_indices=set())
        assert result == set(), (
            f"Trivialer Solar-Surplus darf kein 'Platz fuer Solar'-Label "
            f"ausloesen, got {result}"
        )

    def test_forced_indices_always_labeled(self):
        """force_pre_solar-Slots werden immer markiert, auch ohne viel Solar."""
        slots = _make_slots_detailed([
            {"price": 0.17}, {"price": 0.17}, {"price": 0.16},
            {"price": 0.16, "solar_surplus_kwh": 0.07},
            {"price": 0.15},
        ])
        actions = ["discharge", "discharge", "discharge", "idle", "idle"]
        result = compute_presolar_discharge_hours(actions, slots, forced_indices={2})
        # Trotz trivialem Solar: der erzwungene Slot 2 bleibt markiert.
        assert result == {2}

    def test_only_discharge_slots_labeled(self):
        """Idle/charge-Slots vor Solar werden nie markiert."""
        slots = _make_slots_detailed([
            {"price": 0.05}, {"price": 0.20}, {"price": 0.20},
            {"price": 0.15, "solar_surplus_kwh": 0.8},
        ])
        actions = ["charge", "discharge", "idle", "idle"]
        result = compute_presolar_discharge_hours(actions, slots, forced_indices=set())
        assert result == {1}

    def test_no_solar_at_all_only_forced(self):
        """Ohne jeden Solar-Slot werden nur forced_indices markiert."""
        slots = _make_slots_detailed([{"price": 0.20}] * 4)
        actions = ["discharge", "discharge", "idle", "idle"]
        result = compute_presolar_discharge_hours(actions, slots, forced_indices={1})
        assert result == {1}
