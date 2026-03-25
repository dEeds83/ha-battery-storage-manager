"""Consumption statistics mixin for Battery Storage Manager."""

from __future__ import annotations

import logging

from homeassistant.util import dt as dt_util

from .const import (
    CONSUMPTION_STATS_ROLLING_DAYS,
)

_LOGGER = logging.getLogger(__name__)


class ConsumptionMixin:
    """Mixin providing consumption statistics methods for the coordinator."""

    async def _load_consumption_stats(self) -> None:
        """Load consumption statistics from persistent storage.

        Migrates v1 format (keys "0"-"23") to v2 format (keys "wd_0", "we_0" etc.)
        by copying old data to both weekday and weekend slots.
        """
        if self._consumption_loaded:
            return
        data = await self._consumption_store.async_load()
        if data and isinstance(data, dict):
            needs_migration = any(
                k.isdigit() and not any(k2.startswith("wd_") for k2 in data)
                for k in data
            )
            if needs_migration:
                migrated: dict[str, list[float]] = {}
                for k, v in data.items():
                    if k.isdigit():
                        migrated[f"wd_{k}"] = v
                        migrated[f"we_{k}"] = list(v)
                self._consumption_stats = migrated
                await self._consumption_store.async_save(migrated)
                _LOGGER.info("Migrated consumption stats from v1 to v2 (weekday/weekend)")
            else:
                self._consumption_stats = data
            total = sum(len(v) for v in self._consumption_stats.values())
            _LOGGER.debug("Loaded consumption stats: %d entries across %d slots",
                          total, len(self._consumption_stats))
        else:
            self._consumption_stats = {}
        self._consumption_loaded = True

    async def _record_consumption(self) -> None:
        """Record current grid consumption for the current hour.

        Collects samples every update cycle (30s) and when the hour changes,
        stores the average as one data point for that hour-of-day.
        Only records net consumption (positive grid_power = import from grid).
        """
        if self._grid_power is None:
            return

        now = dt_util.now()
        current_hour = now.hour

        charger_draw = sum(c["power"] for c in self._chargers if c["active"])
        inverter_feed = self._inverter_target_power if self._inverter_active else 0

        solar_w = 0.0
        if self._solar_power is not None:
            solar_w = self._solar_power
        else:
            now_hour_key = now.strftime("%Y-%m-%dT%H")
            solar_wh = self._solar_forecast.get(now_hour_key, 0)
            if solar_wh > 0:
                solar_w = solar_wh

        house_w = self._grid_power - charger_draw + solar_w + inverter_feed
        house_w = max(0, house_w)

        self._consumption_hourly_samples.append(house_w)

        day_type = "wd" if now.weekday() < 5 else "we"

        if self._consumption_last_hour is not None and current_hour != self._consumption_last_hour:
            if self._consumption_hourly_samples:
                avg_w = sum(self._consumption_hourly_samples) / len(self._consumption_hourly_samples)
                store_daytype = self._consumption_last_daytype or day_type
                hour_key = f"{store_daytype}_{self._consumption_last_hour}"

                if hour_key not in self._consumption_stats:
                    self._consumption_stats[hour_key] = []

                self._consumption_stats[hour_key].append(round(avg_w, 1))

                max_entries = CONSUMPTION_STATS_ROLLING_DAYS
                if len(self._consumption_stats[hour_key]) > max_entries:
                    self._consumption_stats[hour_key] = \
                        self._consumption_stats[hour_key][-max_entries:]

                _LOGGER.debug(
                    "Consumption stats: %s avg %.0fW "
                    "(%d samples, %d days stored)",
                    hour_key, avg_w,
                    len(self._consumption_hourly_samples),
                    len(self._consumption_stats[hour_key]),
                )

                await self._consumption_store.async_save(self._consumption_stats)

            self._consumption_hourly_samples = []

        self._consumption_last_hour = current_hour
        self._consumption_last_daytype = day_type

    def get_hourly_consumption_forecast(self, target_date=None) -> dict[int, float]:
        """Get predicted consumption per hour-of-day (0-23) in watts.

        Uses exponentially weighted average (EWA) where recent days have
        more influence than older days.
        """
        if target_date is None:
            target_date = dt_util.now()
        day_type = "wd" if target_date.weekday() < 5 else "we"
        other_type = "we" if day_type == "wd" else "wd"
        alpha = 0.85

        temp_factor = 1.0
        if self._outside_temp is not None:
            if self._outside_temp < 15.0:
                temp_factor = 1.0 + (15.0 - self._outside_temp) * 0.02
            elif self._outside_temp > 25.0:
                temp_factor = 1.0 + (self._outside_temp - 25.0) * 0.02
            temp_factor = max(0.8, min(1.5, temp_factor))

        forecast: dict[int, float] = {}
        for hour in range(24):
            samples = self._consumption_stats.get(f"{day_type}_{hour}", [])
            if not samples:
                samples = self._consumption_stats.get(f"{other_type}_{hour}", [])
            if samples:
                n = len(samples)
                if n == 1:
                    forecast[hour] = samples[0]
                else:
                    weights = [alpha ** (n - 1 - i) for i in range(n)]
                    w_sum = sum(weights)
                    forecast[hour] = sum(w * s for w, s in zip(weights, samples)) / w_sum
            else:
                forecast[hour] = self._house_consumption_w

            forecast[hour] *= temp_factor

        return forecast
