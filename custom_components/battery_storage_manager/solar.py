"""Solar forecast mixin for Battery Storage Manager."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    SOLAR_CALIBRATION_ROLLING_DAYS,
)

_LOGGER = logging.getLogger(__name__)


class SolarMixin:
    """Mixin providing solar forecast methods for the coordinator."""

    async def _load_solar_calibration(self) -> None:
        """Load solar calibration data from persistent storage."""
        if self._solar_calibration_loaded:
            return
        data = await self._solar_calibration_store.async_load()
        if data and isinstance(data, dict):
            self._solar_calibration_history = data.get("history", [])
            self._solar_calibration_last_date = data.get("last_date")
            if self._solar_calibration_history:
                self._solar_calibration_factor = (
                    sum(self._solar_calibration_history)
                    / len(self._solar_calibration_history)
                )
                _LOGGER.debug(
                    "Solar calibration loaded: factor=%.2f (%d days)",
                    self._solar_calibration_factor,
                    len(self._solar_calibration_history),
                )
        self._solar_calibration_loaded = True

    async def _calibrate_solar_forecast(self) -> None:
        """Compare yesterday's forecast with actual production and update factor.

        Called every cycle but only records once per day (at day change).
        Stores the ratio actual/forecast as a rolling 14-day average.
        """
        if not self._solar_energy_today_entity:
            return

        now = dt_util.now()
        today_str = now.strftime("%Y-%m-%d")

        if self._solar_calibration_last_date == today_str:
            return

        if now.hour < 20:
            return

        state = self.hass.states.get(self._solar_energy_today_entity)
        if not state or state.state in ("unknown", "unavailable"):
            return

        try:
            actual_kwh = float(state.state)
        except (ValueError, TypeError):
            return

        if actual_kwh < 0.1:
            return

        # Use RAW forecast (before calibration) by dividing out the
        # current calibration factor.  Otherwise we get a circular
        # comparison: calibrated forecast ≈ actual → factor stays ~1.
        today_prefix = now.strftime("%Y-%m-%dT")
        calibrated_kwh = sum(
            v / 1000 for k, v in self._solar_forecast.items()
            if k.startswith(today_prefix)
        )
        # Undo calibration + intraday correction to get raw forecast
        raw_factor = self._solar_calibration_factor or 1.0
        intraday = self._intraday_solar_factor if hasattr(self, "_intraday_solar_factor") else 1.0
        if intraday is None or intraday <= 0:
            intraday = 1.0
        combined = raw_factor * intraday
        forecast_kwh = calibrated_kwh / combined if combined > 0.1 else calibrated_kwh

        if forecast_kwh < 0.1:
            return

        ratio = actual_kwh / forecast_kwh
        ratio = max(0.3, min(3.0, ratio))

        self._solar_calibration_history.append(round(ratio, 3))

        max_days = SOLAR_CALIBRATION_ROLLING_DAYS
        if len(self._solar_calibration_history) > max_days:
            self._solar_calibration_history = self._solar_calibration_history[-max_days:]

        self._solar_calibration_factor = (
            sum(self._solar_calibration_history)
            / len(self._solar_calibration_history)
        )
        self._solar_calibration_last_date = today_str

        await self._solar_calibration_store.async_save({
            "history": self._solar_calibration_history,
            "last_date": self._solar_calibration_last_date,
        })

        _LOGGER.info(
            "Solar calibration: actual=%.1f kWh, forecast=%.1f kWh, "
            "ratio=%.2f, new factor=%.2f (%d days)",
            actual_kwh, forecast_kwh, ratio,
            self._solar_calibration_factor,
            len(self._solar_calibration_history),
        )

    def _apply_solar_calibration(self) -> None:
        """Apply calibration factor to all solar forecast values."""
        if abs(self._solar_calibration_factor - 1.0) < 0.01:
            return

        for key in self._solar_forecast:
            self._solar_forecast[key] *= self._solar_calibration_factor

        now = dt_util.now()
        now_key = now.strftime("%Y-%m-%dT%H")
        today_prefix = now.strftime("%Y-%m-%dT")
        remaining = {
            k: v for k, v in self._solar_forecast.items()
            if k >= now_key and k.startswith(today_prefix)
        }
        self._expected_solar_kwh = sum(remaining.values()) / 1000

        _LOGGER.debug(
            "Solar forecast calibrated: factor=%.2f, adjusted expected=%.1f kWh",
            self._solar_calibration_factor, self._expected_solar_kwh,
        )

    def _apply_intraday_solar_correction(self) -> None:
        """Adjust remaining solar forecast using a Kalman filter."""
        if not self._solar_energy_today_entity:
            self._intraday_solar_factor = 1.0
            return

        now = dt_util.now()
        if now.hour < 8 or now.hour >= 20:
            self._intraday_solar_factor = 1.0
            self._kalman_x = 1.0
            self._kalman_p = 0.1
            return

        state = self.hass.states.get(self._solar_energy_today_entity)
        if not state or state.state in ("unknown", "unavailable"):
            return

        try:
            actual_so_far_kwh = float(state.state)
        except (ValueError, TypeError):
            return

        today_prefix = now.strftime("%Y-%m-%dT")
        now_key = now.strftime("%Y-%m-%dT%H")
        forecast_so_far_wh = sum(
            v for k, v in self._solar_forecast.items()
            if k.startswith(today_prefix) and k < now_key
        )
        forecast_so_far_kwh = forecast_so_far_wh / 1000

        if forecast_so_far_kwh < 0.5:
            self._intraday_solar_factor = 1.0
            return

        measurement = actual_so_far_kwh / forecast_so_far_kwh
        measurement = max(0.2, min(3.0, measurement))

        x = getattr(self, "_kalman_x", 1.0)
        p = getattr(self, "_kalman_p", 0.1)
        Q = 0.005
        R = 0.05

        p = p + Q
        K = p / (p + R)
        x = x + K * (measurement - x)
        p = (1 - K) * p

        x = max(0.2, min(3.0, x))
        self._kalman_x = x
        self._kalman_p = p

        old_factor = self._intraday_solar_factor
        self._intraday_solar_factor = x

        for key in self._solar_forecast:
            if key.startswith(today_prefix) and key >= now_key:
                self._solar_forecast[key] *= x

        remaining = {
            k: v for k, v in self._solar_forecast.items()
            if k >= now_key and k.startswith(today_prefix)
        }
        self._expected_solar_kwh = sum(remaining.values()) / 1000

        if abs(x - old_factor) > 0.05 and abs(x - 1.0) > 0.1:
            msg = (
                f"Kalman Solar: Ist={actual_so_far_kwh:.1f} kWh, "
                f"Forecast={forecast_so_far_kwh:.1f} kWh, "
                f"Messung={measurement:.2f}, Kalman={x:.2f} "
                f"(K={K:.2f}), Rest={self._expected_solar_kwh:.1f} kWh"
            )
            _LOGGER.info(msg)
            self._log_optimization(msg)

    async def _read_solar_forecast(self) -> None:
        """Read solar production forecast from configured entities."""
        self._solar_forecast = {}
        self._expected_solar_kwh = 0.0

        if not self._use_solar_forecast:
            return

        entity_ids: list[str] = []
        if self._solar_forecast_entity:
            entity_ids.append(self._solar_forecast_entity)
        for eid in self._solar_forecast_entities:
            if eid and eid not in entity_ids:
                entity_ids.append(eid)

        if not entity_ids:
            return

        energy_data_found = await self._read_energy_solar_forecasts(entity_ids)

        for entity_id in entity_ids:
            if entity_id not in energy_data_found:
                self._read_single_solar_forecast(entity_id)

        if self._solar_forecast:
            _LOGGER.debug(
                "Combined solar forecast from %d entities: %d hours, total %.1f kWh",
                len(entity_ids),
                len(self._solar_forecast),
                sum(self._solar_forecast.values()) / 1000,
            )

        now = dt_util.now()
        now_key = now.strftime("%Y-%m-%dT%H")
        today_prefix = now.strftime("%Y-%m-%dT")
        remaining = {
            k: v for k, v in self._solar_forecast.items()
            if k >= now_key and k.startswith(today_prefix)
        }
        self._expected_solar_kwh = sum(remaining.values()) / 1000

    async def _read_energy_solar_forecasts(
        self, entity_ids: list[str]
    ) -> set[str]:
        """Try to read solar forecasts via the HA energy platform."""
        covered: set[str] = set()

        entity_registry = er.async_get(self.hass)
        entries_to_fetch: dict[str, list[str]] = {}

        for entity_id in entity_ids:
            entry = entity_registry.async_get(entity_id)
            if not entry or not entry.config_entry_id:
                continue
            config_entry = self.hass.config_entries.async_get_entry(
                entry.config_entry_id
            )
            if not config_entry or config_entry.domain != "forecast_solar":
                continue
            entries_to_fetch.setdefault(entry.config_entry_id, []).append(
                entity_id
            )

        if not entries_to_fetch:
            return covered

        for config_entry_id, eids in entries_to_fetch.items():
            try:
                config_entry = self.hass.config_entries.async_get_entry(
                    config_entry_id
                )
                if (
                    not config_entry
                    or not hasattr(config_entry, "runtime_data")
                    or config_entry.runtime_data is None
                ):
                    continue

                estimate = config_entry.runtime_data
                wh_period = getattr(estimate, "wh_period", None)
                if not wh_period:
                    data = getattr(estimate, "data", None)
                    if data:
                        wh_period = getattr(data, "wh_period", None)

                if not isinstance(wh_period, dict) or not wh_period:
                    _LOGGER.debug(
                        "forecast_solar entry %s: no wh_period data found",
                        config_entry_id,
                    )
                    continue

                for dt_obj, wh in wh_period.items():
                    try:
                        if isinstance(dt_obj, datetime):
                            local_dt = dt_util.as_local(dt_obj) if dt_obj.tzinfo is not None else dt_obj
                            hour_key = local_dt.strftime("%Y-%m-%dT%H")
                        else:
                            hour_key = self._to_hour_key(str(dt_obj))
                        self._solar_forecast[hour_key] = (
                            self._solar_forecast.get(hour_key, 0) + float(wh)
                        )
                    except (ValueError, TypeError):
                        continue

                covered.update(eids)
                _LOGGER.debug(
                    "Solar forecast from forecast_solar entry %s "
                    "(energy platform): %d entries",
                    config_entry_id,
                    len(wh_period),
                )
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Could not read forecast_solar energy data for %s",
                    config_entry_id,
                    exc_info=True,
                )

        return covered

    def _read_single_solar_forecast(self, entity_id: str) -> None:
        """Read solar forecast from a single entity and add to combined forecast."""
        state = self.hass.states.get(entity_id)
        if not state:
            _LOGGER.debug("Solar forecast entity '%s' not found", entity_id)
            return

        attrs = state.attributes
        parsed = False

        # Format 1: Forecast.Solar watt_hours_period
        wh_period = attrs.get("watt_hours_period")
        if isinstance(wh_period, dict) and wh_period:
            for dt_str, wh in wh_period.items():
                try:
                    hour_key = self._to_hour_key(dt_str)
                    self._solar_forecast[hour_key] = (
                        self._solar_forecast.get(hour_key, 0) + float(wh)
                    )
                except (ValueError, TypeError):
                    continue
            parsed = True
            _LOGGER.debug(
                "Solar forecast '%s' (watt_hours_period): %d entries",
                entity_id,
                len(wh_period),
            )

        # Format 2: Solcast / generic forecast list
        if not parsed:
            forecast_list = attrs.get("forecast") or attrs.get("detailedForecast")
            if isinstance(forecast_list, list) and forecast_list:
                for entry in forecast_list:
                    if not isinstance(entry, dict):
                        continue
                    dt_str = entry.get("period_start") or entry.get("datetime") or entry.get("start")
                    pv = entry.get("pv_estimate") or entry.get("pv_estimate10") or entry.get("power_production")
                    if dt_str and pv is not None:
                        try:
                            hour_key = self._to_hour_key(str(dt_str))
                            self._solar_forecast[hour_key] = (
                                self._solar_forecast.get(hour_key, 0) + float(pv) * 1000
                            )
                        except (ValueError, TypeError):
                            continue
                parsed = True
                _LOGGER.debug(
                    "Solar forecast '%s' (forecast list): %d entries",
                    entity_id,
                    len(forecast_list),
                )

        # Format 3: Forecast.Solar watt_hours (cumulative)
        if not parsed:
            wh_cum = attrs.get("watt_hours")
            if isinstance(wh_cum, dict) and len(wh_cum) > 1:
                sorted_entries = sorted(wh_cum.items())
                for i in range(1, len(sorted_entries)):
                    dt_str, cum_wh = sorted_entries[i]
                    prev_wh = sorted_entries[i - 1][1]
                    try:
                        hour_key = self._to_hour_key(dt_str)
                        delta = float(cum_wh) - float(prev_wh)
                        if delta > 0:
                            self._solar_forecast[hour_key] = (
                                self._solar_forecast.get(hour_key, 0) + delta
                            )
                    except (ValueError, TypeError):
                        continue
                _LOGGER.debug(
                    "Solar forecast '%s' (watt_hours cumulative): %d entries",
                    entity_id,
                    len(sorted_entries),
                )
