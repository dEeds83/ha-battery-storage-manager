"""Tests for helpers.py pure functions (no Home Assistant needed)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / (
    "custom_components/battery_storage_manager/helpers.py"
)
_spec = importlib.util.spec_from_file_location("bsm_helpers", _PATH)
helpers = importlib.util.module_from_spec(_spec)
sys.modules["bsm_helpers"] = helpers
_spec.loader.exec_module(helpers)

should_self_correct_target = helpers.should_self_correct_target


class TestShouldSelfCorrectTarget:
    """Tests fuer den WR-Target-Self-Correct-Entscheider (v2.50.2 + v2.52.0)."""

    def test_no_actual_no_correct(self):
        """Ohne actual-Messung kein Self-Correct."""
        assert should_self_correct_target(800.0, None, 5.0) is False

    def test_fresh_large_delta_corrects(self):
        """Frische Messung, target >> actual (Delta>150) -> clamp."""
        assert should_self_correct_target(800.0, 200.0, 5.0) is True

    def test_fresh_small_delta_no_correct(self):
        """Delta <= 150 -> kein Eingriff (WR folgt im Rahmen)."""
        assert should_self_correct_target(300.0, 200.0, 5.0) is False

    def test_stale_reading_not_trusted(self):
        """v2.52.0: Eingefrorener/alter actual-Wert darf KEINEN Self-Correct
        ausloesen, auch wenn das Delta gross aussieht."""
        assert should_self_correct_target(
            800.0, 50.0, actual_age_s=600.0, max_age_s=120.0
        ) is False

    def test_unknown_age_trusts_reading(self):
        """Ohne bekanntes Alter (None) wird die Messung wie frisch behandelt
        (Verhalten vor dem Watchdog bleibt erhalten)."""
        assert should_self_correct_target(800.0, 50.0, actual_age_s=None) is True

    def test_age_exactly_at_limit_is_fresh(self):
        """Alter genau auf der Grenze gilt noch als frisch (inklusiv)."""
        assert should_self_correct_target(
            800.0, 50.0, actual_age_s=120.0, max_age_s=120.0
        ) is True


combine_grid_power = helpers.combine_grid_power


class TestCombineGridPower:
    """Netzleistung aus Saldo-Sensor bzw. Bezug/Einspeisung (v2.53.1)."""

    def test_net_sensor_wins(self):
        """Saldo-Sensor mit Wert hat Vorrang vor Bezug/Einspeisung."""
        assert combine_grid_power(-120.0, 400.0, 0.0) == -120.0

    def test_net_none_falls_back_to_pulse(self):
        """Saldo-Sensor ohne Wert -> Fallback Bezug - Einspeisung (Regression v2.53.0)."""
        assert combine_grid_power(None, 383.0, 0.0) == 383.0
        assert combine_grid_power(None, 100.0, 250.0) == -150.0

    def test_single_pulse_side(self):
        """Nur eine Pulse-Seite verfuegbar."""
        assert combine_grid_power(None, 300.0, None) == 300.0
        assert combine_grid_power(None, None, 200.0) == -200.0

    def test_nothing_available(self):
        """Keine Quelle -> None (Safety-Rampe greift im Coordinator)."""
        assert combine_grid_power(None, None, None) is None

    def test_zero_net_is_valid(self):
        """0 W vom Saldo-Sensor ist ein gueltiger Wert, kein Fallback."""
        assert combine_grid_power(0.0, 500.0, 0.0) == 0.0
