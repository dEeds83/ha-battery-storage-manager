"""Coordinator for Battery Storage Manager."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

import aiohttp

from .const import (
    ATTR_BATTERY_PLAN,
    ATTR_CURRENT_PRICE,
    ATTR_ESTIMATED_SAVINGS,
    ATTR_EXPECTED_SOLAR_KWH,
    ATTR_NEXT_CHEAP_WINDOW,
    ATTR_NEXT_EXPENSIVE_WINDOW,
    ATTR_OPERATING_MODE,
    ATTR_PLAN_SUMMARY,
    ATTR_STRATEGY,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_CURRENT_ENTITY,
    CONF_BATTERY_CYCLE_COST,
    CONF_BATTERY_EFFICIENCY,
    CONF_BATTERY_SOC_ENTITY,
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_CHARGERS,
    CONF_EPEX_PREDICTOR_ENABLED,
    CONF_EPEX_PREDICTOR_REGION,
    CONSUMPTION_STATS_ROLLING_DAYS,
    DEFAULT_EPEX_PREDICTOR_REGION,
    EPEX_PREDICTOR_BASE_URL,
    STORAGE_KEY_CONSUMPTION,
    STORAGE_VERSION_CONSUMPTION,
    CONF_HOUSE_CONSUMPTION_W,
    CONF_OUTSIDE_TEMPERATURE_ENTITY,
    DEFAULT_BATTERY_CYCLE_COST,
    DEFAULT_BATTERY_EFFICIENCY,
    CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY,
    CONF_INVERTER_FEED_POWER,
    CONF_INVERTER_FEED_POWER_ENTITY,
    CONF_INVERTER_FEED_SWITCH,
    CONF_MAX_SOC,
    CONF_MIN_SOC,
    CONF_PRICE_HIGH_THRESHOLD,
    CONF_PRICE_LOW_THRESHOLD,
    CONF_SOLAR_ENERGY_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_ENTITIES,
    CONF_SOLAR_FORECAST_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
    SOLAR_CALIBRATION_ROLLING_DAYS,
    STORAGE_KEY_SOLAR_CALIBRATION,
    STORAGE_VERSION_SOLAR_CALIBRATION,
    CONF_TIBBER_PRICE_ENTITY,
    CONF_TIBBER_PRICES_ENTITY,
    CONF_TIBBER_PULSE_CONSUMPTION_ENTITY,
    CONF_TIBBER_PULSE_PRODUCTION_ENTITY,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_HOUSE_CONSUMPTION_W,
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_SOC,
    DEFAULT_PRICE_HIGH_THRESHOLD,
    DEFAULT_PRICE_LOW_THRESHOLD,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MODE_CHARGING,
    MODE_DISCHARGING,
    MODE_IDLE,
    MODE_SOLAR_CHARGING,
    STRATEGY_MANUAL,
    STRATEGY_PRICE_OPTIMIZED,
    STRATEGY_SELF_CONSUMPTION,
)

_LOGGER = logging.getLogger(__name__)


class BatteryStorageCoordinator(DataUpdateCoordinator):
    """Coordinator that manages battery charging/discharging based on energy prices."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        self._config = {**entry.data, **entry.options}

        # Entity IDs from config
        self._tibber_price_entity = self._config.get(CONF_TIBBER_PRICE_ENTITY, "")
        self._tibber_prices_entity = self._config.get(CONF_TIBBER_PRICES_ENTITY, "")
        self._pulse_consumption_entity = self._config.get(CONF_TIBBER_PULSE_CONSUMPTION_ENTITY, "")
        self._pulse_production_entity = self._config.get(CONF_TIBBER_PULSE_PRODUCTION_ENTITY, "")

        # Chargers: list of {"switch": entity_id, "power": int, "active": bool}
        self._chargers: list[dict] = []
        for c in self._config.get(CONF_CHARGERS, []):
            self._chargers.append({
                "switch": c.get("switch", ""),
                "power": c.get("power", 0),
                "active": False,
            })

        self._inverter_switch = self._config.get(CONF_INVERTER_FEED_SWITCH, "")
        self._inverter_power = self._config.get(CONF_INVERTER_FEED_POWER, 0)
        self._inverter_power_entity = self._config.get(CONF_INVERTER_FEED_POWER_ENTITY, "")
        self._inverter_actual_power_entity = self._config.get(CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY, "")
        self._battery_soc_entity = self._config.get(CONF_BATTERY_SOC_ENTITY, "")
        self._battery_capacity = self._config.get(
            CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY
        )
        self._min_soc = self._config.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
        self._max_soc = self._config.get(CONF_MAX_SOC, DEFAULT_MAX_SOC)
        self._price_low = self._config.get(
            CONF_PRICE_LOW_THRESHOLD, DEFAULT_PRICE_LOW_THRESHOLD
        )
        self._price_high = self._config.get(
            CONF_PRICE_HIGH_THRESHOLD, DEFAULT_PRICE_HIGH_THRESHOLD
        )
        self._solar_forecast_entity = self._config.get(CONF_SOLAR_FORECAST_ENTITY, "")
        self._solar_forecast_entities: list[str] = self._config.get(
            CONF_SOLAR_FORECAST_ENTITIES, []
        )
        self._solar_power_entity = self._config.get(CONF_SOLAR_POWER_ENTITY, "")
        self._solar_power: float | None = None  # current solar production in W
        self._solar_energy_today_entity = self._config.get(CONF_SOLAR_ENERGY_TODAY_ENTITY, "")

        # Solar forecast calibration
        self._solar_calibration_store = Store(
            hass,
            STORAGE_VERSION_SOLAR_CALIBRATION,
            f"{DOMAIN}.{entry.entry_id}.{STORAGE_KEY_SOLAR_CALIBRATION}",
        )
        self._solar_calibration_factor: float = 1.0  # multiplier for forecast
        self._solar_calibration_history: list[float] = []  # daily ratios
        self._solar_calibration_loaded = False
        self._solar_calibration_last_date: str | None = None
        self._solar_forecast_today_kwh: float = 0.0  # today's total forecast
        self._intraday_solar_factor: float = 1.0  # intraday correction factor

        self._house_consumption_w = self._config.get(
            CONF_HOUSE_CONSUMPTION_W, DEFAULT_HOUSE_CONSUMPTION_W
        )

        # Optional sensors: outside temperature + Smartshunt
        self._outside_temp_entity = self._config.get(CONF_OUTSIDE_TEMPERATURE_ENTITY, "")
        self._outside_temp: float | None = None
        self._battery_voltage_entity = self._config.get(CONF_BATTERY_VOLTAGE_ENTITY, "")
        self._battery_current_entity = self._config.get(CONF_BATTERY_CURRENT_ENTITY, "")
        self._battery_voltage: float | None = None
        self._battery_current: float | None = None
        self._battery_real_power: float | None = None  # V × A
        # Efficiency tracking: accumulated charge/discharge energy
        self._efficiency_charge_kwh: float = 0.0
        self._efficiency_discharge_kwh: float = 0.0
        self._efficiency_last_reset: str | None = None  # date string

        # EPEX Predictor for long-term price forecast
        self._epex_enabled = bool(self._config.get(CONF_EPEX_PREDICTOR_ENABLED, False))
        self._epex_region = self._config.get(
            CONF_EPEX_PREDICTOR_REGION, DEFAULT_EPEX_PREDICTOR_REGION
        )
        self._epex_cache: list[dict] = []  # cached EPEX predictions
        self._epex_cache_time: datetime | None = None
        self._epex_cache_ttl = timedelta(hours=2)  # EPEX predictions change slowly
        self._epex_markup: dict | None = None  # regression coefficients {a, b}
        self._epex_terminal_value_per_kwh: float = 0.0  # EUR/kWh incentive for end SOC
        self._epex_visualization: list[dict] = []  # scaled prices for UI only

        # Battery economics
        self._cycle_cost = self._config.get(
            CONF_BATTERY_CYCLE_COST, DEFAULT_BATTERY_CYCLE_COST
        )  # ct/kWh
        self._battery_efficiency = self._config.get(
            CONF_BATTERY_EFFICIENCY, DEFAULT_BATTERY_EFFICIENCY
        ) / 100.0  # convert percent to factor

        # Optimization log (recent decisions for UI)
        self._optimization_log: list[str] = []
        self._max_log_entries = 50
        self._last_dp_signature: str = ""  # to avoid re-logging identical plans

        # State
        self._strategy = STRATEGY_PRICE_OPTIMIZED
        self._operating_mode = MODE_IDLE
        self._current_price: float | None = None
        self._price_forecast: list[dict] = []
        self._battery_soc: float | None = None
        self._grid_power: float | None = None  # positive = import, negative = export
        self._estimated_savings: float = 0.0
        self._unsub_listeners: list = []
        self._inverter_active = False
        self._inverter_target_power: float = 0  # current target power for zero-feed
        self._inverter_actual_power: float | None = None  # actual power from sensor

        # Runtime toggles
        self._allow_grid_charging = True
        self._allow_discharging = True
        self._use_solar_forecast = bool(
            self._solar_forecast_entity or self._solar_forecast_entities
        )

        # Solar forecast and battery plan
        self._solar_forecast: dict[str, float] = {}  # hour_key -> Wh expected
        self._battery_plan: list[dict] = []  # hourly plan entries
        self._plan_summary: str = ""
        self._expected_solar_kwh: float = 0.0

        # PID controller state for zero-feed regulation
        self._pid_integral: float = 0.0
        self._pid_last_error: float | None = None
        self._pid_kp: float = 0.6   # proportional gain
        self._pid_ki: float = 0.15  # integral gain
        self._pid_kd: float = 0.1   # derivative gain

        # Hysteresis state for charger switching
        self._charger_last_switch_time: dict[int, datetime] = {}
        self._charger_min_on_time = timedelta(seconds=120)  # min 2 min on
        self._charger_min_off_time = timedelta(seconds=60)   # min 1 min off

        # Consumption statistics: rolling average per hour-of-day, split by day type
        # Format: {"wd_0": [w1, w2, ...], "we_0": [...], ...}
        # wd_ = weekday (Mon-Fri), we_ = weekend (Sat-Sun)
        self._consumption_store = Store(
            hass,
            STORAGE_VERSION_CONSUMPTION,
            f"{DOMAIN}.{entry.entry_id}.{STORAGE_KEY_CONSUMPTION}",
        )
        self._consumption_stats: dict[str, list[float]] = {}
        self._consumption_hourly_samples: list[float] = []  # samples within current hour
        self._consumption_last_hour: int | None = None
        self._consumption_last_daytype: str | None = None  # "wd" or "we"
        self._consumption_loaded = False

        # Tibber watchdog: restart integration if Pulse data is stale
        self._tibber_watchdog_stale_since: datetime | None = None
        self._tibber_watchdog_threshold = timedelta(minutes=5)
        self._tibber_last_restart: datetime | None = None
        self._tibber_restart_cooldown = timedelta(minutes=15)

        # Track whether entities have ever been seen (for startup race condition)
        self._price_entity_seen = False
        self._prices_entity_seen = False
        self._fallback_price_range: dict | None = None

    async def _load_consumption_stats(self) -> None:
        """Load consumption statistics from persistent storage.

        Migrates v1 format (keys "0"-"23") to v2 format (keys "wd_0", "we_0" etc.)
        by copying old data to both weekday and weekend slots.
        """
        if self._consumption_loaded:
            return
        data = await self._consumption_store.async_load()
        if data and isinstance(data, dict):
            # Detect v1 format: keys are "0"-"23" without prefix
            needs_migration = any(
                k.isdigit() and not any(k2.startswith("wd_") for k2 in data)
                for k in data
            )
            if needs_migration:
                migrated = {}
                for k, v in data.items():
                    if k.isdigit():
                        migrated[f"wd_{k}"] = v
                        migrated[f"we_{k}"] = list(v)  # copy for weekend too
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

        # Calculate pure house consumption by compensating for all known
        # loads/sources that the grid meter sees:
        #
        #   grid_power = house + chargers - solar - inverter_feed
        #   → house = grid_power - chargers + solar + inverter_feed
        #
        # We don't have a direct solar power sensor, but we can estimate
        # current solar from the forecast (hourly Wh → average W).
        charger_draw = sum(c["power"] for c in self._chargers if c["active"])
        inverter_feed = self._inverter_target_power if self._inverter_active else 0

        # Current solar production: prefer actual sensor, fall back to forecast
        solar_w = 0.0
        if self._solar_power is not None:
            solar_w = self._solar_power
        else:
            now_hour_key = now.strftime("%Y-%m-%dT%H")
            solar_wh = self._solar_forecast.get(now_hour_key, 0)
            if solar_wh > 0:
                solar_w = solar_wh  # Wh per hour ≈ average W for that hour

        house_w = self._grid_power - charger_draw + solar_w + inverter_feed
        house_w = max(0, house_w)

        self._consumption_hourly_samples.append(house_w)

        # Determine day type: weekday (Mon=0..Fri=4) vs weekend (Sat=5, Sun=6)
        day_type = "wd" if now.weekday() < 5 else "we"

        # When the hour changes, store the average for the previous hour
        if self._consumption_last_hour is not None and current_hour != self._consumption_last_hour:
            if self._consumption_hourly_samples:
                avg_w = sum(self._consumption_hourly_samples) / len(self._consumption_hourly_samples)
                # Use day type from when samples were collected
                store_daytype = self._consumption_last_daytype or day_type
                hour_key = f"{store_daytype}_{self._consumption_last_hour}"

                if hour_key not in self._consumption_stats:
                    self._consumption_stats[hour_key] = []

                self._consumption_stats[hour_key].append(round(avg_w, 1))

                # Keep only the last N days
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

                # Persist to disk
                await self._consumption_store.async_save(self._consumption_stats)

            self._consumption_hourly_samples = []

        self._consumption_last_hour = current_hour
        self._consumption_last_daytype = day_type

    def get_hourly_consumption_forecast(self, target_date: datetime | None = None) -> dict[int, float]:
        """Get predicted consumption per hour-of-day (0-23) in watts.

        Uses exponentially weighted average (EWA) where recent days have
        more influence than older days. This captures trends (e.g. new
        appliance, seasonal changes) better than a simple average.

        Weight for sample i (0=oldest, n-1=newest): w = α^(n-1-i)
        with α = 0.85 (15% decay per day → 7-day half-life).

        If an outside temperature sensor is configured, applies a
        temperature correction: below 15°C heating increases consumption,
        above 25°C cooling increases consumption. The correction is
        ~2% per °C deviation from the 15-25°C comfort zone.
        """
        if target_date is None:
            target_date = dt_util.now()
        day_type = "wd" if target_date.weekday() < 5 else "we"
        other_type = "we" if day_type == "wd" else "wd"
        alpha = 0.85  # decay factor: recent days weighted more

        # Temperature correction factor
        temp_factor = 1.0
        if self._outside_temp is not None:
            if self._outside_temp < 15.0:
                # Cold: heating adds ~2% per degree below 15°C
                temp_factor = 1.0 + (15.0 - self._outside_temp) * 0.02
            elif self._outside_temp > 25.0:
                # Hot: cooling adds ~2% per degree above 25°C
                temp_factor = 1.0 + (self._outside_temp - 25.0) * 0.02
            temp_factor = max(0.8, min(1.5, temp_factor))  # clamp

        forecast: dict[int, float] = {}
        for hour in range(24):
            samples = self._consumption_stats.get(f"{day_type}_{hour}", [])
            if not samples:
                samples = self._consumption_stats.get(f"{other_type}_{hour}", [])
            if samples:
                # Exponentially weighted average (newest sample = highest weight)
                n = len(samples)
                if n == 1:
                    forecast[hour] = samples[0]
                else:
                    weights = [alpha ** (n - 1 - i) for i in range(n)]
                    w_sum = sum(weights)
                    forecast[hour] = sum(w * s for w, s in zip(weights, samples)) / w_sum
            else:
                forecast[hour] = self._house_consumption_w

            # Apply temperature correction
            forecast[hour] *= temp_factor

        return forecast

    # ── Solar forecast calibration ───────────────────────────

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

        # Only calibrate once per day (when the date changes)
        if self._solar_calibration_last_date == today_str:
            return

        # Read actual solar production from yesterday
        # (the sensor shows "today", but at midnight it briefly shows yesterday's total)
        # Better approach: we stored yesterday's forecast, compare at end of day
        # For simplicity: calibrate when hour >= 20 (most solar done)
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
            return  # Skip cloudy/night days

        # Calculate today's total forecast
        today_prefix = now.strftime("%Y-%m-%dT")
        forecast_kwh = sum(
            v / 1000 for k, v in self._solar_forecast.items()
            if k.startswith(today_prefix)
        )

        if forecast_kwh < 0.1:
            return  # No forecast data

        # Ratio: actual / forecast (> 1 means forecast underestimates)
        ratio = actual_kwh / forecast_kwh
        # Clamp to reasonable range (0.3 - 3.0)
        ratio = max(0.3, min(3.0, ratio))

        self._solar_calibration_history.append(round(ratio, 3))

        # Keep rolling window
        max_days = SOLAR_CALIBRATION_ROLLING_DAYS
        if len(self._solar_calibration_history) > max_days:
            self._solar_calibration_history = self._solar_calibration_history[-max_days:]

        # Update factor
        self._solar_calibration_factor = (
            sum(self._solar_calibration_history)
            / len(self._solar_calibration_history)
        )
        self._solar_calibration_last_date = today_str

        # Persist
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

    def _log_optimization(self, message: str) -> None:
        """Add an entry to the optimization log (visible in UI)."""
        now = dt_util.now()
        entry = f"{now.strftime('%H:%M:%S')} {message}"
        self._optimization_log.append(entry)
        if len(self._optimization_log) > self._max_log_entries:
            self._optimization_log = self._optimization_log[-self._max_log_entries:]

    def _apply_solar_calibration(self) -> None:
        """Apply calibration factor to all solar forecast values."""
        if abs(self._solar_calibration_factor - 1.0) < 0.01:
            return  # Factor is ~1.0, no adjustment needed

        for key in self._solar_forecast:
            self._solar_forecast[key] *= self._solar_calibration_factor

        # Recalculate expected solar after calibration
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
        """Adjust remaining solar forecast using a Kalman filter.

        The Kalman filter combines the forecast (prediction) with actual
        production (measurement) to estimate the true correction factor.
        It reacts quickly to weather changes but doesn't overshoot on
        short fluctuations (unlike a simple ratio).

        State: x = solar correction factor (1.0 = forecast is accurate)
        Measurement: z = actual_so_far / forecast_so_far
        """
        if not self._solar_energy_today_entity:
            self._intraday_solar_factor = 1.0
            return

        now = dt_util.now()
        if now.hour < 8 or now.hour >= 20:
            self._intraday_solar_factor = 1.0
            # Reset Kalman state at night
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

        # Measurement: actual ratio
        measurement = actual_so_far_kwh / forecast_so_far_kwh
        measurement = max(0.2, min(3.0, measurement))

        # Kalman filter update
        # x = state estimate (correction factor), p = error covariance
        # Q = process noise (how fast the true factor can change)
        # R = measurement noise (how noisy the ratio measurement is)
        x = getattr(self, "_kalman_x", 1.0)
        p = getattr(self, "_kalman_p", 0.1)
        Q = 0.005  # process noise: factor changes slowly
        R = 0.05   # measurement noise: ratio can be noisy

        # Predict
        p = p + Q

        # Update
        K = p / (p + R)  # Kalman gain
        x = x + K * (measurement - x)  # updated estimate
        p = (1 - K) * p  # updated covariance

        # Clamp
        x = max(0.2, min(3.0, x))
        self._kalman_x = x
        self._kalman_p = p

        old_factor = self._intraday_solar_factor
        self._intraday_solar_factor = x

        # Apply to remaining forecast hours
        for key in self._solar_forecast:
            if key.startswith(today_prefix) and key >= now_key:
                self._solar_forecast[key] *= x

        # Recalculate expected solar
        remaining = {
            k: v for k, v in self._solar_forecast.items()
            if k >= now_key and k.startswith(today_prefix)
        }
        self._expected_solar_kwh = sum(remaining.values()) / 1000

        # Log only on significant changes
        if abs(x - old_factor) > 0.05 and abs(x - 1.0) > 0.1:
            msg = (
                f"Kalman Solar: Ist={actual_so_far_kwh:.1f} kWh, "
                f"Forecast={forecast_so_far_kwh:.1f} kWh, "
                f"Messung={measurement:.2f}, Kalman={x:.2f} "
                f"(K={K:.2f}), Rest={self._expected_solar_kwh:.1f} kWh"
            )
            _LOGGER.info(msg)
            self._log_optimization(msg)

    async def _check_tibber_watchdog(self) -> None:
        """Restart Tibber integration if Pulse data appears stale.

        Checks last_changed of the consumption entity. If it hasn't
        changed for > 5 minutes, reloads the Tibber config entry.
        Cooldown of 15 minutes between restarts to avoid loops.
        """
        if not self._pulse_consumption_entity:
            return

        state = self.hass.states.get(self._pulse_consumption_entity)
        if state is None:
            return

        now = dt_util.utcnow()

        # Check if state is fresh
        last_changed = state.last_changed
        if last_changed and (now - last_changed) < self._tibber_watchdog_threshold:
            # Data is fresh, reset stale tracker
            self._tibber_watchdog_stale_since = None
            return

        # Data is stale
        if self._tibber_watchdog_stale_since is None:
            self._tibber_watchdog_stale_since = now
            _LOGGER.warning(
                "Tibber Pulse data stale: %s last changed %s",
                self._pulse_consumption_entity,
                last_changed,
            )
            return

        # Check if we've been stale long enough to trigger a restart
        if (now - self._tibber_watchdog_stale_since) < self._tibber_watchdog_threshold:
            return

        # Check cooldown
        if self._tibber_last_restart and (now - self._tibber_last_restart) < self._tibber_restart_cooldown:
            _LOGGER.debug("Tibber watchdog: still in cooldown, skipping restart")
            return

        # Find and reload the Tibber config entry
        for entry in self.hass.config_entries.async_entries("tibber"):
            _LOGGER.warning(
                "Tibber Pulse data stale for >%d min – reloading Tibber integration (%s)",
                int(self._tibber_watchdog_threshold.total_seconds() / 60),
                entry.entry_id,
            )
            await self.hass.config_entries.async_reload(entry.entry_id)
            self._tibber_last_restart = now
            self._tibber_watchdog_stale_since = None
            break

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and process data, then decide on battery action."""
        _LOGGER.debug(
            "Update cycle - Price entity: '%s', Prices entity: '%s'",
            self._tibber_price_entity,
            self._tibber_prices_entity,
        )
        await self._load_consumption_stats()
        await self._load_solar_calibration()
        self._read_sensor_states()
        self._validate_operating_mode()
        await self._check_tibber_watchdog()
        await self._record_consumption()
        await self._update_price_forecast()
        await self._extend_prices_with_epex()
        await self._read_solar_forecast()
        self._apply_solar_calibration()
        self._apply_intraday_solar_correction()
        await self._calibrate_solar_forecast()
        self._create_battery_plan()

        if self._strategy == STRATEGY_PRICE_OPTIMIZED:
            await self._run_price_optimization()
        elif self._strategy == STRATEGY_SELF_CONSUMPTION:
            await self._run_self_consumption()
        # STRATEGY_MANUAL: no automatic charge/discharge actions

        # Always capture free solar surplus, regardless of strategy
        if self._operating_mode == MODE_IDLE:
            await self._try_solar_opportunistic()

        return self._build_data()

    def _read_sensor_states(self) -> None:
        """Read current sensor values from Home Assistant."""
        # Current electricity price
        price_state = self.hass.states.get(self._tibber_price_entity)
        if price_state and price_state.state not in ("unknown", "unavailable"):
            try:
                self._current_price = float(price_state.state)
                _LOGGER.debug(
                    "Price read successfully: %s EUR/kWh from '%s' (state: '%s')",
                    self._current_price,
                    self._tibber_price_entity,
                    price_state.state,
                )
            except (ValueError, TypeError) as err:
                self._current_price = None
                _LOGGER.warning(
                    "Could not convert price state '%s' to float from entity '%s': %s",
                    price_state.state,
                    self._tibber_price_entity,
                    err,
                )
        else:
            self._current_price = None
            if price_state:
                _LOGGER.debug(
                    "Price entity '%s' has state '%s' - not usable",
                    self._tibber_price_entity,
                    price_state.state,
                )
            else:
                # Use debug level if entity was never seen (startup race condition)
                log_fn = _LOGGER.warning if self._price_entity_seen else _LOGGER.debug
                log_fn(
                    "Price entity '%s' not found in Home Assistant. "
                    "Check that the entity ID is correct and the Tibber integration is loaded.",
                    self._tibber_price_entity,
                )

        if price_state:
            self._price_entity_seen = True

        # Battery SOC
        soc_state = self.hass.states.get(self._battery_soc_entity)
        if soc_state and soc_state.state not in ("unknown", "unavailable"):
            try:
                self._battery_soc = float(soc_state.state)
            except (ValueError, TypeError):
                self._battery_soc = None

        # Grid power from Tibber Pulse: consumption - production = net grid power
        # positive = net import from grid, negative = net export to grid
        consumption = None
        production = None

        cons_state = self.hass.states.get(self._pulse_consumption_entity)
        if cons_state and cons_state.state not in ("unknown", "unavailable"):
            try:
                consumption = float(cons_state.state)
            except (ValueError, TypeError):
                pass

        prod_state = self.hass.states.get(self._pulse_production_entity)
        if prod_state and prod_state.state not in ("unknown", "unavailable"):
            try:
                production = float(prod_state.state)
            except (ValueError, TypeError):
                pass

        if consumption is not None and production is not None:
            self._grid_power = consumption - production
        elif consumption is not None:
            self._grid_power = consumption
        elif production is not None:
            self._grid_power = -production
        else:
            self._grid_power = None

        # Inverter actual power
        if self._inverter_actual_power_entity:
            inv_state = self.hass.states.get(self._inverter_actual_power_entity)
            if inv_state and inv_state.state not in ("unknown", "unavailable"):
                try:
                    self._inverter_actual_power = float(inv_state.state)
                except (ValueError, TypeError):
                    self._inverter_actual_power = None
            else:
                self._inverter_actual_power = None

        # Current solar production (actual sensor, not forecast)
        if self._solar_power_entity:
            solar_state = self.hass.states.get(self._solar_power_entity)
            if solar_state and solar_state.state not in ("unknown", "unavailable"):
                try:
                    self._solar_power = float(solar_state.state)
                except (ValueError, TypeError):
                    self._solar_power = None
            else:
                self._solar_power = None

        # Outside temperature (for consumption forecast)
        if self._outside_temp_entity:
            temp_state = self.hass.states.get(self._outside_temp_entity)
            if temp_state and temp_state.state not in ("unknown", "unavailable"):
                try:
                    self._outside_temp = float(temp_state.state)
                except (ValueError, TypeError):
                    self._outside_temp = None
            else:
                self._outside_temp = None

        # Smartshunt battery voltage + current → real power
        if self._battery_voltage_entity:
            v_state = self.hass.states.get(self._battery_voltage_entity)
            if v_state and v_state.state not in ("unknown", "unavailable"):
                try:
                    self._battery_voltage = float(v_state.state)
                except (ValueError, TypeError):
                    self._battery_voltage = None
            else:
                self._battery_voltage = None

        if self._battery_current_entity:
            c_state = self.hass.states.get(self._battery_current_entity)
            if c_state and c_state.state not in ("unknown", "unavailable"):
                try:
                    self._battery_current = float(c_state.state)
                except (ValueError, TypeError):
                    self._battery_current = None
            else:
                self._battery_current = None

        if self._battery_voltage is not None and self._battery_current is not None:
            self._battery_real_power = abs(
                self._battery_voltage * self._battery_current
            )
        else:
            self._battery_real_power = None

        # Sync charger active flags with actual switch states
        self._sync_device_states()

    def _sync_device_states(self) -> None:
        """Synchronize internal active flags with actual switch entity states.

        After a restart, or if a switch is toggled externally, our internal
        flags may diverge from reality.  Reading the actual switch state on
        every cycle ensures we stay in sync.
        """
        for i, charger in enumerate(self._chargers):
            if not charger["switch"]:
                continue
            state = self.hass.states.get(charger["switch"])
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            actual_on = state.state == "on"
            if actual_on != charger["active"]:
                _LOGGER.info(
                    "Charger %d (%s): internal=%s, actual=%s → syncing",
                    i + 1, charger["switch"],
                    "ON" if charger["active"] else "OFF",
                    "ON" if actual_on else "OFF",
                )
                charger["active"] = actual_on

        if self._inverter_switch:
            state = self.hass.states.get(self._inverter_switch)
            if state and state.state not in ("unknown", "unavailable"):
                actual_on = state.state == "on"
                if actual_on != self._inverter_active:
                    _LOGGER.info(
                        "Inverter (%s): internal=%s, actual=%s → syncing",
                        self._inverter_switch,
                        "ON" if self._inverter_active else "OFF",
                        "ON" if actual_on else "OFF",
                    )
                    self._inverter_active = actual_on

        # Sync inverter power: after restart, internal target is 0 but the
        # real entity may still have the old value.  Read the actual value
        # and push 0 if we're idle, or adopt the real value if we're active.
        if self._inverter_power_entity:
            pw_state = self.hass.states.get(self._inverter_power_entity)
            if pw_state and pw_state.state not in ("unknown", "unavailable"):
                try:
                    actual_power = float(pw_state.state)
                except (ValueError, TypeError):
                    actual_power = None
                if actual_power is not None:
                    if self._operating_mode == MODE_IDLE and actual_power > 10:
                        # Inverter still running from before restart → shut it down
                        _LOGGER.warning(
                            "Inverter power entity shows %.0fW but mode is IDLE "
                            "→ resetting to 0",
                            actual_power,
                        )
                        self.hass.async_create_task(
                            self._set_inverter_power(0)
                        )
                    elif (self._inverter_active
                          and self._inverter_target_power == 0
                          and actual_power > 10):
                        # After restart: adopt the real value so regulation
                        # can adjust from here instead of ignoring it
                        _LOGGER.info(
                            "Inverter power: adopting actual value %.0fW "
                            "(internal was 0 after restart)",
                            actual_power,
                        )
                        self._inverter_target_power = actual_power

    def _validate_operating_mode(self) -> None:
        """Ensure operating mode matches actual device states.

        If we think we're charging but no charger is on, reset to idle.
        Same for discharging with inverter off.  This catches post-restart
        inconsistencies and external switch changes.
        """
        any_charger_on = any(c["active"] for c in self._chargers)

        if self._operating_mode in (MODE_CHARGING, MODE_SOLAR_CHARGING) and not any_charger_on:
            _LOGGER.warning(
                "Mode is %s but no charger is active → resetting to IDLE",
                self._operating_mode,
            )
            self._operating_mode = MODE_IDLE

        if self._operating_mode == MODE_DISCHARGING and not self._inverter_active:
            _LOGGER.warning(
                "Mode is DISCHARGING but inverter is not active → resetting to IDLE"
            )
            self._operating_mode = MODE_IDLE

    async def _update_price_forecast(self) -> None:
        """Update price forecast, preferring tibber.get_prices action."""
        # Try the tibber.get_prices action first (HA 2024.8+)
        if await self._fetch_prices_via_action():
            self._fallback_price_range = None
            _LOGGER.debug(
                "Price forecast built with %d entries (via tibber.get_prices action)",
                len(self._price_forecast),
            )
            return

        # Fallback: read from entity attributes (older Tibber integration)
        self._fetch_prices_from_attributes()

    async def _fetch_prices_via_action(self) -> bool:
        """Fetch prices using the tibber.get_prices action. Returns True on success."""
        if not self.hass.services.has_service("tibber", "get_prices"):
            _LOGGER.debug("tibber.get_prices action not available, using attribute fallback")
            return False

        now = dt_util.now()
        start = now.replace(minute=0, second=0, microsecond=0)
        end = (now + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0)

        try:
            response = await self.hass.services.async_call(
                "tibber",
                "get_prices",
                {"start": start.isoformat(), "end": end.isoformat()},
                blocking=True,
                return_response=True,
            )
        except Exception:
            _LOGGER.debug("tibber.get_prices action call failed", exc_info=True)
            return False

        if not response or "prices" not in response:
            _LOGGER.debug("tibber.get_prices returned no price data: %s", response)
            return False

        # Response format: {"prices": {"HomeName": [{"start_time": "...", "price": 0.46}, ...]}}
        self._price_forecast = []
        for home_prices in response["prices"].values():
            if not isinstance(home_prices, list):
                continue
            for entry in home_prices:
                if isinstance(entry, dict) and "start_time" in entry and "price" in entry:
                    self._price_forecast.append({
                        "start": str(entry["start_time"]),
                        "total": entry["price"],
                    })
            # Use the first home's prices only
            break

        return len(self._price_forecast) > 0

    def _fetch_prices_from_attributes(self) -> None:
        """Fallback: read price forecast from Tibber entity attributes."""
        prices_entity = self._tibber_prices_entity or self._tibber_price_entity
        prices_state = self.hass.states.get(prices_entity)
        if not prices_state:
            log_fn = _LOGGER.warning if self._prices_entity_seen else _LOGGER.debug
            log_fn(
                "Prices forecast entity '%s' not found in Home Assistant.",
                prices_entity,
            )
            return

        self._prices_entity_seen = True

        # Tibber provides today and tomorrow prices in attributes
        today = prices_state.attributes.get("today", [])
        tomorrow = prices_state.attributes.get("tomorrow", [])

        _LOGGER.debug(
            "Price forecast from '%s': %d today entries, %d tomorrow entries. "
            "Available attributes: %s",
            prices_entity,
            len(today) if isinstance(today, list) else 0,
            len(tomorrow) if isinstance(tomorrow, list) else 0,
            list(prices_state.attributes.keys()),
        )

        self._price_forecast = []
        for price_entry in today + tomorrow:
            if isinstance(price_entry, dict):
                self._price_forecast.append({
                    "start": price_entry.get("startsAt", ""),
                    "total": price_entry.get("total", 0),
                })

        # Fallback: if no today/tomorrow forecast available, build a simplified
        # forecast from summary attributes (max_price, avg_price, min_price)
        if not self._price_forecast:
            max_price = prices_state.attributes.get("max_price")
            avg_price = prices_state.attributes.get("avg_price")
            min_price = prices_state.attributes.get("min_price")

            if max_price is not None and min_price is not None:
                _LOGGER.debug(
                    "No hourly forecast available. Using price summary attributes: "
                    "min=%.4f, avg=%.4f, max=%.4f",
                    min_price,
                    avg_price or 0,
                    max_price,
                )
                self._fallback_price_range = {
                    "min": float(min_price),
                    "avg": float(avg_price) if avg_price is not None else None,
                    "max": float(max_price),
                }
            else:
                self._fallback_price_range = None
                _LOGGER.debug(
                    "No hourly forecast and no summary price attributes available "
                    "on '%s'.",
                    prices_entity,
                )
        else:
            self._fallback_price_range = None

    async def _extend_prices_with_epex(self) -> None:
        """Use EPEX Predictor to set DP terminal value and provide visualization data.

        EPEX predictions are NOT added to the price forecast for DP planning.
        Instead, they determine the terminal value of the DP: should the
        battery end the Tibber window full or empty?

        - High EPEX prices ahead → high terminal SOC value → DP keeps battery full
        - Low EPEX prices ahead → low terminal value → DP discharges everything

        The scaled EPEX prices are stored separately for UI visualization only.
        """
        if not self._epex_enabled or not self._price_forecast:
            self._epex_terminal_value_per_kwh = 0.0
            return

        # Refresh EPEX cache if stale
        now = dt_util.now()
        if (self._epex_cache_time is None
                or now - self._epex_cache_time > self._epex_cache_ttl):
            await self._fetch_epex_prices()

        if not self._epex_cache:
            self._epex_terminal_value_per_kwh = 0.0
            return

        # Find the end of Tibber data
        tibber_end = None
        for p in reversed(self._price_forecast):
            try:
                tibber_end = datetime.fromisoformat(p["start"])
                if tibber_end.tzinfo is not None:
                    tibber_end = dt_util.as_local(tibber_end)
                break
            except (ValueError, TypeError):
                continue

        if tibber_end is None:
            self._epex_terminal_value_per_kwh = 0.0
            return

        # Build lookups for regression
        tibber_by_hour: dict[str, list[float]] = {}
        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                hk = start.strftime("%Y-%m-%dT%H")
                tibber_by_hour.setdefault(hk, []).append(p["total"])
            except (ValueError, TypeError):
                continue

        epex_by_hour: dict[str, list[float]] = {}
        epex_future: list[dict] = []
        for p in self._epex_cache:
            try:
                start = datetime.fromisoformat(p["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                hk = start.strftime("%Y-%m-%dT%H")
                epex_by_hour.setdefault(hk, []).append(p["total"])
                if start > tibber_end:
                    epex_future.append(p)
            except (ValueError, TypeError):
                continue

        if not epex_future:
            self._epex_terminal_value_per_kwh = 0.0
            return

        # Linear regression: Tibber = a + b × EPEX
        pairs: list[tuple[float, float]] = []
        for hk in tibber_by_hour:
            if hk in epex_by_hour:
                t_avg = sum(tibber_by_hour[hk]) / len(tibber_by_hour[hk])
                e_avg = sum(epex_by_hour[hk]) / len(epex_by_hour[hk])
                pairs.append((e_avg, t_avg))

        if len(pairs) < 3:
            self._epex_terminal_value_per_kwh = 0.0
            return

        n_pairs = len(pairs)
        x_vals = [p[0] for p in pairs]
        y_vals = [p[1] for p in pairs]
        x_mean = sum(x_vals) / n_pairs
        y_mean = sum(y_vals) / n_pairs

        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
        den = sum((x - x_mean) ** 2 for x in x_vals)

        if abs(den) < 1e-10:
            b = 1.0
            a = y_mean - x_mean
        else:
            b = num / den
            a = y_mean - b * x_mean

        b = max(0.5, min(3.0, b))
        a = max(-0.05, min(0.50, a))

        self._epex_markup = {"a": round(a, 4), "b": round(b, 4)}

        # Build EPEX visualization data (NOT added to _price_forecast)
        self._epex_visualization: list[dict] = []
        for p in epex_future:
            predicted = a + b * p["total"]
            self._epex_visualization.append({
                "start": p["start"],
                "total": round(max(0, predicted), 4),
                "source": "epex_predictor",
                "epex_spot": round(p["total"], 4),
            })

        # Calculate terminal value from EPEX predicted future prices.
        # Same formula as base_tv but with actual prediction data:
        # TV = median_future × η × uncertainty_discount − half_cycle
        # This is MORE informed than the base_tv (which uses current Tibber median).
        predicted_prices = sorted(max(0, a + b * p["total"]) for p in epex_future)
        median_future = predicted_prices[len(predicted_prices) // 2]
        efficiency = self._battery_efficiency
        half_cycle = self._cycle_cost / 100 / 2
        uncertainty_discount = 0.8  # EPEX is more reliable than base → less discount
        self._epex_terminal_value_per_kwh = max(
            0.0,
            median_future * efficiency * uncertainty_discount - half_cycle
        )

        epex_sig = f"{len(epex_future)}:{a:.4f}:{b:.4f}:{self._epex_terminal_value_per_kwh:.4f}"
        if epex_sig != getattr(self, "_last_epex_signature", ""):
            self._last_epex_signature = epex_sig
            tv_ct = self._epex_terminal_value_per_kwh * 100
            msg = (
                f"EPEX Terminal-Value: {self._fmt_ct(tv_ct)} ct/kWh "
                f"(Median Zukunft {self._fmt_ct(median_future * 100)} ct, "
                f"Unsicherheit 20%, "
                f"Regression: {self._fmt_ct(a * 100)} ct + {b:.2f}\u00d7EPEX)"
            )
            _LOGGER.info(msg)
            self._log_optimization(msg)

    async def _fetch_epex_prices(self) -> None:
        """Fetch price predictions from EPEX Predictor API."""
        now = dt_util.now()
        url = (
            f"{EPEX_PREDICTOR_BASE_URL}/prices"
            f"?region={self._epex_region}"
            f"&unit=EUR_PER_KWH"
            f"&timezone=Europe/Berlin"
            f"&hours=96"
        )

        try:
            session = aiohttp.ClientSession()
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("EPEX API returned status %d", resp.status)
                        return
                    data = await resp.json()
            finally:
                await session.close()

            prices = data.get("prices", [])
            if not prices:
                _LOGGER.debug("EPEX API returned no prices")
                return

            # Convert to our internal format
            self._epex_cache = []
            for entry in prices:
                if "startsAt" in entry and "total" in entry:
                    self._epex_cache.append({
                        "start": entry["startsAt"],
                        "total": entry["total"],
                    })

            self._epex_cache_time = now
            _LOGGER.debug(
                "EPEX cache refreshed: %d entries for region %s",
                len(self._epex_cache), self._epex_region,
            )

        except aiohttp.ClientError as err:
            _LOGGER.warning("EPEX API request failed: %s", err)
        except Exception:
            _LOGGER.warning("EPEX API error", exc_info=True)

        _LOGGER.debug("Price forecast built with %d entries", len(self._price_forecast))

    # ── Solar forecast ──────────────────────────────────────────────

    async def _read_solar_forecast(self) -> None:
        """Read solar production forecast from configured entities.

        Supports multiple entities (e.g. multiple roof arrays, mixed services).
        All forecasts are summed together per hour.

        Supported formats per entity:
        - Forecast.Solar energy platform (wh_hours via config entry)
        - Sensor attributes: watt_hours_period {ISO_datetime: Wh}
        - Solcast / generic: forecast attribute [{period_start, pv_estimate}, ...]
        - Sensor attributes: watt_hours (cumulative)
        """
        self._solar_forecast = {}
        self._expected_solar_kwh = 0.0

        if not self._use_solar_forecast:
            return

        # Collect all configured solar forecast entity IDs
        entity_ids: list[str] = []
        if self._solar_forecast_entity:
            entity_ids.append(self._solar_forecast_entity)
        for eid in self._solar_forecast_entities:
            if eid and eid not in entity_ids:
                entity_ids.append(eid)

        if not entity_ids:
            return

        # Try to read from the HA energy platform first (works for forecast_solar)
        energy_data_found = await self._read_energy_solar_forecasts(entity_ids)

        # For entities not covered by energy platform, fall back to attributes
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
        # Only sum remaining hours of TODAY (not tomorrow)
        remaining = {
            k: v for k, v in self._solar_forecast.items()
            if k >= now_key and k.startswith(today_prefix)
        }
        self._expected_solar_kwh = sum(remaining.values()) / 1000

    async def _read_energy_solar_forecasts(
        self, entity_ids: list[str]
    ) -> set[str]:
        """Try to read solar forecasts via the HA energy platform.

        This works for integrations like forecast_solar that expose data
        through the energy platform but not through sensor attributes.

        Returns set of entity_ids that were successfully read.
        """
        covered: set[str] = set()

        # Map entity_ids to their config entries
        entity_registry = er.async_get(self.hass)
        entries_to_fetch: dict[str, list[str]] = {}  # config_entry_id -> [entity_ids]

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

                # forecast_solar stores an Estimate object in runtime_data
                estimate = config_entry.runtime_data
                wh_period = getattr(estimate, "wh_period", None)
                if not wh_period:
                    # Try .data.wh_period for wrapped data
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

        # Format 1: Forecast.Solar watt_hours_period {datetime_str: Wh}
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

        # Format 3: Forecast.Solar watt_hours (cumulative) → derive per-period
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

    @staticmethod
    def _to_hour_key(dt_str: str) -> str:
        """Convert a datetime string to an hour key like '2024-01-15T14'.

        Always normalizes to local time so that keys from different sources
        (Tibber in UTC+1, Forecast.Solar in UTC, etc.) match correctly.
        """
        dt = datetime.fromisoformat(str(dt_str))
        if dt.tzinfo is not None:
            dt = dt_util.as_local(dt)
        return dt.strftime("%Y-%m-%dT%H")

    # ── Battery plan ────────────────────────────────────────────────

    def _create_battery_plan(self) -> None:
        """Create a cost-optimized battery plan using Dynamic Programming.

        Uses backward DP over discretized SOC states to find the globally
        optimal sequence of charge/discharge/idle actions that maximizes
        profit (= revenue from discharging − cost of charging − cycle wear).

        Considers:
        - Effective charge cost (solar reduces grid draw)
        - Battery round-trip efficiency
        - Cycle degradation cost (configurable ct/kWh)
        - Pre-solar discharge to create headroom for free solar
        - Weekday/weekend consumption patterns
        - Intraday solar correction factor
        """
        now = dt_util.now()

        slot_data, slot_h = self._build_slot_data(now)
        if not slot_data:
            self._battery_plan = []
            self._plan_summary = "Keine Preisdaten verfügbar"
            return

        # Battery parameters scaled to slot duration
        current_soc = self._battery_soc if self._battery_soc is not None else 50.0
        charge_power_w = sum(c["power"] for c in self._chargers)
        charge_kwh_slot = charge_power_w / 1000 * slot_h
        discharge_power_w = self._inverter_power or 800
        discharge_kwh_slot = discharge_power_w / 1000 * slot_h
        cap = self._battery_capacity
        efficiency = self._battery_efficiency  # round-trip, e.g. 0.90
        cycle_cost_eur = self._cycle_cost / 100  # convert ct to EUR

        # Per-day consumption forecast (weekday/weekend aware)
        # Cache forecasts per date to avoid recalculation
        consumption_cache: dict[str, dict[int, float]] = {}

        # Enrich slot data with solar info and effective charge cost
        slots_per_hour = max(1, round(1.0 / slot_h))
        for h in slot_data:
            hour_of_day = h.get("hour_of_day", 12)
            slot_date = h["start_dt"]
            date_key = slot_date.strftime("%Y-%m-%d")
            if date_key not in consumption_cache:
                consumption_cache[date_key] = self.get_hourly_consumption_forecast(slot_date)
            h["house_w"] = consumption_cache[date_key].get(hour_of_day, self._house_consumption_w)
            house_kwh_slot = h["house_w"] / 1000 * slot_h

            solar_wh_hour = self._solar_forecast.get(h["hour_key"], 0)
            h["solar_kwh"] = (solar_wh_hour / 1000) / slots_per_hour
            h["solar_wh_hour_raw"] = solar_wh_hour
            h["solar_surplus_kwh"] = max(0, h["solar_kwh"] - house_kwh_slot)

            if charge_kwh_slot > 0:
                grid_fraction = max(0, charge_kwh_slot - h["solar_surplus_kwh"]) / charge_kwh_slot
            else:
                grid_fraction = 1.0
            h["effective_charge_cost"] = grid_fraction * h["price"]
            h["grid_fraction"] = grid_fraction

        n = len(slot_data)
        hourly_data = slot_data

        # ── Scenario-based DP ────────────────────────────────────
        # Run the DP for 3 scenarios with different solar/consumption
        # assumptions. Pick the plan that is profitable in ALL scenarios
        # (conservative/robust approach).
        #
        # - Expected:    solar 100%, consumption 100%
        # - Pessimistic: solar  60%, consumption 120%
        # - Optimistic:  solar 120%, consumption  80%
        #
        # For each scenario, adjust grid_fraction per slot and re-run DP.
        # The final plan uses the action that appears in at least 2 of 3
        # scenarios (majority vote), defaulting to idle on disagreement.

        scenarios = [
            {"name": "expected",    "solar_factor": 1.0, "consumption_factor": 1.0},
            {"name": "pessimistic", "solar_factor": 0.6, "consumption_factor": 1.2},
            {"name": "optimistic",  "solar_factor": 1.2, "consumption_factor": 0.8},
        ]

        scenario_actions: list[list[str]] = []
        scenario_profits: list[float] = []

        for scenario in scenarios:
            sf = scenario["solar_factor"]
            cf = scenario["consumption_factor"]

            # Adjust slot data for this scenario
            for h in hourly_data:
                adj_solar = h["solar_kwh"] * sf
                adj_house = h["house_w"] * cf / 1000 * slot_h
                adj_surplus = max(0, adj_solar - adj_house)
                if charge_kwh_slot > 0:
                    h["_scn_grid_frac"] = max(0, charge_kwh_slot - adj_surplus) / charge_kwh_slot
                else:
                    h["_scn_grid_frac"] = 1.0

            actions, profit = self._solve_dp(
                hourly_data, n, current_soc, charge_kwh_slot, discharge_kwh_slot,
                cap, efficiency, cycle_cost_eur, slot_h,
            )
            scenario_actions.append(actions)
            scenario_profits.append(profit)

        # Restore original grid_fraction for plan building
        for h in hourly_data:
            h["_scn_grid_frac"] = h["grid_fraction"]

        # Asymmetric vote: charge follows expected scenario (index 0),
        # discharge requires majority (≥2 of 3 scenarios).
        #
        # Rationale: charging at cheap prices is low-risk (worst case: battery
        # is full and solar surplus gets curtailed, but _try_solar_opportunistic
        # handles that at runtime). Discharging at the wrong time loses money
        # directly, so we require consensus.
        expected = scenario_actions[0]
        actions = []
        for t in range(n):
            exp_act = expected[t]
            if exp_act == "charge":
                # Trust expected scenario for charge decisions
                actions.append("charge")
            elif exp_act == "discharge":
                # Require majority for discharge (conservative)
                votes = [sa[t] for sa in scenario_actions]
                if votes.count("discharge") >= 2:
                    actions.append("discharge")
                else:
                    actions.append("idle")
            else:
                # idle/hold: check if any scenario wants to charge here
                # (pessimistic might want to charge where expected doesn't)
                votes = [sa[t] for sa in scenario_actions]
                if votes.count("charge") >= 2:
                    actions.append("charge")
                elif votes.count("discharge") >= 2:
                    actions.append("discharge")
                else:
                    actions.append("idle")

        # Use the pessimistic profit as the reported profit (conservative)
        actual_profit = scenario_profits[1]  # pessimistic scenario

        _LOGGER.debug(
            "Scenario DP: expected=%.3f, pessimistic=%.3f, optimistic=%.3f EUR",
            scenario_profits[0], scenario_profits[1], scenario_profits[2],
        )

        # ── Smooth micro-cycles ─────────────────────────────────
        # The DP can create rapid charge↔discharge cycling when price
        # differences are small (e.g. 1-2 ct), especially with EPEX data.
        # This loses money due to efficiency + cycle costs.
        #
        # Two-pass smoothing:
        # Pass 1: Require minimum run length of 2 slots (30 min) for
        #         charge/discharge. Short runs become idle.
        # Pass 2: Remove charge↔discharge transitions where the price
        #         spread is below the break-even threshold.
        smoothed = 0

        # Pass 1: Remove single-slot charge/discharge enclaves.
        # A single charge or discharge slot surrounded by idle/hold is
        # almost always a DP artifact from SOC quantization near limits.
        # But single slots at the START or END of a block are fine.
        for i in range(1, n - 1):
            act = actions[i]
            if act in ("charge", "discharge"):
                prev_same = (actions[i - 1] == act)
                next_same = (actions[i + 1] == act)
                if not prev_same and not next_same:
                    actions[i] = "idle"
                    smoothed += 1

        # Pass 2: Remove rapid charge↔discharge alternation
        # If charge is immediately followed by discharge (or vice versa)
        # and the price spread is below break-even, convert to idle.
        avg_plan_price = sum(h["price"] for h in hourly_data) / n if n else 0.25
        break_even_spread = cycle_cost_eur + (1 - efficiency) * avg_plan_price
        for i in range(1, n):
            prev_a, cur_a = actions[i - 1], actions[i]
            if (prev_a == "charge" and cur_a == "discharge") or \
               (prev_a == "discharge" and cur_a == "charge"):
                p_prev = hourly_data[i - 1]["price"]
                p_cur = hourly_data[i]["price"]
                spread = abs(p_cur - p_prev)
                if spread < break_even_spread:
                    actions[i] = "idle"
                    smoothed += 1

        # Pass 3: Swap cheap discharge slots with more expensive idle slots.
        # Only swap if the idle slot comes AFTER the discharge slot (so
        # the saved energy is available later) and the price gain is
        # meaningful (> 1 ct difference to avoid pointless swaps).
        swapped = 0
        while True:
            cheapest_d_idx = None
            cheapest_d_price = float("inf")
            for i in range(n):
                if actions[i] == "discharge" and hourly_data[i]["price"] < cheapest_d_price:
                    cheapest_d_price = hourly_data[i]["price"]
                    cheapest_d_idx = i

            best_idle_idx = None
            best_idle_price = 0.0
            for i in range(n):
                if actions[i] == "idle" and hourly_data[i]["price"] > best_idle_price:
                    best_idle_price = hourly_data[i]["price"]
                    best_idle_idx = i

            if (cheapest_d_idx is not None
                    and best_idle_idx is not None
                    and best_idle_price > cheapest_d_price + 0.01  # >1ct gain
                    and best_idle_idx > cheapest_d_idx):  # idle must be later
                actions[cheapest_d_idx] = "idle"
                actions[best_idle_idx] = "discharge"
                swapped += 1
            else:
                break

        smoothed += swapped

        # Pass 4: Target-based backward charge fill for EACH discharge block.
        # The DP may plan too few charge slots due to floating-point break-even.
        # This pass simulates the full plan, finds each discharge block start,
        # and backfills idle→charge if SOC < max_soc at that point.
        filled = 0
        charge_pct_per_slot = charge_kwh_slot / cap * 100 if cap > 0 else 0

        if charge_kwh_slot > 0 and charge_pct_per_slot > 0:
            # Simulate full plan to find SOC at each discharge block start
            sim_soc = current_soc
            discharge_block_starts = []
            for i in range(n):
                # Detect start of a discharge block (non-discharge → discharge)
                if actions[i] == "discharge" and (i == 0 or actions[i - 1] != "discharge"):
                    discharge_block_starts.append((i, sim_soc))

                if actions[i] == "charge":
                    delta = min(charge_kwh_slot, (self._max_soc - sim_soc) / 100 * cap)
                    sim_soc = min(self._max_soc, sim_soc + delta / cap * 100)
                elif actions[i] == "discharge":
                    delta = min(discharge_kwh_slot, (sim_soc - self._min_soc) / 100 * cap)
                    sim_soc = max(self._min_soc, sim_soc - delta / cap * 100)

            # For each discharge block, check if SOC was below max_soc
            for block_start, soc_at_start in discharge_block_starts:
                soc_gap = self._max_soc - soc_at_start
                if soc_gap <= 1.0:
                    continue  # already near max_soc

                slots_needed = int(soc_gap / charge_pct_per_slot) + 1

                # Collect idle/hold slots BEFORE this discharge block
                candidates = []
                for i in range(block_start):
                    if actions[i] in ("idle", "hold"):
                        candidates.append((hourly_data[i]["price"], -i, i))
                # Sort: cheapest price first, latest time first (for solar room)
                candidates.sort()

                block_filled = 0
                for _, _, idx in candidates[:slots_needed]:
                    actions[idx] = "charge"
                    block_filled += 1
                filled += block_filled

                if block_filled:
                    _LOGGER.info(
                        "Pass 4 fill block@t=%d: SOC was %.1f%%, gap %.1f%% "
                        "→ added %d charge slots (needed %d)",
                        block_start, soc_at_start, soc_gap,
                        block_filled, slots_needed,
                    )

        smoothed += filled

        # Pass 5: Merge separated charge blocks at same price.
        # If a non-main charge block has similar price to the main block
        # AND there are enough hold/idle slots between them, dissolve the
        # earlier block and expand the main block backward instead.
        # This prevents night charging when morning slots at the same price
        # are available (leaving room for solar).
        charge_blocks = []
        block_s = None
        for i in range(n):
            if actions[i] == "charge":
                if block_s is None:
                    block_s = i
            else:
                if block_s is not None:
                    charge_blocks.append((block_s, i - block_s))
                    block_s = None
        if block_s is not None:
            charge_blocks.append((block_s, n - block_s))

        if len(charge_blocks) > 1:
            main_block = max(charge_blocks, key=lambda b: b[1])
            main_start, main_len = main_block
            main_price = hourly_data[main_start]["price"] if main_start < n else 0
            removed_islands = 0

            for start, length in charge_blocks:
                if (start, length) == main_block:
                    continue
                block_price = hourly_data[start]["price"] if start < n else 0

                # Check if block is BEFORE main and at similar price
                if start < main_start and abs(block_price - main_price) < 0.005:
                    # Count available hold/idle slots between this block
                    # and the main block that could replace it
                    gap_slots = []
                    for j in range(start + length, main_start):
                        if actions[j] in ("idle", "hold"):
                            p = hourly_data[j]["price"]
                            if abs(p - main_price) < 0.02:  # within 2ct
                                gap_slots.append(j)

                    if len(gap_slots) >= length:
                        # Dissolve this block
                        for j in range(start, start + length):
                            actions[j] = "idle"
                            removed_islands += 1
                        # Expand main block backward using latest gap slots
                        gap_slots.sort(reverse=True)  # latest first
                        for j in gap_slots[:length]:
                            actions[j] = "charge"
                        _LOGGER.info(
                            "Pass 5: merged %d charge slots from t=%d "
                            "into main block (shifted to latest slots)",
                            length, start,
                        )
                elif start >= main_start + main_len:
                    # Block AFTER main: remove if small and separated
                    main_end = main_start + main_len
                    gap = start - main_end
                    if length < 4 and gap > 2:
                        for j in range(start, start + length):
                            actions[j] = "idle"
                            removed_islands += 1
                else:
                    # Block before main, different price: remove if small
                    main_end = main_start + main_len
                    gap = min(abs(start - main_end),
                              abs(main_start - (start + length)))
                    if length < 4 and gap > 2:
                        for j in range(start, start + length):
                            actions[j] = "idle"
                            removed_islands += 1

            if removed_islands:
                smoothed += removed_islands
                _LOGGER.info(
                    "Pass 5: adjusted %d charge slots total",
                    removed_islands,
                )

        if smoothed:
            _LOGGER.info(
                "Plan smoothing: %d slots adjusted (%d swaps, %d filled)",
                smoothed, swapped, filled,
            )

        # Log DP result (only when plan changes)
        charge_slots = sum(1 for a in actions if a == "charge")
        discharge_slots = sum(1 for a in actions if a == "discharge")
        dp_signature = f"{charge_slots}:{discharge_slots}:{actual_profit:.2f}"

        if dp_signature != self._last_dp_signature:
            profit_str = f"{actual_profit:.3f}".replace(".", ",")
            # Count how many charge slots have solar contribution
            solar_assisted = sum(
                1 for i, a in enumerate(actions)
                if a == "charge" and hourly_data[i]["solar_surplus_kwh"] > 0.05
            )
            solar_info = f", davon {solar_assisted} mit Solar" if solar_assisted else ""
            dp_msg = (
                f"DP-Optimierung: {charge_slots} Lade{solar_info}, "
                f"{discharge_slots} Entlade Slots \u2192 Profit {profit_str} EUR "
                f"(Effizienz {self._battery_efficiency*100:.0f}%, "
                f"Zykluskosten {self._cycle_cost:.0f} ct/kWh)"
            )
            _LOGGER.info(dp_msg)
            self._log_optimization(dp_msg)
            self._last_dp_signature = dp_signature

        # ── Pre-solar discharge enhancement ──────────────────────
        # DP handles this implicitly, but we track pre-solar slots for reasons
        presolar_discharge_hours: set[int] = set()
        first_solar_idx = next(
            (i for i, h in enumerate(hourly_data) if h["solar_surplus_kwh"] > 0.05),
            n,
        )
        for i in range(first_solar_idx):
            if actions[i] == "discharge":
                # Check if there's later solar that benefits from this
                later_solar = any(
                    hourly_data[j]["solar_surplus_kwh"] > 0.05
                    for j in range(i + 1, n)
                )
                if later_solar:
                    presolar_discharge_hours.add(i)

        # ── Build plan with SOC simulation and reasons ───────────
        # The DP outputs "charge", "discharge", "idle".
        # The SOC simulation validates feasibility and converts
        # infeasible actions to idle (e.g. discharge at min_soc).
        self._battery_plan = []
        estimated_soc = current_soc
        charge_count = 0
        discharge_count = 0
        grid_charge_kwh = 0.0

        for i, h in enumerate(hourly_data):
            action = actions[i]
            delta_kwh = 0.0

            if action == "charge":
                if estimated_soc >= self._max_soc:
                    action = "idle"
                else:
                    delta_kwh = min(charge_kwh_slot, (self._max_soc - estimated_soc) / 100 * cap)
                    grid_charge_kwh += delta_kwh * h["grid_fraction"]
            elif action == "discharge":
                if estimated_soc <= self._min_soc:
                    action = "idle"
                else:
                    delta_kwh = min(discharge_kwh_slot, (estimated_soc - self._min_soc) / 100 * cap)

            if action == "charge":
                estimated_soc += delta_kwh / cap * 100
                charge_count += 1
            elif action == "discharge":
                estimated_soc -= delta_kwh / cap * 100
                discharge_count += 1

            if action == "idle":
                has_future_discharge = any(
                    actions[j] == "discharge" for j in range(i + 1, n)
                )
                if has_future_discharge and estimated_soc > self._min_soc + 5:
                    action = "hold"

            estimated_soc = max(self._min_soc, min(self._max_soc, estimated_soc))

            # Build reason text with clear explanation
            reason = self._build_plan_reason(
                action, h, i, presolar_discharge_hours, hourly_data, actions
            )

            self._battery_plan.append({
                "hour": h.get("slot_key", h["hour_key"] + ":00"),
                "price": round(h["price"], 4),
                "solar_kwh": round(h["solar_kwh"], 3),
                "solar_wh_hour": round(h.get("solar_wh_hour_raw", 0)),
                "solar_surplus_kwh": round(h["solar_surplus_kwh"], 2),
                "expected_soc": round(estimated_soc, 1),
                "action": action,
                "reason": reason,
            })

        # Summary
        parts = []
        def _fmt_duration(slots: int) -> str:
            total_min = round(slots * slot_h * 60)
            if total_min >= 60 and total_min % 60 == 0:
                return f"{total_min // 60}h"
            if total_min >= 60:
                return f"{total_min // 60}h{total_min % 60:02d}"
            return f"{total_min}min"

        if charge_count:
            parts.append(f"{_fmt_duration(charge_count)} Laden ({self._fmt_ct(grid_charge_kwh)} kWh)")
        if discharge_count:
            parts.append(f"{_fmt_duration(discharge_count)} Entladen")
        self._plan_summary = " | ".join(parts) if parts else "Kein Plan erstellt"

        self._estimated_savings = round(max(0, actual_profit), 2)

        _LOGGER.debug(
            "Battery plan (DP): %s | Profit: %.2f EUR "
            "(SOC: %.0f%%, Solar: %.1f kWh, Efficiency: %.0f%%)",
            self._plan_summary,
            actual_profit,
            current_soc,
            self._expected_solar_kwh,
            efficiency * 100,
        )

    @staticmethod
    def _fmt_ct(value: float) -> str:
        """Format a ct/kWh value with German decimal comma."""
        return f"{value:.1f}".replace(".", ",")

    def _build_plan_reason(
        self,
        action: str,
        h: dict,
        slot_idx: int,
        presolar_set: set[int],
        hourly_data: list[dict],
        actions: list[str],
    ) -> str:
        """Build a human-readable reason for a plan action."""
        fc = self._fmt_ct
        eff_pct = f", η={self._battery_efficiency*100:.0f}%" if self._battery_efficiency < 0.99 else ""
        cycle_info = f", Zyklus {fc(self._cycle_cost)}ct" if self._cycle_cost > 0 else ""

        if action == "charge" and h["solar_surplus_kwh"] > 0.05:
            grid_pct = round(h["grid_fraction"] * 100)
            return (
                f"Solar+Netz ({grid_pct}% Netz à {fc(h['price']*100)} ct "
                f"\u2192 eff. {fc(h['effective_charge_cost']*100)} ct/kWh{cycle_info})"
            )
        if action == "charge":
            return f"Netz-Laden ({fc(h['price']*100)} ct/kWh{cycle_info})"
        if action == "solar_charge":
            return f"Solar {fc(h['solar_surplus_kwh'])} kWh (kostenlos)"
        if action == "discharge":
            if slot_idx in presolar_set:
                return f"Platz für Solar schaffen ({fc(h['price']*100)} ct/kWh)"
            nearby_charge = None
            for j in range(len(actions)):
                if actions[j] == "charge" and j != slot_idx:
                    nearby_charge = hourly_data[j]
                    break
            if nearby_charge:
                spread = (h["price"] - nearby_charge["effective_charge_cost"]) * 100
                eff_loss = nearby_charge["effective_charge_cost"] * 100 * (1 - self._battery_efficiency)
                net_spread = spread - self._cycle_cost - eff_loss
                return (
                    f"Entladen ({fc(h['price']*100)} ct, "
                    f"Spread {fc(spread)} ct, netto {fc(net_spread)} ct{eff_pct})"
                )
            return f"Entladen ({fc(h['price']*100)} ct/kWh{eff_pct})"
        if action == "hold":
            return "Halten für teure Stunden"
        return "Keine Aktion"

    def _solve_dp(
        self,
        hourly_data: list[dict],
        n: int,
        current_soc: float,
        charge_kwh_slot: float,
        discharge_kwh_slot: float,
        cap: float,
        efficiency: float,
        cycle_cost_eur: float,
        slot_h: float,
    ) -> tuple[list[str], float]:
        """Run the core DP optimization. Returns (actions, profit).

        Uses _scn_grid_frac from hourly_data for the current scenario.
        """
        # SOC discretization
        charge_soc_pct = charge_kwh_slot / cap * 100 if cap > 0 else 5
        discharge_soc_pct = discharge_kwh_slot / cap * 100 if cap > 0 else 5
        min_delta_pct = min(charge_soc_pct, discharge_soc_pct) if charge_soc_pct > 0 else discharge_soc_pct
        soc_step = max(0.5, min(3.0, min_delta_pct * 0.45))
        soc_step = round(soc_step, 1) or 0.5

        soc_levels = []
        s = float(self._min_soc)
        while s <= self._max_soc + 0.01:
            soc_levels.append(round(s, 1))
            s += soc_step
        num_soc = len(soc_levels)

        def soc_to_idx(soc: float) -> int:
            idx = round((soc - self._min_soc) / soc_step)
            return max(0, min(num_soc - 1, idx))

        half_cycle_eur = cycle_cost_eur / 2

        # Terminal value: what is stored energy worth beyond the planning horizon?
        # Use MEDIAN price (not upper-third) as conservative reference, plus a
        # 30% uncertainty discount. The future is not guaranteed — tomorrow
        # might have cheaper charging opportunities that make today's expensive
        # charging unnecessary.
        all_prices = sorted(h["price"] for h in hourly_data)
        median_price = all_prices[len(all_prices) // 2] if all_prices else 0.25
        uncertainty_discount = 0.7  # 30% discount for future uncertainty
        base_tv = max(0.0, median_price * efficiency * uncertainty_discount - half_cycle_eur)
        epex_tv = getattr(self, "_epex_terminal_value_per_kwh", 0.0)
        tv_per_kwh = max(base_tv, epex_tv)

        INF = float("-inf")
        dp = [[INF] * num_soc for _ in range(n + 1)]
        action_dp = [["idle"] * num_soc for _ in range(n)]

        for s_idx in range(num_soc):
            stored_kwh = (soc_levels[s_idx] - self._min_soc) / 100 * cap
            dp[n][s_idx] = stored_kwh * tv_per_kwh

        # Backward pass
        for t in range(n - 1, -1, -1):
            h = hourly_data[t]
            price = h["price"]
            grid_frac = h.get("_scn_grid_frac", h["grid_fraction"])

            for si in range(num_soc):
                soc = soc_levels[si]
                best_val = INF
                best_act = "idle"

                val = dp[t + 1][si]
                if val > best_val:
                    best_val = val
                    best_act = "idle"

                # No epsilon tie-breaker needed. The DP naturally prefers
                # solar-hour charging over night charging because grid_fraction
                # is lower during solar hours (e.g. 0.3 vs 1.0), making the
                # effective charge cost much cheaper. The backward pass
                # correctly propagates the value of earlier charging when it
                # enables additional profitable discharge slots.

                # Charge: use >= so that break-even ties prefer charging.
                # The DP value difference Δchg can be exactly equal to charge
                # cost due to floating point (0.0434 vs 0.04334 EUR). With >=,
                # the DP charges at break-even slots. Pass 4 (backward shift)
                # then moves these charge slots to the latest possible position,
                # leaving early hours free for solar.
                if soc < self._max_soc and charge_kwh_slot > 0:
                    delta = min(charge_kwh_slot, (self._max_soc - soc) / 100 * cap)
                    new_soc = soc + delta / cap * 100
                    new_si = soc_to_idx(new_soc)
                    if new_si > si:
                        cost = delta * grid_frac * price + delta * half_cycle_eur
                        val = -cost + dp[t + 1][new_si]
                        if val >= best_val:
                            best_val = val
                            best_act = "charge"

                # Discharge: keep strict > (conservative — don't discharge
                # at break-even, prefer holding energy for uncertain future).
                if soc > self._min_soc and discharge_kwh_slot > 0:
                    delta = min(discharge_kwh_slot, (soc - self._min_soc) / 100 * cap)
                    delivered = delta * efficiency
                    new_soc = soc - delta / cap * 100
                    new_si = soc_to_idx(new_soc)
                    if new_si < si:
                        revenue = delivered * price - delta * half_cycle_eur
                        val = revenue + dp[t + 1][new_si]
                        if val > best_val:
                            best_val = val
                            best_act = "discharge"

                dp[t][si] = best_val
                action_dp[t][si] = best_act

        # Forward pass
        start_si = soc_to_idx(current_soc)
        actions = []
        current_si = start_si
        for t in range(n):
            act = action_dp[t][current_si]
            actions.append(act)
            soc = soc_levels[current_si]

            if act == "charge":
                delta = min(charge_kwh_slot, (self._max_soc - soc) / 100 * cap)
                new_soc = soc + delta / cap * 100
            elif act == "discharge":
                delta = min(discharge_kwh_slot, (soc - self._min_soc) / 100 * cap)
                new_soc = soc - delta / cap * 100
            else:
                new_soc = soc
            current_si = soc_to_idx(new_soc)

        # Profit = DP value − terminal value of starting energy
        start_stored_kwh = (current_soc - self._min_soc) / 100 * cap
        profit = dp[0][soc_to_idx(current_soc)] - start_stored_kwh * tv_per_kwh

        return actions, profit

    def _build_slot_data(self, now: datetime) -> tuple[list[dict], float]:
        """Build list of time slots from price forecast.

        Supports both hourly and sub-hourly (e.g. 15-min) price data.
        Returns (slots, slot_duration_hours).
        """
        now_str = now.strftime("%Y-%m-%dT%H:%M")
        slots = []

        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start.tzinfo is not None:
                    start = dt_util.as_local(start)
                slot_key = start.strftime("%Y-%m-%dT%H:%M")
                if slot_key >= now_str:
                    slots.append({
                        "slot_key": slot_key,
                        "hour_key": start.strftime("%Y-%m-%dT%H"),
                        "hour_of_day": start.hour,
                        "price": p.get("total", 0),
                        "start_dt": start,
                    })
            except (ValueError, TypeError):
                continue

        # Deduplicate (keep first occurrence)
        seen = set()
        unique = []
        for s in slots:
            if s["slot_key"] not in seen:
                seen.add(s["slot_key"])
                unique.append(s)

        # Detect slot duration from data (15min, 30min, or 60min)
        slot_duration_h = 1.0
        if len(unique) >= 2:
            dt1 = unique[0]["start_dt"]
            dt2 = unique[1]["start_dt"]
            delta_min = (dt2 - dt1).total_seconds() / 60
            if 10 <= delta_min <= 20:
                slot_duration_h = 0.25  # 15 min
            elif 25 <= delta_min <= 35:
                slot_duration_h = 0.5   # 30 min
            # else: default 1h

        # Log multi-day coverage
        if unique:
            first_dt = unique[0]["start_dt"]
            last_dt = unique[-1]["start_dt"]
            hours_covered = (last_dt - first_dt).total_seconds() / 3600
            days_covered = hours_covered / 24
            _LOGGER.debug(
                "Price slots: %d entries, %.0f min resolution, %.0fh coverage (%.1f days)",
                len(unique), slot_duration_h * 60, hours_covered, days_covered,
            )
        else:
            _LOGGER.debug("Price slots: 0 entries")

        return unique, slot_duration_h

    def _get_current_plan_action(self) -> str | None:
        """Get the planned action for the current time slot.

        Detects slot duration from the plan, rounds current time down to
        the nearest slot boundary, and does a simple string match.
        """
        if not self._battery_plan:
            return None

        now = dt_util.now()

        # Detect slot duration from first two entries
        slot_minutes = 60
        if len(self._battery_plan) >= 2:
            h1 = self._battery_plan[0]["hour"]
            h2 = self._battery_plan[1]["hour"]
            try:
                d1 = datetime.fromisoformat(h1)
                d2 = datetime.fromisoformat(h2)
                diff = int((d2 - d1).total_seconds() / 60)
                if 10 <= diff <= 60:
                    slot_minutes = diff
            except (ValueError, TypeError):
                pass

        # Round current time down to slot boundary
        minute = (now.minute // slot_minutes) * slot_minutes
        now_key = now.strftime("%Y-%m-%dT%H:") + f"{minute:02d}"

        # Simple string match
        for entry in self._battery_plan:
            if entry["hour"] == now_key:
                return entry["action"]

        # Fallback: match by hour only (for hourly plans without :MM)
        now_hour = now.strftime("%Y-%m-%dT%H")
        for entry in self._battery_plan:
            if entry["hour"].startswith(now_hour):
                return entry["action"]

        return None

    def _find_cheap_hours(self, count: int = 4) -> list[dict]:
        """Find the cheapest upcoming hours from the forecast."""
        now = dt_util.now()
        future_prices = []
        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start >= now:
                    future_prices.append(p)
            except (ValueError, TypeError):
                continue

        future_prices.sort(key=lambda x: x.get("total", 999))
        return future_prices[:count]

    def _find_expensive_hours(self, count: int = 4) -> list[dict]:
        """Find the most expensive upcoming hours from the forecast."""
        now = dt_util.now()
        future_prices = []
        for p in self._price_forecast:
            try:
                start = datetime.fromisoformat(p["start"])
                if start >= now:
                    future_prices.append(p)
            except (ValueError, TypeError):
                continue

        future_prices.sort(key=lambda x: x.get("total", 0), reverse=True)
        return future_prices[:count]

    def _is_in_cheap_window(self) -> bool:
        """Check if current time falls within a cheap price window."""
        if self._current_price is None:
            return False

        # Dynamic threshold: use the lower third of available prices
        if self._price_forecast:
            prices = [p.get("total", 0) for p in self._price_forecast]
            if prices:
                sorted_prices = sorted(prices)
                threshold_idx = len(sorted_prices) // 3
                dynamic_threshold = sorted_prices[threshold_idx]
                return self._current_price <= dynamic_threshold

        # Fallback: use summary price range from entity attributes
        if self._fallback_price_range:
            price_range = self._fallback_price_range
            # "Cheap" = in the lower third of today's price range
            low_third = price_range["min"] + (price_range["max"] - price_range["min"]) / 3
            return self._current_price <= low_third

        return self._current_price <= self._price_low / 100  # convert ct to EUR

    def _is_in_expensive_window(self) -> bool:
        """Check if current time falls within an expensive price window."""
        if self._current_price is None:
            return False

        # Dynamic threshold: use the upper third of available prices
        if self._price_forecast:
            prices = [p.get("total", 0) for p in self._price_forecast]
            if prices:
                sorted_prices = sorted(prices)
                threshold_idx = (len(sorted_prices) * 2) // 3
                dynamic_threshold = sorted_prices[threshold_idx]
                return self._current_price >= dynamic_threshold

        # Fallback: use summary price range from entity attributes
        if self._fallback_price_range:
            price_range = self._fallback_price_range
            # "Expensive" = in the upper third of today's price range
            high_third = price_range["min"] + (price_range["max"] - price_range["min"]) * 2 / 3
            return self._current_price >= high_third

        return self._current_price >= self._price_high / 100  # convert ct to EUR

    async def _run_price_optimization(self) -> None:
        """Main price optimization logic using the battery plan.

        If a plan exists, follow it. Otherwise fall back to simple
        threshold-based logic.
        """
        if self._battery_soc is None:
            _LOGGER.debug("Missing SOC data, staying idle")
            await self._set_mode_idle()
            return

        # Use plan-based decisions if a plan is available
        # Solar actions don't need price data, so check plan first
        planned_action = self._get_current_plan_action()
        if planned_action:
            await self._execute_plan_action(planned_action)
            return

        # Fallback: simple threshold-based logic (needs price data)
        if self._current_price is None:
            _LOGGER.debug("No plan and no price data, staying idle")
            await self._set_mode_idle()
            return

        is_cheap = self._is_in_cheap_window()
        is_expensive = self._is_in_expensive_window()

        if is_cheap and self._battery_soc < self._max_soc and self._allow_grid_charging:
            await self._start_charging()
        elif is_expensive and self._battery_soc > self._min_soc and self._allow_discharging:
            await self._start_discharging()
        else:
            await self._set_mode_idle()

    async def _execute_plan_action(self, action: str) -> None:
        """Execute the action from the battery plan for the current hour."""
        if action == "charge" and self._battery_soc < self._max_soc:
            if not self._allow_grid_charging:
                _LOGGER.debug("Plan action: CHARGE skipped (grid charging disabled)")
                await self._set_mode_idle()
                return
            _LOGGER.debug("Plan action: CHARGE (grid)")
            await self._start_charging()
        elif action == "discharge" and self._battery_soc > self._min_soc:
            if not self._allow_discharging:
                _LOGGER.debug("Plan action: DISCHARGE skipped (discharging disabled)")
                await self._set_mode_idle()
                return
            _LOGGER.debug("Plan action: DISCHARGE")
            await self._start_discharging()
        elif action == "solar_charge":
            # AC-coupled system: solar surplus flows through the house network
            # and needs the chargers to be ON to charge the battery.
            # Solar charging is allowed even above max_soc (free energy).
            true_surplus = self._calculate_true_solar_surplus()
            if (
                true_surplus is not None
                and true_surplus > 50
            ):
                _LOGGER.debug(
                    "Plan action: SOLAR_CHARGE - charging from solar surplus "
                    "(grid_power=%.0fW, true_surplus=%.0fW)",
                    self._grid_power, true_surplus,
                )
                await self._start_solar_charging(true_surplus)
            elif (
                self._allow_discharging
                and self._grid_power is not None
                and self._grid_power > 50
                and not any(c["active"] for c in self._chargers)
                and self._battery_soc > self._min_soc
            ):
                _LOGGER.debug("Plan action: SOLAR_CHARGE - discharging to cover grid import")
                await self._start_discharging()
            else:
                _LOGGER.debug("Plan action: SOLAR_CHARGE - idle (no surplus/full)")
                await self._set_mode_idle()
        elif action in ("hold", "idle"):
            # Even during hold/idle: capture free solar surplus if available
            if await self._try_solar_opportunistic():
                _LOGGER.debug(
                    "Plan action: %s - but charging from solar surplus",
                    action.upper(),
                )
            else:
                _LOGGER.debug("Plan action: %s", action.upper())
                await self._set_mode_idle()
        else:
            if not await self._try_solar_opportunistic():
                _LOGGER.debug("Plan action: IDLE (unknown action: %s)", action)
                await self._set_mode_idle()

    async def _try_solar_opportunistic(self) -> bool:
        """Check for solar surplus and charge opportunistically.

        Called during hold/idle plan actions to capture free solar energy
        that would otherwise be exported.

        Above max_soc: still captures pure solar surplus (free energy)
        but does NOT use inverter-deficit mode (that draws grid power).
        """
        true_surplus = self._calculate_true_solar_surplus()
        if true_surplus is None or true_surplus <= 50:
            return False

        above_max_soc = (
            self._battery_soc is not None and self._battery_soc >= self._max_soc
        )

        if above_max_soc:
            # Above max_soc: only charge from PURE solar surplus
            # (charger power must be covered by solar alone, no grid/inverter)
            min_charger = min(
                (c["power"] for c in self._chargers if c["power"] > 0),
                default=0,
            )
            if true_surplus >= min_charger * 0.8:
                _LOGGER.debug(
                    "Solar charge above max_soc: surplus=%.0fW (pure solar)",
                    true_surplus,
                )
                await self._start_solar_charging(true_surplus)
                return True
            # Not enough surplus for a charger without grid → skip
            return False

        _LOGGER.debug(
            "Opportunistic solar charge: surplus=%.0fW (grid=%.0fW)",
            true_surplus, self._grid_power or 0,
        )
        await self._start_solar_charging(true_surplus)
        return True

    async def _run_self_consumption(self) -> None:
        """Self-consumption optimization.

        Always try to cover house demand from battery, charge when
        there is excess solar/grid production.
        """
        if self._battery_soc is None or self._grid_power is None:
            await self._set_mode_idle()
            return

        if self._grid_power > 50 and self._battery_soc > self._min_soc and self._allow_discharging:
            # House is importing from grid - discharge battery to cover it
            await self._start_discharging()
        elif self._grid_power < -50 and self._battery_soc < self._max_soc:
            # Excess production (exporting) - charge battery (solar, not grid)
            await self._start_charging()
        else:
            await self._set_mode_idle()

    async def _start_charging(self) -> None:
        """Activate chargers to charge the battery."""
        if self._operating_mode == MODE_CHARGING:
            return

        _LOGGER.info(
            "Starting battery charge (SOC: %.1f%%, Price: %.4f EUR/kWh)",
            self._battery_soc or 0,
            self._current_price or 0,
        )

        # Turn on all chargers
        for charger in self._chargers:
            if charger["switch"]:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": charger["switch"]}
                )
                charger["active"] = True

        # Turn off inverter feed and reset PID
        self._reset_pid()
        if self._inverter_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._inverter_switch}
            )
        await self._set_inverter_power(0)
        self._inverter_active = False

        self._operating_mode = MODE_CHARGING

    def _calculate_true_solar_surplus(self) -> float | None:
        """Calculate the true solar surplus, compensating for active charger draw.

        grid_power reflects what the meter sees *after* chargers are already
        drawing.  To know the real solar surplus we must add back the power
        that active chargers are consuming, and subtract any inverter feed.

        Example: grid_power = -200W, charger1 (500W) active
          → true surplus = 500 - (-200) = 700W
        """
        if self._grid_power is None:
            return None

        active_draw = sum(c["power"] for c in self._chargers if c["active"])

        # Inverter feeds power INTO the house network, reducing grid import.
        # Subtract it to get the pure solar contribution.
        inverter_feed = 0
        if self._inverter_active and self._inverter_target_power > 0:
            inverter_feed = self._inverter_target_power

        # true_surplus = charger_draw + inverter_feed - grid_power
        # (grid_power negative = export, so subtracting it adds the export)
        return active_draw + inverter_feed - self._grid_power

    async def _start_solar_charging(self, surplus_w: float) -> None:
        """Activate chargers proportionally to available solar surplus.

        Greedily adds chargers sorted by power (largest first) until the
        surplus is used up.  If no single charger fits, the smallest charger
        is used with the inverter covering the deficit.
        """
        if not self._chargers:
            await self._set_mode_idle()
            return

        # Sort chargers by power descending for greedy packing
        indexed = [(i, c) for i, c in enumerate(self._chargers) if c["power"] > 0]
        indexed.sort(key=lambda x: x[1]["power"], reverse=True)

        # Greedily select chargers that fit within surplus (80% margin)
        selected: set[int] = set()
        remaining = surplus_w
        for idx, charger in indexed:
            if remaining >= charger["power"] * 0.8:
                selected.add(idx)
                remaining -= charger["power"]

        if not selected:
            # No charger fits purely from surplus – try inverter-assisted
            # But NOT above max_soc (inverter draws from battery = grid loop)
            above_max = self._battery_soc is not None and self._battery_soc >= self._max_soc
            smallest = min(indexed, key=lambda x: x[1]["power"])
            smallest_idx, smallest_charger = smallest
            deficit_w = smallest_charger["power"] - surplus_w
            if surplus_w >= 100 and self._inverter_power_entity and not above_max:
                _LOGGER.debug(
                    "Solar surplus %.0fW < smallest charger (%dW) – "
                    "using inverter to cover deficit %.0fW",
                    surplus_w, smallest_charger["power"], deficit_w,
                )
                await self._apply_charger_states({smallest_idx})
                await self._start_inverter_deficit(deficit_w)
                self._operating_mode = MODE_SOLAR_CHARGING
                return

            powers_str = ", ".join(
                f"C{i+1}={c['power']}W" for i, c in indexed
            )
            _LOGGER.debug(
                "Solar surplus %.0fW too low for any charger (%s)",
                surplus_w, powers_str,
            )
            await self._set_mode_idle()
            return

        # Apply selected charger states and turn off inverter
        await self._apply_charger_states(selected)

        if self._inverter_active:
            if self._inverter_switch:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": self._inverter_switch}
                )
            await self._set_inverter_power(0)
            self._inverter_active = False

        self._operating_mode = MODE_SOLAR_CHARGING
        active_str = ", ".join(
            f"C{i+1}={'ON' if i in selected else 'OFF'}({c['power']}W)"
            for i, c in enumerate(self._chargers)
        )
        _LOGGER.info("Solar charging: surplus=%.0fW, %s", surplus_w, active_str)

    async def _apply_charger_states(self, should_be_on: set[int]) -> None:
        """Set each charger on or off, respecting minimum on/off times.

        Hysteresis prevents rapid switching (flapping) by enforcing:
        - Minimum ON time: charger must stay on for at least _charger_min_on_time
        - Minimum OFF time: charger must stay off for at least _charger_min_off_time
        """
        now = dt_util.utcnow()
        for i, charger in enumerate(self._chargers):
            if not charger["switch"]:
                continue
            want_on = i in should_be_on
            last_switch = self._charger_last_switch_time.get(i)

            if want_on and not charger["active"]:
                # Check minimum OFF time before turning on
                if last_switch and (now - last_switch) < self._charger_min_off_time:
                    _LOGGER.debug(
                        "Charger %d: want ON but min off time not elapsed (%.0fs left)",
                        i + 1,
                        (self._charger_min_off_time - (now - last_switch)).total_seconds(),
                    )
                    continue
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": charger["switch"]}
                )
                charger["active"] = True
                self._charger_last_switch_time[i] = now
            elif not want_on and charger["active"]:
                # Check minimum ON time before turning off
                if last_switch and (now - last_switch) < self._charger_min_on_time:
                    _LOGGER.debug(
                        "Charger %d: want OFF but min on time not elapsed (%.0fs left)",
                        i + 1,
                        (self._charger_min_on_time - (now - last_switch)).total_seconds(),
                    )
                    continue
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": charger["switch"]}
                )
                charger["active"] = False
                self._charger_last_switch_time[i] = now

    async def _start_inverter_deficit(self, deficit_w: float) -> None:
        """Turn on inverter to cover a charger deficit."""
        if self._inverter_switch and not self._inverter_active:
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self._inverter_switch}
            )
        self._inverter_active = True
        self._inverter_target_power = deficit_w
        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": round(deficit_w),
            },
        )

    async def _start_discharging(self) -> None:
        """Activate inverter to discharge battery into home network.

        If an inverter power entity is configured, use zero-feed regulation
        to match the inverter output to the current grid import, preventing
        any export back to the grid.
        """
        # Turn off chargers (always, even if already discharging)
        if self._operating_mode != MODE_DISCHARGING:
            _LOGGER.info(
                "Starting battery discharge (SOC: %.1f%%, Price: %.4f EUR/kWh)",
                self._battery_soc or 0,
                self._current_price or 0,
            )

            for charger in self._chargers:
                if charger["switch"]:
                    await self.hass.services.async_call(
                        "switch", "turn_off", {"entity_id": charger["switch"]}
                    )
                    charger["active"] = False

            # Turn on inverter switch if configured (simple on/off mode)
            if self._inverter_switch:
                await self.hass.services.async_call(
                    "switch", "turn_on", {"entity_id": self._inverter_switch}
                )

            self._inverter_active = True
            self._operating_mode = MODE_DISCHARGING

        # Zero-feed regulation: adjust inverter power to match grid import
        if self._inverter_power_entity:
            await self._regulate_zero_feed()

    async def _regulate_zero_feed(self) -> None:
        """PID-regulated zero-feed control for the inverter.

        Uses a PID controller to smoothly adjust inverter output so that
        grid power approaches a small positive target (slight import preferred
        over export).  The PID eliminates the oscillation and overshoot of
        the previous simple additive approach.

        Error = grid_power - setpoint (positive = importing too much)
        P: immediate proportional response
        I: corrects persistent offset (e.g. slow-changing loads)
        D: dampens rapid changes (e.g. cloud edges)
        """
        if self._grid_power is None:
            _LOGGER.debug("No grid power data available for zero-feed regulation")
            return

        max_power = self._inverter_power or 800

        # Asymmetric regulation:
        # - Export (grid < 0): avoid aggressively → immediate correction
        # - Import 0-50W: tolerated, no adjustment needed
        # - Import > 50W: increase inverter to reduce grid draw

        if self._grid_power < 0:
            # EXPORT detected → immediately reduce inverter by export amount
            export_w = abs(self._grid_power)
            new_target = self._inverter_target_power - export_w
            _LOGGER.info(
                "Zero-feed: EXPORT %.0fW → reducing inverter %.0f → %.0fW",
                export_w, self._inverter_target_power, new_target,
            )
            self._pid_integral = 0.0
            self._pid_last_error = None
        elif self._grid_power <= 50:
            # Import 0-50W → within tolerance, no adjustment
            return
        else:
            # Import > 50W → increase inverter to reduce grid draw
            setpoint = 25  # target: 25W import (middle of 0-50W band)
            error = self._grid_power - setpoint

            if error > 100:
                # Fast path for large import
                new_target = self._inverter_target_power + error * 0.9
                _LOGGER.debug(
                    "Zero-feed FAST: import=%.0fW → inverter=%.0fW",
                    self._grid_power, new_target,
                )
                self._pid_integral = 0.0
                self._pid_last_error = error
            else:
                # Fine PID tuning
                p_term = self._pid_kp * error

                self._pid_integral += error
                max_integral = max_power / self._pid_ki if self._pid_ki > 0 else 1000
                self._pid_integral = max(-max_integral, min(max_integral, self._pid_integral))
                i_term = self._pid_ki * self._pid_integral

                d_term = 0.0
                if self._pid_last_error is not None:
                    d_term = self._pid_kd * (error - self._pid_last_error)
                self._pid_last_error = error

                new_target = self._inverter_target_power + p_term + i_term + d_term
                _LOGGER.debug(
                    "Zero-feed PID: grid=%.0fW P=%.0f I=%.0f D=%.0f → %.0fW",
                    self._grid_power, p_term, i_term, d_term, new_target,
                )

        # Clamp between 0 and max power
        new_target = max(0, min(max_power, new_target))

        # Only update if the change is significant (> 10W) to avoid excessive calls
        if abs(new_target - self._inverter_target_power) < 10:
            return

        self._inverter_target_power = new_target

        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": round(new_target),
            },
        )

    def _reset_pid(self) -> None:
        """Reset PID state when switching modes."""
        self._pid_integral = 0.0
        self._pid_last_error = None

    async def _set_mode_idle(self) -> None:
        """Set idle mode - turn off all devices."""
        any_device_on = (
            any(c["active"] for c in self._chargers) or self._inverter_active
        )
        if self._operating_mode == MODE_IDLE and not any_device_on:
            return

        _LOGGER.info("Setting battery to idle mode")
        self._reset_pid()
        await self.stop_all()

    async def stop_all(self) -> None:
        """Turn off all chargers and inverters."""
        for charger in self._chargers:
            if charger["switch"]:
                await self.hass.services.async_call(
                    "switch", "turn_off", {"entity_id": charger["switch"]}
                )
                charger["active"] = False

        if self._inverter_switch:
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self._inverter_switch}
            )
        await self._set_inverter_power(0)
        self._inverter_active = False

        self._operating_mode = MODE_IDLE

    async def _set_inverter_power(self, value: float) -> None:
        """Set inverter power entity to a specific value."""
        if not self._inverter_power_entity:
            return

        value = max(0, round(value))
        if value == round(self._inverter_target_power):
            return

        self._inverter_target_power = value
        domain = self._inverter_power_entity.split(".")[0]
        await self.hass.services.async_call(
            domain,
            "set_value",
            {
                "entity_id": self._inverter_power_entity,
                "value": value,
            },
        )

    async def force_charge(self) -> None:
        """Force battery into charging mode."""
        self._strategy = STRATEGY_MANUAL
        await self._start_charging()

    async def force_discharge(self) -> None:
        """Force battery into discharging mode."""
        self._strategy = STRATEGY_MANUAL
        await self._start_discharging()

    def apply_options(self, options: dict) -> None:
        """Apply updated options from the options flow (live, no restart needed)."""
        self._tibber_price_entity = options.get(
            CONF_TIBBER_PRICE_ENTITY, self._tibber_price_entity
        )
        self._tibber_prices_entity = options.get(
            CONF_TIBBER_PRICES_ENTITY, self._tibber_prices_entity
        )
        self._pulse_consumption_entity = options.get(
            CONF_TIBBER_PULSE_CONSUMPTION_ENTITY, self._pulse_consumption_entity
        )
        self._pulse_production_entity = options.get(
            CONF_TIBBER_PULSE_PRODUCTION_ENTITY, self._pulse_production_entity
        )
        if CONF_CHARGERS in options:
            new_chargers = []
            for c in options[CONF_CHARGERS]:
                new_chargers.append({
                    "switch": c.get("switch", ""),
                    "power": c.get("power", 0),
                    "active": False,
                })
            self._chargers = new_chargers
        self._inverter_switch = options.get(
            CONF_INVERTER_FEED_SWITCH, self._inverter_switch
        )
        self._inverter_power = options.get(
            CONF_INVERTER_FEED_POWER, self._inverter_power
        )
        self._inverter_power_entity = options.get(
            CONF_INVERTER_FEED_POWER_ENTITY, self._inverter_power_entity
        )
        self._inverter_actual_power_entity = options.get(
            CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY, self._inverter_actual_power_entity
        )
        self._battery_soc_entity = options.get(
            CONF_BATTERY_SOC_ENTITY, self._battery_soc_entity
        )
        self._battery_capacity = options.get(
            CONF_BATTERY_CAPACITY_KWH, self._battery_capacity
        )
        self._min_soc = options.get(CONF_MIN_SOC, self._min_soc)
        self._max_soc = options.get(CONF_MAX_SOC, self._max_soc)
        self._price_low = options.get(
            CONF_PRICE_LOW_THRESHOLD, self._price_low
        )
        self._price_high = options.get(
            CONF_PRICE_HIGH_THRESHOLD, self._price_high
        )
        self._solar_forecast_entity = options.get(
            CONF_SOLAR_FORECAST_ENTITY, self._solar_forecast_entity
        )
        self._solar_forecast_entities = options.get(
            CONF_SOLAR_FORECAST_ENTITIES, self._solar_forecast_entities
        )
        self._solar_power_entity = options.get(
            CONF_SOLAR_POWER_ENTITY, self._solar_power_entity
        )
        self._solar_energy_today_entity = options.get(
            CONF_SOLAR_ENERGY_TODAY_ENTITY, self._solar_energy_today_entity
        )
        self._house_consumption_w = options.get(
            CONF_HOUSE_CONSUMPTION_W, self._house_consumption_w
        )
        self._cycle_cost = options.get(
            CONF_BATTERY_CYCLE_COST, self._cycle_cost
        )
        eff_pct = options.get(CONF_BATTERY_EFFICIENCY)
        if eff_pct is not None:
            self._battery_efficiency = eff_pct / 100.0
        self._epex_enabled = bool(options.get(
            CONF_EPEX_PREDICTOR_ENABLED, self._epex_enabled
        ))
        self._epex_region = options.get(
            CONF_EPEX_PREDICTOR_REGION, self._epex_region
        )
        self._outside_temp_entity = options.get(
            CONF_OUTSIDE_TEMPERATURE_ENTITY, self._outside_temp_entity
        )
        self._battery_voltage_entity = options.get(
            CONF_BATTERY_VOLTAGE_ENTITY, self._battery_voltage_entity
        )
        self._battery_current_entity = options.get(
            CONF_BATTERY_CURRENT_ENTITY, self._battery_current_entity
        )
        _LOGGER.info("Configuration updated from options flow")

    def set_strategy(self, strategy: str) -> None:
        """Set the operating strategy."""
        if strategy in (STRATEGY_PRICE_OPTIMIZED, STRATEGY_SELF_CONSUMPTION, STRATEGY_MANUAL):
            self._strategy = strategy
            _LOGGER.info("Strategy changed to: %s", strategy)

    def stop(self) -> None:
        """Stop the coordinator and remove listeners."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

    def _build_data(self) -> dict[str, Any]:
        """Build the data dict exposed to entities."""
        cheap_hours = self._find_cheap_hours(3)
        expensive_hours = self._find_expensive_hours(3)

        return {
            ATTR_CURRENT_PRICE: self._current_price,
            ATTR_OPERATING_MODE: self._operating_mode,
            ATTR_STRATEGY: self._strategy,
            ATTR_ESTIMATED_SAVINGS: self._estimated_savings,
            ATTR_NEXT_CHEAP_WINDOW: (
                cheap_hours[0]["start"] if cheap_hours else None
            ),
            ATTR_NEXT_EXPENSIVE_WINDOW: (
                expensive_hours[0]["start"] if expensive_hours else None
            ),
            ATTR_BATTERY_PLAN: self._battery_plan,
            ATTR_PLAN_SUMMARY: self._plan_summary,
            ATTR_EXPECTED_SOLAR_KWH: self._expected_solar_kwh,
            "planned_action": self._get_current_plan_action(),
            "battery_soc": self._battery_soc,
            "grid_power": self._grid_power,
            "chargers": [
                {"index": i, "switch": c["switch"], "power": c["power"], "active": c["active"]}
                for i, c in enumerate(self._chargers)
            ],
            "inverter_active": self._inverter_active,
            "inverter_target_power": self._inverter_target_power,
            "inverter_actual_power": self._inverter_actual_power,
            "price_forecast": self._price_forecast,
            "cheap_hours": cheap_hours,
            "expensive_hours": expensive_hours,
            "min_soc": self._min_soc,
            "max_soc": self._max_soc,
            "battery_capacity_kwh": self._battery_capacity,
            "allow_grid_charging": self._allow_grid_charging,
            "allow_discharging": self._allow_discharging,
            "use_solar_forecast": self._use_solar_forecast,
            "solar_power": self._solar_power,
            "solar_calibration_factor": self._solar_calibration_factor,
            "intraday_solar_factor": self._intraday_solar_factor,
            "kalman_gain": round(getattr(self, "_kalman_p", 0.1) / (getattr(self, "_kalman_p", 0.1) + 0.05), 3),
            "battery_efficiency": round(self._battery_efficiency * 100),
            "cycle_cost_ct": self._cycle_cost,
            "consumption_forecast": self.get_hourly_consumption_forecast(),
            "optimization_log": list(self._optimization_log),
            "epex_markup": self._epex_markup,
            "epex_terminal_value_ct": round(
                getattr(self, "_epex_terminal_value_per_kwh", 0) * 100, 1
            ),
            "epex_visualization": getattr(self, "_epex_visualization", []),
            "outside_temperature": self._outside_temp,
            "battery_real_power_w": round(self._battery_real_power, 1) if self._battery_real_power else None,
            "battery_voltage": round(self._battery_voltage, 2) if self._battery_voltage else None,
            "battery_current": round(self._battery_current, 2) if self._battery_current else None,
        }

    # Properties for entities
    @property
    def chargers(self) -> list[dict]:
        return self._chargers

    @property
    def strategy(self) -> str:
        return self._strategy

    @property
    def operating_mode(self) -> str:
        return self._operating_mode

    @property
    def current_price(self) -> float | None:
        return self._current_price

    @property
    def battery_soc(self) -> float | None:
        return self._battery_soc

    @property
    def grid_power(self) -> float | None:
        return self._grid_power

    @property
    def min_soc(self) -> int:
        return self._min_soc

    @min_soc.setter
    def min_soc(self, value: int) -> None:
        self._min_soc = max(0, min(100, value))

    @property
    def max_soc(self) -> int:
        return self._max_soc

    @max_soc.setter
    def max_soc(self, value: int) -> None:
        self._max_soc = max(0, min(100, value))

    @property
    def price_low_threshold(self) -> float:
        return self._price_low

    @price_low_threshold.setter
    def price_low_threshold(self, value: float) -> None:
        self._price_low = value

    @property
    def price_high_threshold(self) -> float:
        return self._price_high

    @price_high_threshold.setter
    def price_high_threshold(self, value: float) -> None:
        self._price_high = value

    @property
    def allow_grid_charging(self) -> bool:
        return self._allow_grid_charging

    @allow_grid_charging.setter
    def allow_grid_charging(self, value: bool) -> None:
        self._allow_grid_charging = value
        _LOGGER.info("Grid charging %s", "enabled" if value else "disabled")

    @property
    def allow_discharging(self) -> bool:
        return self._allow_discharging

    @allow_discharging.setter
    def allow_discharging(self, value: bool) -> None:
        self._allow_discharging = value
        _LOGGER.info("Discharging %s", "enabled" if value else "disabled")

    @property
    def use_solar_forecast(self) -> bool:
        return self._use_solar_forecast

    @use_solar_forecast.setter
    def use_solar_forecast(self, value: bool) -> None:
        self._use_solar_forecast = value
        _LOGGER.info("Solar forecast %s", "enabled" if value else "disabled")
