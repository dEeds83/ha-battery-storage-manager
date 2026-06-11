"""Tests for history.py pure helpers (no running Home Assistant needed).

The homeassistant modules are mocked in conftest.py, so history.py can be
loaded directly via importlib without pulling in the real dependencies.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HIST_PATH = Path(__file__).resolve().parent.parent / (
    "custom_components/battery_storage_manager/history.py"
)
_spec = importlib.util.spec_from_file_location("history", _HIST_PATH)
history = importlib.util.module_from_spec(_spec)
sys.modules["history"] = history
_spec.loader.exec_module(history)

summarize_7day_curtailment = history.summarize_7day_curtailment


def _stat(start, mean):
    return {"start": start, "mean": mean}


class TestSummarize7dayCurtailment:
    """Tests fuer die 7-Tage-Curtailment-Auswertung."""

    def test_empty_stats_unavailable(self):
        """Keine Long-Term-Statistics -> avg 0, available False (Live-Bug:
        Entities ohne state_class liefern leere Listen, der 7d-Hebel war
        damit still tot)."""
        avg, available, common = summarize_7day_curtailment(
            [], [], soc_threshold=99.0, solar_w_min=200.0,
        )
        assert avg == 0.0
        assert available is False
        assert common == 0

    def test_only_one_entity_has_stats_unavailable(self):
        """Wenn nur eine der beiden Entities Statistics hat -> unavailable."""
        soc = [_stat(1000, 99.5), _stat(4600, 99.5)]
        avg, available, common = summarize_7day_curtailment(
            soc, [], soc_threshold=99.0, solar_w_min=200.0,
        )
        assert available is False
        assert avg == 0.0

    def test_counts_curtailment_hours(self):
        """Slots mit SOC>=99% UND Solar>=200W zaehlen als Curtailment."""
        # 7 gemeinsame Slots, davon 7 ueber Schwelle -> 7h ueber 7 Tage = 1.0/Tag
        soc = [_stat(t, 99.5) for t in range(7)]
        solar = [_stat(t, 500.0) for t in range(7)]
        avg, available, common = summarize_7day_curtailment(
            soc, solar, soc_threshold=99.0, solar_w_min=200.0,
        )
        assert available is True
        assert common == 7
        assert avg == 1.0

    def test_below_threshold_not_counted(self):
        """SOC oder Solar unter Schwelle -> kein Curtailment, aber available."""
        soc = [_stat(t, 80.0) for t in range(7)]      # SOC zu niedrig
        solar = [_stat(t, 500.0) for t in range(7)]
        avg, available, common = summarize_7day_curtailment(
            soc, solar, soc_threshold=99.0, solar_w_min=200.0,
        )
        assert available is True
        assert avg == 0.0

    def test_inclusive_thresholds(self):
        """Schwellen sind inklusiv (>=), passend zu den 24h-Trackern."""
        soc = [_stat(0, 99.0)]       # genau auf SOC-Schwelle
        solar = [_stat(0, 200.0)]    # genau auf Solar-Schwelle
        avg, available, common = summarize_7day_curtailment(
            soc, solar, soc_threshold=99.0, solar_w_min=200.0,
        )
        assert common == 1
        assert avg == round(1 / 7.0, 2)

    def test_merges_datetime_and_ms_keys(self):
        """start kann ms-int oder datetime-aehnliches Objekt sein."""
        class _DT:
            def __init__(self, ts):
                self._ts = ts

            def timestamp(self):
                return self._ts

        soc = [_stat(_DT(123.0), 99.5)]
        solar = [_stat(123000 if False else _DT(123.0), 500.0)]
        avg, available, common = summarize_7day_curtailment(
            soc, solar, soc_threshold=99.0, solar_w_min=200.0,
        )
        assert common == 1
        assert avg == round(1 / 7.0, 2)

    def test_none_means_skipped(self):
        """Stats mit mean=None werden uebersprungen, blockieren aber nicht."""
        soc = [_stat(0, None), _stat(1, 99.5)]
        solar = [_stat(0, 500.0), _stat(1, 500.0)]
        avg, available, common = summarize_7day_curtailment(
            soc, solar, soc_threshold=99.0, solar_w_min=200.0,
        )
        # Slot 0 hat soc=None -> nur Slot 1 gemeinsam.
        assert common == 1
        assert available is True
