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
    CONF_BATTERY_SOC_ENTITY,
    CONF_CHARGERS,
    CONSUMPTION_STATS_ROLLING_DAYS,
    STORAGE_KEY_CONSUMPTION,
    STORAGE_VERSION_CONSUMPTION,
    CONF_HOUSE_CONSUMPTION_W,
    CONF_INVERTER_FEED_ACTUAL_POWER_ENTITY,
    CONF_INVERTER_FEED_POWER,
    CONF_INVERTER_FEED_POWER_ENTITY,
    CONF_INVERTER_FEED_SWITCH,
    CONF_MAX_SOC,
    CONF_MIN_SOC,
    CONF_PRICE_HIGH_THRESHOLD,
    CONF_PRICE_LOW_THRESHOLD,
    CONF_SOLAR_FORECAST_ENTITIES,
    CONF_SOLAR_FORECAST_ENTITY,
    CONF_SOLAR_POWER_ENTITY,
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
        self._house_consumption_w = self._config.get(
            CONF_HOUSE_CONSUMPTION_W, DEFAULT_HOUSE_CONSUMPTION_W
        )

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

        # Consumption statistics: rolling average per hour-of-day
        # Format: {"0": [w1, w2, ...], "1": [...], ..., "23": [...]}
        self._consumption_store = Store(
            hass,
            STORAGE_VERSION_CONSUMPTION,
            f"{DOMAIN}.{entry.entry_id}.{STORAGE_KEY_CONSUMPTION}",
        )
        self._consumption_stats: dict[str, list[float]] = {}
        self._consumption_hourly_samples: list[float] = []  # samples within current hour
        self._consumption_last_hour: int | None = None
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
        """Load consumption statistics from persistent storage."""
        if self._consumption_loaded:
            return
        data = await self._consumption_store.async_load()
        if data and isinstance(data, dict):
            self._consumption_stats = data
            total = sum(len(v) for v in self._consumption_stats.values())
            _LOGGER.debug("Loaded consumption stats: %d entries across %d hours",
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

        # When the hour changes, store the average for the previous hour
        if self._consumption_last_hour is not None and current_hour != self._consumption_last_hour:
            if self._consumption_hourly_samples:
                avg_w = sum(self._consumption_hourly_samples) / len(self._consumption_hourly_samples)
                hour_key = str(self._consumption_last_hour)

                if hour_key not in self._consumption_stats:
                    self._consumption_stats[hour_key] = []

                self._consumption_stats[hour_key].append(round(avg_w, 1))

                # Keep only the last N days
                max_entries = CONSUMPTION_STATS_ROLLING_DAYS
                if len(self._consumption_stats[hour_key]) > max_entries:
                    self._consumption_stats[hour_key] = \
                        self._consumption_stats[hour_key][-max_entries:]

                _LOGGER.debug(
                    "Consumption stats: hour %s avg %.0fW "
                    "(%d samples, %d days stored)",
                    hour_key, avg_w,
                    len(self._consumption_hourly_samples),
                    len(self._consumption_stats[hour_key]),
                )

                # Persist to disk
                await self._consumption_store.async_save(self._consumption_stats)

            self._consumption_hourly_samples = []

        self._consumption_last_hour = current_hour

    def get_hourly_consumption_forecast(self) -> dict[int, float]:
        """Get predicted consumption per hour-of-day (0-23) in watts.

        Returns a dict mapping hour (int) → average consumption (W).
        Falls back to configured house_consumption_w for hours without data.
        """
        forecast: dict[int, float] = {}
        for hour in range(24):
            samples = self._consumption_stats.get(str(hour), [])
            if samples:
                forecast[hour] = sum(samples) / len(samples)
            else:
                forecast[hour] = self._house_consumption_w
        return forecast

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
        self._read_sensor_states()
        self._validate_operating_mode()
        await self._check_tibber_watchdog()
        await self._record_consumption()
        await self._update_price_forecast()
        await self._read_solar_forecast()
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
        """Create a cost-optimized 24h battery plan using effective-price arbitrage.

        For each hour, computes the **effective charge cost**: the portion of
        charger power that must come from the grid (after solar surplus is
        subtracted) times the grid price.  This means a solar hour with cheap
        grid prices can be preferred over a non-solar hour with expensive
        prices for grid-charging, because the effective cost is lower.

        Algorithm:
        1. Compute effective charge cost per hour (solar reduces grid draw)
        2. All hours participate in arbitrage (no solar carve-out)
        3. Greedily pair cheapest effective-charge hours with most expensive
           discharge hours
        4. Mark hours with solar surplus that aren't charging as solar_charge
        5. Forward-simulate SOC to validate all actions
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

        # Per-hour consumption forecast (rolling average or fallback)
        consumption_forecast = self.get_hourly_consumption_forecast()

        # Enrich slot data with solar info and effective charge cost
        # Solar forecast is hourly → split evenly across sub-hour slots
        slots_per_hour = max(1, round(1.0 / slot_h))
        for h in slot_data:
            hour_of_day = h.get("hour_of_day", 12)
            h["house_w"] = consumption_forecast.get(hour_of_day, self._house_consumption_w)
            house_kwh_slot = h["house_w"] / 1000 * slot_h

            # Solar: hourly forecast divided by slots per hour
            solar_wh_hour = self._solar_forecast.get(h["hour_key"], 0)
            h["solar_kwh"] = (solar_wh_hour / 1000) / slots_per_hour
            h["solar_wh_hour_raw"] = solar_wh_hour  # for debugging
            h["solar_surplus_kwh"] = max(0, h["solar_kwh"] - house_kwh_slot)

            if charge_kwh_slot > 0:
                grid_fraction = max(0, charge_kwh_slot - h["solar_surplus_kwh"]) / charge_kwh_slot
            else:
                grid_fraction = 1.0
            h["effective_charge_cost"] = grid_fraction * h["price"]
            h["grid_fraction"] = grid_fraction

        n = len(slot_data)
        # Alias for readability in the rest of the method
        hourly_data = slot_data
        actions = ["idle"] * n
        headroom_kwh = (self._max_soc - current_soc) / 100 * cap

        # Step 1: Calculate total expected solar and reserve headroom
        # Solar is free → always prioritize it. Only grid-charge the
        # portion of headroom that solar can't fill.
        total_solar_kwh = 0.0
        solar_hours_end_idx = -1  # last hour with significant solar
        for i, h in enumerate(hourly_data):
            solar_charge = min(h["solar_surplus_kwh"], charge_kwh_slot)
            if solar_charge > 0.05:
                total_solar_kwh += solar_charge
                solar_hours_end_idx = i

        solar_fills_kwh = min(total_solar_kwh, headroom_kwh)
        grid_headroom_kwh = max(0, headroom_kwh - solar_fills_kwh)

        # Calculate how much solar would be wasted (more solar than headroom)
        solar_wasted_kwh = max(0, total_solar_kwh - headroom_kwh)

        # Pre-solar discharge: if solar exceeds headroom, discharge BEFORE
        # solar hours to create room.  Pick the most expensive pre-solar hours.
        presolar_discharge_hours: set[int] = set()
        if solar_wasted_kwh > 0.1 and discharge_kwh_slot > 0:
            # Find the first solar hour
            first_solar_idx = next(
                (i for i, h in enumerate(hourly_data) if h["solar_surplus_kwh"] > 0.05),
                n,
            )
            # Candidate hours: before first solar, sorted by price (most expensive first)
            presolar_cands = [
                (i, hourly_data[i]["price"])
                for i in range(first_solar_idx)
                if hourly_data[i]["solar_surplus_kwh"] < 0.05
            ]
            presolar_cands.sort(key=lambda x: x[1], reverse=True)

            discharge_needed = solar_wasted_kwh
            for idx, price in presolar_cands:
                if discharge_needed <= 0:
                    break
                # Only discharge if stored energy is available
                presolar_discharge_hours.add(idx)
                discharge_needed -= discharge_kwh_slot
                actions[idx] = "discharge"

            if presolar_discharge_hours:
                # Recalculate headroom after pre-solar discharge
                extra_headroom = len(presolar_discharge_hours) * discharge_kwh_slot
                headroom_kwh += extra_headroom
                solar_fills_kwh = min(total_solar_kwh, headroom_kwh)
                grid_headroom_kwh = max(0, headroom_kwh - solar_fills_kwh)
                _LOGGER.info(
                    "Pre-solar discharge: %d hours to free %.1f kWh for solar "
                    "(was wasting %.1f kWh)",
                    len(presolar_discharge_hours), extra_headroom, solar_wasted_kwh,
                )

        _LOGGER.debug(
            "Plan budget: headroom=%.1f kWh, solar=%.1f kWh, "
            "grid_needed=%.1f kWh, pre-solar_discharge=%d hours",
            headroom_kwh, solar_fills_kwh, grid_headroom_kwh,
            len(presolar_discharge_hours),
        )

        # Step 2: Find profitable arbitrage pairs
        # ALL hours are charge candidates (solar hours have low effective cost).
        # This ensures solar-assisted hours at cheap grid prices are preferred
        # over expensive non-solar hours.
        charge_cands = []   # (index, sort_cost)
        discharge_cands = []  # (index, price)
        for i, h in enumerate(hourly_data):
            if i in presolar_discharge_hours:
                continue  # already assigned
            # Charge candidate: effective cost (solar reduces it)
            charge_cands.append((i, h["effective_charge_cost"]))
            # Discharge candidate: only hours with low solar surplus
            if h["solar_surplus_kwh"] < (h["house_w"] / 1000) * 0.3:
                discharge_cands.append((i, h["price"]))

        charge_cands.sort(key=lambda x: x[1])
        discharge_cands.sort(key=lambda x: x[1], reverse=True)

        # Find profitable arbitrage pairs.
        # The SOC forward simulation (Step 4) validates actual feasibility,
        # so we don't limit pairs here – just require minimum spread.
        # This ensures existing stored energy can be discharged at expensive
        # hours and recharged at cheap hours, even when solar fills headroom.
        min_spread = 0.02
        used = set()
        pairs = []
        ci, di = 0, 0
        # Max pairs: limited by battery capacity (discharge + recharge cycles)
        max_pairs = int(
            (self._max_soc - self._min_soc) / 100 * cap / charge_kwh_slot
        ) if charge_kwh_slot > 0 else 50
        while ci < len(charge_cands) and di < len(discharge_cands):
            if len(pairs) >= max_pairs:
                break

            c_idx, c_eff_cost = charge_cands[ci]
            d_idx, d_price = discharge_cands[di]

            if d_price - c_eff_cost < min_spread:
                break

            if c_idx in used or d_idx in used or c_idx == d_idx:
                if c_idx in used:
                    ci += 1
                else:
                    di += 1
                continue

            pairs.append((c_idx, d_idx, d_price - c_eff_cost))
            used.add(c_idx)
            used.add(d_idx)
            ci += 1
            di += 1

        for c_idx, d_idx, _ in pairs:
            actions[c_idx] = "charge"
            actions[d_idx] = "discharge"

        # Step 3: Remaining solar hours (not picked for grid-charge) → solar_charge
        for i, h in enumerate(hourly_data):
            if actions[i] == "idle" and h["solar_surplus_kwh"] > 0.05:
                actions[i] = "solar_charge"

        # Step 3b: Optimize charge slot timing
        # The SOC simulation runs chronologically, so expensive early charge
        # slots fill the battery before cheaper later slots get a chance.
        # Fix: count how many charge slots the battery actually needs, keep
        # only the cheapest ones, and drop the rest (they become solar_charge
        # or idle).
        charge_indices = [i for i in range(n) if actions[i] == "charge"]
        if charge_indices and charge_kwh_slot > 0:
            # Quick SOC simulation to count how many charge slots actually
            # charge the battery (before it hits max_soc). This tells us
            # exactly how many slots are needed.
            sim_soc = current_soc
            slots_that_charged = 0
            for i in range(n):
                a = actions[i]
                if a == "charge":
                    if sim_soc < self._max_soc:
                        delta = min(charge_kwh_slot, (self._max_soc - sim_soc) / 100 * cap)
                        sim_soc += delta / cap * 100
                        slots_that_charged += 1
                elif a == "discharge":
                    delta = min(discharge_kwh_slot, (sim_soc - self._min_soc) / 100 * cap)
                    sim_soc -= delta / cap * 100
                elif a == "solar_charge":
                    delta = min(hourly_data[i]["solar_surplus_kwh"], charge_kwh_slot)
                    sim_soc += delta / cap * 100
                sim_soc = max(self._min_soc, min(self._max_soc, sim_soc))

            # If we have more charge slots than actually used, drop the expensive ones
            if len(charge_indices) > slots_that_charged:
                charge_with_cost = [
                    (i, hourly_data[i]["effective_charge_cost"]) for i in charge_indices
                ]
                charge_with_cost.sort(key=lambda x: x[1])
                keep = set(idx for idx, _ in charge_with_cost[:slots_that_charged])
                dropped = 0
                for idx in charge_indices:
                    if idx not in keep:
                        if hourly_data[idx]["solar_surplus_kwh"] > 0.05:
                            actions[idx] = "solar_charge"
                        else:
                            actions[idx] = "idle"
                        dropped += 1
                if dropped:
                    _LOGGER.info(
                        "Charge optimization: dropped %d expensive slots, "
                        "kept %d cheapest (of %d total)",
                        dropped, slots_that_charged, len(charge_indices),
                    )

        # Step 4: Forward-simulate SOC to validate and build plan
        self._battery_plan = []
        estimated_soc = current_soc
        charge_count = 0
        discharge_count = 0
        solar_count = 0
        grid_charge_kwh = 0.0

        for i, h in enumerate(hourly_data):
            action = actions[i]
            delta_kwh = 0.0

            if action == "solar_charge":
                delta_kwh = min(h["solar_surplus_kwh"], charge_kwh_slot)
                if estimated_soc + delta_kwh / cap * 100 > self._max_soc:
                    delta_kwh = max(0, (self._max_soc - estimated_soc) / 100 * cap)
                    if delta_kwh < 0.05:
                        action = "idle"
            elif action == "charge":
                if estimated_soc >= self._max_soc:
                    action = "idle"
                else:
                    delta_kwh = min(charge_kwh_slot, (self._max_soc - estimated_soc) / 100 * cap)
                    # Only grid portion counts as grid charge
                    grid_charge_kwh += delta_kwh * h["grid_fraction"]
            elif action == "discharge":
                if estimated_soc <= self._min_soc:
                    action = "idle"
                else:
                    delta_kwh = min(discharge_kwh_slot, (estimated_soc - self._min_soc) / 100 * cap)

            if action in ("solar_charge", "charge"):
                estimated_soc += delta_kwh / cap * 100
                if action == "solar_charge":
                    solar_count += 1
                else:
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
            if action == "charge" and h["solar_surplus_kwh"] > 0.05:
                grid_pct = round(h["grid_fraction"] * 100)
                reason = (
                    f"Solar+Netz ({grid_pct}% Netz à {h['price']*100:.1f} ct "
                    f"→ eff. {h['effective_charge_cost']*100:.1f} ct/kWh)"
                )
            elif action == "charge":
                reason = f"Netz-Laden ({h['price']*100:.1f} ct/kWh)"
            elif action == "solar_charge":
                reason = f"Solar {h['solar_surplus_kwh']:.1f} kWh (kostenlos)"
            elif action == "discharge":
                if i in presolar_discharge_hours:
                    reason = (
                        f"Platz für Solar schaffen "
                        f"({h['price']*100:.1f} ct/kWh)"
                    )
                else:
                    # Find the paired charge hour for context
                    paired_charge = None
                    for c_idx, d_idx, spread in pairs:
                        if d_idx == i:
                            ch = hourly_data[c_idx]
                            paired_charge = ch
                            break
                    if paired_charge:
                        reason = (
                            f"Entladen ({h['price']*100:.1f} ct, "
                            f"Spread {(h['price'] - paired_charge['effective_charge_cost'])*100:.0f} ct)"
                        )
                    else:
                        reason = f"Entladen ({h['price']*100:.1f} ct/kWh)"
            elif action == "hold":
                reason = "Halten für teure Stunden"
            else:
                reason = "Keine Aktion"

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
            """Format slot count as human-readable duration."""
            total_min = round(slots * slot_h * 60)
            if total_min >= 60 and total_min % 60 == 0:
                return f"{total_min // 60}h"
            if total_min >= 60:
                return f"{total_min // 60}h{total_min % 60:02d}"
            return f"{total_min}min"

        if solar_count:
            parts.append(f"{_fmt_duration(solar_count)} Solar")
        if charge_count:
            parts.append(f"{_fmt_duration(charge_count)} Laden ({grid_charge_kwh:.1f} kWh)")
        if discharge_count:
            parts.append(f"{_fmt_duration(discharge_count)} Entladen")
        self._plan_summary = " | ".join(parts) if parts else "Kein Plan erstellt"

        savings = sum(
            p[2] * min(charge_kwh_slot, discharge_kwh_slot)
            for p in pairs
        )
        self._estimated_savings = round(savings, 2)

        _LOGGER.debug(
            "Battery plan (optimized): %s | Savings: %.2f EUR "
            "(SOC: %.0f%%, Solar: %.1f kWh, %d pairs)",
            self._plan_summary,
            savings,
            current_soc,
            self._expected_solar_kwh,
            len(pairs),
        )

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

        _LOGGER.debug(
            "Price slots: %d entries, %.0f min resolution",
            len(unique), slot_duration_h * 60,
        )

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
            # grid_power already includes the draw of active chargers, so we
            # must add their power back to get the true solar surplus.
            true_surplus = self._calculate_true_solar_surplus()
            if (
                true_surplus is not None
                and true_surplus > 50
                and self._battery_soc < self._max_soc
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
        that would otherwise be exported.  Returns True if solar charging
        was activated, False if no surplus or battery full.
        """
        if self._battery_soc is not None and self._battery_soc >= self._max_soc:
            return False

        true_surplus = self._calculate_true_solar_surplus()
        if true_surplus is None or true_surplus <= 50:
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
            # Any surplus > 100W is worth capturing (inverter covers the rest)
            smallest = min(indexed, key=lambda x: x[1]["power"])
            smallest_idx, smallest_charger = smallest
            deficit_w = smallest_charger["power"] - surplus_w
            if surplus_w >= 100 and self._inverter_power_entity:
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
        self._house_consumption_w = options.get(
            CONF_HOUSE_CONSUMPTION_W, self._house_consumption_w
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
            "consumption_forecast": self.get_hourly_consumption_forecast(),
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
