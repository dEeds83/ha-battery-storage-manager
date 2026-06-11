"""Action history and optimization log mixin for Battery Storage Manager."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# v2.49.0: Curtailment-Detection-Schwellen.
# SOC-Schwelle: ab hier gilt der Akku als "voll" und kann kein Solar mehr
# aufnehmen. Bewusst < 100, weil reale BMS oft schon bei 99% drosseln.
_CURTAIL_SOC_THRESHOLD = 99.0
# Solar-Power-Schwelle in W: darunter ist die PV im Wesentlichen aus
# (Wolken, Daemmerung) und ein hoher SOC ist nicht aussagekraeftig.
_CURTAIL_SOLAR_W_MIN = 200.0
# Snapshot-Intervall in der History (10 Minuten == 1/6 Stunde).
_HISTORY_SLOT_HOURS = 10.0 / 60.0


def _stat_key(s):
    """Map a statistics row's ``start`` to a hashable slot key.

    HA delivers ``start`` either as an ms/seconds timestamp (int/float) or as
    a datetime-like object; absorb both.
    """
    v = s.get("start")
    if isinstance(v, (int, float)):
        return int(v)
    if hasattr(v, "timestamp"):
        return int(v.timestamp())
    return v


def summarize_7day_curtailment(
    soc_stats: list[dict],
    solar_stats: list[dict],
    soc_threshold: float,
    solar_w_min: float,
) -> tuple[float, bool, int]:
    """Reduce hourly SOC/solar statistics to a 7-day curtailment average.

    A slot counts as curtailment when both the mean SOC and the mean solar
    power are at or above their thresholds (inclusive, matching the 24h
    tracker). The two series are joined on their slot start key.

    Returns ``(avg_hours_per_day, available, common_slots)``. ``available`` is
    ``False`` when the series do not overlap at all — which happens when the
    chosen SOC/solar entities carry no long-term statistics (no numeric
    ``state_class``). In that case callers must NOT treat the 0.0 as "no
    curtailment"; the lever is simply blind and should say so.
    """
    soc_by: dict = {}
    for s in soc_stats:
        mean = s.get("mean")
        if mean is None:
            continue
        soc_by[_stat_key(s)] = float(mean)
    solar_by: dict = {}
    for s in solar_stats:
        mean = s.get("mean")
        if mean is None:
            continue
        solar_by[_stat_key(s)] = float(mean)

    common = soc_by.keys() & solar_by.keys()
    if not common:
        return 0.0, False, 0

    curtail_hours = sum(
        1 for k in common
        if soc_by[k] >= soc_threshold and solar_by[k] >= solar_w_min
    )
    return round(curtail_hours / 7.0, 2), True, len(common)


class HistoryMixin:
    """Mixin providing action history and optimization log methods."""

    async def _record_action_history(self) -> None:
        """Record current state to action history (1 per 10 min, 48h, persistent)."""
        if not self._action_history_loaded:
            stored = await self._action_history_store.async_load()
            if stored and isinstance(stored, list):
                self._action_history = stored
            self._action_history_loaded = True

        now = dt_util.now()
        rounded_min = (now.minute // 10) * 10
        interval_key = now.strftime(f"%Y-%m-%dT%H:{rounded_min:02d}")

        if interval_key == self._action_history_last_key:
            return

        self._action_history_last_key = interval_key

        planned = self._get_current_plan_action() or "none"
        entry = {
            "time": interval_key,
            "mode": self._operating_mode,
            "planned": planned,
            "soc": round(self._battery_soc, 1) if self._battery_soc else None,
            "price": round(self._current_price * 100, 1) if self._current_price else None,
            "grid_w": round(self._grid_power) if self._grid_power is not None else None,
            "solar_w": round(self._solar_power) if self._solar_power is not None else None,
            "version": self._version,
        }
        self._action_history.append(entry)

        max_entries = 288
        if len(self._action_history) > max_entries:
            self._action_history = self._action_history[-max_entries:]

        await self._action_history_store.async_save(self._action_history)

        # v2.49.0: Curtailment-Stats nach jedem neuen Snapshot
        # aktualisieren — das ist der einzige Moment, in dem sie sich
        # aendern koennen.
        self._update_curtailment_stats()

    def _update_curtailment_stats(self) -> None:
        """Rechne Solar-Curtailment der letzten 24h aus der History aus.

        Snapshots mit SOC >= 99% UND Solar > 200 W zaehlen als Stunden
        mit verschenkter Energie: bei vollem Akku geht alles ueber den
        Hausverbrauch hinaus als Export raus (oder wird gedrosselt).

        Setzt:
            ``_curtailment_hours_24h``  -- Summe der Stunden
            ``_curtailment_lost_kwh_24h`` -- best-effort kWh-Schaetzung
                (Solar minus Hausverbrauch in dem Slot)
        """
        if not self._action_history:
            self._curtailment_hours_24h = 0.0
            self._curtailment_lost_kwh_24h = 0.0
            return

        now = dt_util.now()
        cutoff = now - timedelta(hours=24)
        # Erwarteter Hausverbrauch (best-effort): Konfig-Default. Reale
        # Per-Slot-Werte koennten praeziser sein, kosten aber bei jeder
        # 10-min-Rotation einen Forecast-Lookup. Konstante reicht fuer
        # einen Trend-Indikator.
        house_w = float(getattr(self, "_house_consumption_w", 500) or 500)

        hours = 0.0
        lost_kwh = 0.0
        for entry in self._action_history:
            time_str = entry.get("time")
            if not time_str:
                continue
            try:
                ts = datetime.strptime(time_str, "%Y-%m-%dT%H:%M")
            except (TypeError, ValueError):
                continue
            ts = ts.replace(tzinfo=now.tzinfo)
            if ts < cutoff:
                continue
            soc = entry.get("soc")
            solar_w = entry.get("solar_w")
            if soc is None or solar_w is None:
                continue
            if soc < _CURTAIL_SOC_THRESHOLD or solar_w < _CURTAIL_SOLAR_W_MIN:
                continue
            hours += _HISTORY_SLOT_HOURS
            # Lost = Anteil ueber Hausverbrauch (das geht bei vollem
            # Akku in den Export oder wird gedrosselt).
            lost_w = max(0.0, float(solar_w) - house_w)
            lost_kwh += lost_w / 1000.0 * _HISTORY_SLOT_HOURS

        self._curtailment_hours_24h = round(hours, 2)
        self._curtailment_lost_kwh_24h = round(lost_kwh, 2)

    async def _async_fetch_7day_curtailment(self, force: bool = False) -> None:
        """Hole 7-Tage-Curtailment aus dem HA-Statistics-Modul.

        Approximation: pro Stunde wird der MEAN von SOC und Solar-Power
        verglichen. Eine Stunde gilt als Curtailment, wenn beide
        Mittelwerte ueber den Schwellen liegen. Das ist gegenueber dem
        24h-Tracker (10-min-Snapshots) groeber, dafuer aber persistent
        und ueber Tage stabil — die Adaption des Floors gewinnt damit
        Wochen-Kontext.

        Refresh nur einmal pro Stunde, weil Statistics teuer und
        stuendlich aggregiert ist.
        """
        if not self._battery_soc_entity or not self._solar_power_entity:
            return
        now = dt_util.now()
        last = self._curtailment_7day_last_fetch
        if not force and last is not None and (now - last) < timedelta(hours=1):
            return

        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )
        except ImportError:
            _LOGGER.debug("Recorder-Modul nicht verfuegbar; 7day-Curtailment uebersprungen")
            return

        end = now
        start = end - timedelta(days=7)
        statistic_ids = {self._battery_soc_entity, self._solar_power_entity}

        try:
            recorder = get_instance(self.hass)
            stats = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                start,
                end,
                statistic_ids,
                "hour",
                None,
                {"mean"},
            )
        except Exception as exc:
            _LOGGER.warning("7day-Curtailment-Fetch fehlgeschlagen: %s", exc)
            self._curtailment_7day_last_fetch = now
            return

        soc_stats = stats.get(self._battery_soc_entity, []) if stats else []
        solar_stats = stats.get(self._solar_power_entity, []) if stats else []

        avg, available, common = summarize_7day_curtailment(
            soc_stats, solar_stats,
            _CURTAIL_SOC_THRESHOLD, _CURTAIL_SOLAR_W_MIN,
        )
        self._curtailment_7day_avg_hours_per_day = avg
        self._curtailment_7day_available = available
        self._curtailment_7day_last_fetch = now

        if not available:
            # Kein gemeinsamer Statistics-Slot -> die gewaehlten SOC/Solar-
            # Entities fuehren keine Long-Term-Statistics (kein numerischer
            # state_class). Der 7d-Hebel ist damit blind. Einmalig warnen,
            # statt still 0 als "kein Curtailment" zu interpretieren.
            if not self._curtailment_7day_warned:
                self._curtailment_7day_warned = True
                _LOGGER.warning(
                    "7day-Curtailment nicht verfuegbar: Entities %s / %s liefern "
                    "keine Long-Term-Statistics (state_class fehlt?). Der 7-Tage-"
                    "Floor-Hebel bleibt inaktiv; der 24h-Tracker laeuft weiter.",
                    self._battery_soc_entity, self._solar_power_entity,
                )
            return

        # Verfuegbar -> evtl. Warn-Flag zuruecksetzen (Statistics wieder da).
        self._curtailment_7day_warned = False
        _LOGGER.debug(
            "7day-Curtailment: %.2f h/Tag (von %d gemeinsamen Slots)",
            avg, common,
        )

    def _log_optimization(self, message: str) -> None:
        """Add an entry to the optimization log (visible in UI)."""
        now = dt_util.now()
        entry = f"{now.strftime('%H:%M:%S')} {message}"
        self._optimization_log.append(entry)
        if len(self._optimization_log) > self._max_log_entries:
            self._optimization_log = self._optimization_log[-self._max_log_entries:]
